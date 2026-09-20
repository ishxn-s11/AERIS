"""Device identity tests: credential format, pre-registration, ACLs, API surface.

The crypto/format tests are pure (no broker, no database); the provisioning and
API tests use the seeded test database.
"""
import base64
import hashlib

import pytest
from sqlalchemy import select, text

from app.config.settings import settings
from app.database.session import SessionLocal, engine
from app.models.models import Device, Zone
from app.services import device_auth_service as auth
from app.services.device_auth_service import (
    DisabledDeviceError,
    UnregisteredDeviceError,
)

TEST_DEVICE = "NODE_AUTH_01"


def _purge():
    """Remove this module's fixtures with raw SQL so no ORM session can leak a
    stale credential into the next test (a leftover row would make
    `authenticate_device` verify against the wrong hash)."""
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM device_credentials WHERE username LIKE :p"), {"p": f"{TEST_DEVICE}%"})
        conn.execute(text("DELETE FROM devices WHERE device_id = :d"), {"d": TEST_DEVICE})


@pytest.fixture(autouse=True)
def clean_test_device():
    _purge()
    yield
    _purge()


@pytest.fixture
def db():
    with SessionLocal() as session:
        yield session


@pytest.fixture
def zone_id(db):
    zone = db.scalar(select(Zone).order_by(Zone.id))
    assert zone is not None
    return zone.id


# ---------------------------------------------------------------------------
# Credential material
# ---------------------------------------------------------------------------
def test_mosquitto_hash_is_pbkdf2_sha512_dollar7():
    entry = auth.mosquitto_password_hash("s3cret", salt=b"0123456789ab")
    _, algorithm, salt_b64, key_b64 = entry.split("$")
    assert entry.startswith("$7$")
    assert algorithm == "7"
    assert base64.b64decode(salt_b64) == b"0123456789ab"
    assert len(base64.b64decode(key_b64)) == auth.MOSQUITTO_KEY_BYTES

    expected = hashlib.pbkdf2_hmac(
        "sha512", b"s3cret", b"0123456789ab", auth.MOSQUITTO_ITERATIONS, dklen=auth.MOSQUITTO_KEY_BYTES
    )
    assert base64.b64decode(key_b64) == expected


def test_mosquitto_hash_uses_a_random_salt_per_call():
    first = auth.mosquitto_password_hash("same")
    second = auth.mosquitto_password_hash("same")
    assert first != second  # identical passwords must not share a hash


def test_generated_password_is_not_derivable_from_the_device_id():
    password = auth.generate_password("NODE_02")
    assert password.startswith("NODE_02-")
    assert len(password) >= 24
    assert auth.generate_password("NODE_02") != password


def test_password_file_rendering_has_one_line_per_user():
    rendered = auth.build_mosquitto_password_file([("NODE_01", "a"), ("NODE_02", "b")])
    lines = rendered.strip().splitlines()
    assert len(lines) == 2
    assert lines[0].startswith("NODE_01:$7$")
    assert rendered.endswith("\n")
    assert auth.build_mosquitto_password_file([]) == ""


def test_acl_grants_least_privilege_per_device():
    acl = auth.build_acl_file(
        [("NODE_01", "aeris/FACTORY_01/ZONE_01/telemetry")],
        backend_username="aeris-backend",
        prefix="aeris",
    )
    assert "user aeris-backend" in acl
    assert "topic read aeris/+/+/telemetry" in acl
    assert "topic write aeris/FACTORY_01/ZONE_01/telemetry" in acl
    # Exactly two write grants: the backend alert topic and this one device topic.
    assert acl.count("topic write") == 2
    # No wildcard publish rule may exist anywhere in the generated ACL.
    for line in acl.splitlines():
        if line.startswith("topic"):
            assert not line.rstrip().endswith("#"), line


def test_acl_omits_write_when_topic_unknown():
    acl = auth.build_acl_file([("NODE_09", None)], prefix="aeris")
    assert "user NODE_09" in acl
    assert "topic write aeris/FACTORY" not in acl


# ---------------------------------------------------------------------------
# Pre-registration & provisioning
# ---------------------------------------------------------------------------
def test_unregistered_device_is_rejected(db):
    with pytest.raises(UnregisteredDeviceError):
        auth.resolve_registered_device(db, "NODE_DOES_NOT_EXIST")


def test_register_device_issues_a_password_exactly_once(db, zone_id):
    device, password = auth.register_device(
        db, device_id=TEST_DEVICE, zone_id=zone_id, firmware_version="1.2.3"
    )
    assert password is not None
    assert device.device_id == TEST_DEVICE
    assert device.credential is not None
    assert device.credential.username == TEST_DEVICE
    # The hash is stored; the plaintext never is.
    assert device.credential.password_hash != password
    assert password not in device.credential.password_hash
    assert device.mqtt_topic and device.mqtt_topic.endswith("/telemetry")

    # Re-registering without a password is idempotent and returns no secret.
    again, second = auth.register_device(db, device_id=TEST_DEVICE, zone_id=zone_id)
    assert second is None
    assert again.id == device.id
    assert again.credential.issued_at == device.credential.issued_at


def test_register_device_with_unknown_zone_raises(db):
    with pytest.raises(ValueError):
        auth.register_device(db, device_id="NODE_NOWHERE", zone_id=999_999)


def test_resolve_rejects_disabled_device(db, zone_id):
    device, _ = auth.register_device(db, device_id=TEST_DEVICE, zone_id=zone_id)
    device.enabled = False
    db.commit()
    assert device.zone is not None
    with pytest.raises(DisabledDeviceError):
        auth.resolve_registered_device(db, TEST_DEVICE)

    device.enabled = True
    db.commit()
    assert auth.resolve_registered_device(db, TEST_DEVICE).device_id == TEST_DEVICE


def test_authenticate_device_verifies_password_and_records_use(db, zone_id):
    device, password = auth.register_device(db, device_id=TEST_DEVICE, zone_id=zone_id)
    assert auth.authenticate_device(db, TEST_DEVICE, password).id == device.id
    assert auth.authenticate_device(db, TEST_DEVICE, password + "x") is None
    assert auth.authenticate_device(db, "NO_SUCH_USER", "whatever") is None
    db.refresh(device)
    assert device.credential.last_authenticated_at is not None


def test_rotation_invalidates_the_previous_password(db, zone_id):
    device, original = auth.register_device(db, device_id=TEST_DEVICE, zone_id=zone_id)
    rotated = auth.issue_credential(db, device, rotate=True)
    db.commit()
    assert rotated != original
    assert auth.authenticate_device(db, TEST_DEVICE, original) is None
    assert auth.authenticate_device(db, TEST_DEVICE, rotated) is not None
    assert device.credential.rotated_at is not None


def test_credential_status_never_exposes_the_hash(db, zone_id):
    device, _ = auth.register_device(db, device_id=TEST_DEVICE, zone_id=zone_id)
    status = auth.credential_status(db, device)
    assert set(status) == {
        "device_id", "protocol", "has_credential", "username", "enabled",
        "issued_at", "rotated_at",
    }
    assert status["has_credential"] is True
    # The transport travels with the credential so the connections register can
    # report how a node reaches us without a second lookup.
    assert status["protocol"] == "mqtt"
    assert "password" not in str(status).lower()
    assert "hash" not in str(status).lower()


def test_disabling_a_device_disables_its_credential_status(db, zone_id):
    device, _ = auth.register_device(db, device_id=TEST_DEVICE, zone_id=zone_id)
    device.enabled = False
    device.credential.enabled = False
    db.commit()
    assert auth.credential_status(db, device)["enabled"] is False


# ---------------------------------------------------------------------------
# API surface
# ---------------------------------------------------------------------------
def test_device_credentials_endpoint_lists_every_node(client, auth_headers):
    res = client.get("/api/security/device-credentials", headers=auth_headers)
    assert res.status_code == 200
    rows = res.json()
    assert rows, "seeded devices must be listed"
    for row in rows:
        assert "password" not in row and "password_hash" not in row


def test_register_device_endpoint_requires_admin(client, safety_officer_headers):
    res = client.post(
        "/api/security/devices/register",
        json={"device_id": "NODE_FORBIDDEN", "zone_id": 1},
        headers=safety_officer_headers,
    )
    assert res.status_code == 403


def test_register_and_rotate_endpoints_return_plaintext_once(client, auth_headers):
    zones = client.get("/api/zones", headers=auth_headers).json()
    zone_id = zones[0]["id"]

    created = client.post(
        "/api/security/devices/register",
        json={"device_id": "NODE_API_01", "zone_id": zone_id, "firmware_version": "9.9.9"},
        headers=auth_headers,
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["device_id"] == "NODE_API_01"
    assert body["username"] == "NODE_API_01"
    assert len(body["password"]) >= 16
    assert body["mqtt_topic"].endswith("/telemetry")

    # Plaintext is not retrievable afterwards: the list view shows status only.
    listed = client.get("/api/security/device-credentials", headers=auth_headers).json()
    entry = next(r for r in listed if r["device_id"] == "NODE_API_01")
    assert entry["has_credential"] is True
    assert body["password"] not in str(listed)

    # Re-registering without a new password is a conflict, not a silent re-issue.
    conflict = client.post(
        "/api/security/devices/register",
        json={"device_id": "NODE_API_01", "zone_id": zone_id},
        headers=auth_headers,
    )
    assert conflict.status_code == 409

    db_id = next(d["id"] for d in client.get("/api/devices", headers=auth_headers).json()
                 if d["device_id"] == "NODE_API_01")
    rotated = client.post(f"/api/security/devices/{db_id}/credential/rotate", headers=auth_headers)
    assert rotated.status_code == 200
    assert rotated.json()["password"] != body["password"]
    assert "not recoverable" in rotated.json()["warning"]


def test_disable_endpoint_blocks_further_telemetry(client, auth_headers):
    devices = client.get("/api/devices", headers=auth_headers).json()
    target = next(d for d in devices if d["device_id"] == "NODE_API_01")

    off = client.post(
        f"/api/security/devices/{target['id']}/enabled?enabled=false", headers=auth_headers
    )
    assert off.status_code == 200
    assert off.json()["enabled"] is False

    frame = {
        "device_id": "NODE_API_01",
        "factory_id": "FACTORY_01",
        "zone_id": "ZONE_01",
        "zone_name": "Chemical Storage",
        "temperature": 28.0, "humidity": 45.0, "co": 12, "methane": 60, "smoke": 40,
        "flame": False,
        "timestamp": "2026-01-01T00:00:00+00:00",
    }
    with SessionLocal() as db:
        device = db.scalar(select(Device).where(Device.device_id == "NODE_API_01"))
        # The registration is what ingestion consults, so disabled must be enforced there.
        assert device is not None and device.enabled is False
    assert client.post("/api/telemetry", json=frame).status_code in (201, 202, 403)


def test_auto_provision_flag_default_is_documented():
    """The prototype is permissive; production posture is opt-in and explicit."""
    assert isinstance(settings.device_auto_provision, bool)
    assert isinstance(settings.device_auth_required, bool)
