"""Device identity and provisioning.

Model:
- A node must exist as a `Device` row before its telemetry is trusted. That is
  the *pre-registration* rule — with `DEVICE_AUTO_PROVISION=false` an unknown
  `NODE_xx` is rejected instead of silently appearing on the dashboard.
- Each device owns one `DeviceCredential` (broker username + bcrypt hash).
  Plaintext is returned exactly once (issue/rotate) and never stored.
- The broker enforces these credentials. Mosquitto cannot query the database,
  so `scripts/sync_mqtt_credentials.py` materialises the credentials into a
  mosquitto password file plus a per-device ACL file.

Why bcrypt here and not the broker's own `$7$` hash: the backend needs to verify
device tokens (HTTP telemetry, registration flows) with the same primitive used
for user passwords. The broker file is generated separately, from the plaintext,
at issue time.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import secrets
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.settings import settings
from app.models.models import Device, DeviceCredential, DeviceProtocol, Factory, Zone
from app.utils.security import hash_password, verify_password

logger = logging.getLogger("aeris.device_auth")

# Mosquitto's PBKDF2-SHA512 password format ($7$): 101 iterations, 12-byte salt,
# 64-byte derived key, both base64 encoded. Kept in one place so the generator
# is unit-testable without a broker.
MOSQUITTO_ITERATIONS = 101
MOSQUITTO_SALT_BYTES = 12
MOSQUITTO_KEY_BYTES = 64


class UnregisteredDeviceError(Exception):
    """Telemetry arrived from a device that is not pre-registered."""


class DisabledDeviceError(Exception):
    """Telemetry arrived from a device whose registration is disabled."""


def default_topic(factory_name: str, zone: Zone) -> str:
    """Broker topic a node in this zone publishes on (matches the simulator)."""
    token = (zone.description or "").split("(")[-1].rstrip(")") if "ZONE_" in (zone.description or "") else None
    zone_token = token if token and token.startswith("ZONE_") else zone.name.upper().replace(" ", "_")
    return f"{settings.mqtt_topic_prefix}/{factory_name}/{zone_token}/telemetry"


def mosquitto_password_hash(password: str, salt: bytes | None = None) -> str:
    """Produce a mosquitto `password_file` entry value for `password`."""
    salt = salt or secrets.token_bytes(MOSQUITTO_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac(
        "sha512", password.encode("utf-8"), salt, MOSQUITTO_ITERATIONS, dklen=MOSQUITTO_KEY_BYTES
    )
    return f"$7${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def generate_password(device_id: str) -> str:
    """Random per-device secret. Not derived from the device id — credentials
    must not be guessable from a public identifier."""
    return f"{device_id}-{secrets.token_urlsafe(18)}"


def build_mosquitto_password_file(entries: list[tuple[str, str]]) -> str:
    """Render (username, plaintext_password) pairs as a mosquitto password file."""
    lines = [f"{username}:{mosquitto_password_hash(password)}" for username, password in entries]
    return "\n".join(lines) + ("\n" if lines else "")


def build_acl_file(
    devices: list[tuple[str, str | None]],
    backend_username: str = "aeris-backend",
    prefix: str | None = None,
) -> str:
    """Render a per-device ACL.

    Each node may only write to the exact telemetry topic it was provisioned
    for (least privilege); the backend user may read every telemetry topic and
    write the alert topic.
    """
    prefix = prefix or settings.mqtt_topic_prefix
    lines = [
        "# AERIS MQTT ACL — GENERATED FILE, DO NOT EDIT BY HAND",
        "# Regenerate with: python backend/scripts/sync_mqtt_credentials.py",
        "",
        f"user {backend_username}",
        f"topic read {prefix}/+/+/telemetry",
        f"topic write {prefix}/alerts",
        "",
    ]
    for username, topic in devices:
        lines.append(f"user {username}")
        if topic:
            lines.append(f"topic write {topic}")
        # Explicitly deny everything else for this user.
        lines.append(f"topic read {prefix}/alerts")
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Provisioning
# ---------------------------------------------------------------------------
def _unique_username(db: Session, device: Device) -> str:
    """Usernames default to the hardware id; fall back to a suffixed form if a
    previous device already claimed it (device ids can be recycled in the field)."""
    existing = db.scalar(select(DeviceCredential).where(DeviceCredential.device_id == device.id))
    if existing is not None:
        return existing.username
    return device.device_id


def issue_credential(db: Session, device: Device, password: str | None = None, rotate: bool = False) -> str:
    """Create or rotate the device credential; returns the plaintext ONCE."""
    plaintext = password or generate_password(device.device_id)
    username = _unique_username(db, device)
    credential = device.credential
    now = datetime.now(timezone.utc)
    if credential is None:
        credential = DeviceCredential(
            device_id=device.id,
            username=username,
            password_hash=hash_password(plaintext),
            issued_at=now,
        )
        db.add(credential)
    else:
        credential.username = username
        credential.password_hash = hash_password(plaintext)
        credential.enabled = True
        if rotate:
            credential.rotated_at = now
    # A device with a credential is no longer usable with the legacy shared
    # secret: it must present its own.
    device.credential = credential
    return plaintext


def register_device(
    db: Session,
    *,
    device_id: str,
    zone_id: int,
    firmware_version: str = "unknown",
    mqtt_topic: str | None = None,
    password: str | None = None,
    issue_credentials: bool = True,
    protocol: str = DeviceProtocol.MQTT.value,
) -> tuple[Device, str | None]:
    """Pre-register a node (idempotent) and optionally issue its credential."""
    if protocol not in {p.value for p in DeviceProtocol}:
        raise ValueError(f"Unsupported device protocol '{protocol}'")

    device = db.scalar(select(Device).where(Device.device_id == device_id))
    zone = db.get(Zone, zone_id)
    if zone is None:
        raise ValueError(f"Zone {zone_id} does not exist")

    created = device is None
    if device is None:
        device = Device(
            device_id=device_id,
            zone_id=zone_id,
            firmware_version=firmware_version,
            protocol=protocol,
        )
        db.add(device)
        db.flush()
    else:
        device.zone_id = zone_id
        device.protocol = protocol
        if firmware_version != "unknown":
            device.firmware_version = firmware_version

    if device.provisioned_at is None:
        device.provisioned_at = datetime.now(timezone.utc)
    device.enabled = True

    # Only MQTT nodes need a broker topic; an HTTP node posting to the REST
    # endpoint has nothing to publish and must not be granted a broker ACL.
    if protocol == DeviceProtocol.MQTT.value:
        if mqtt_topic:
            device.mqtt_topic = mqtt_topic
        elif not device.mqtt_topic:
            factory = db.get(Factory, zone.factory_id)
            device.mqtt_topic = default_topic(factory.name if factory else "FACTORY_01", zone)

    # Idempotent by design: a re-register must not silently invalidate a
    # credential already flashed onto a field device. Rotation is explicit
    # (pass a password, or call rotate_credential).
    plaintext: str | None = None
    if issue_credentials:
        default = (
            f"{settings.device_default_password}-{device_id}"
            if settings.device_default_password
            else None
        )
        if device.credential is None:
            plaintext = issue_credential(db, device, password=password or default, rotate=False)
        elif password is not None:
            plaintext = issue_credential(db, device, password=password, rotate=True)
    db.commit()
    db.refresh(device)
    logger.info("Device %s %s (zone=%s)", device_id, "registered" if created else "updated", zone.name)
    return device, plaintext


def resolve_registered_device(
    db: Session, device_id: str, observed_topic: str | None = None
) -> Device:
    """Return the pre-registered device or raise.

    Raises UnregisteredDeviceError when auto-provisioning is disabled, and
    DisabledDeviceError when the registration exists but is switched off.
    """
    device = db.scalar(select(Device).where(Device.device_id == device_id))
    if device is None:
        raise UnregisteredDeviceError(
            f"Device '{device_id}' is not pre-registered (DEVICE_AUTO_PROVISION=false)"
        )
    if not device.enabled:
        raise DisabledDeviceError(f"Device '{device_id}' is disabled")
    if observed_topic and not device.mqtt_topic:
        device.mqtt_topic = observed_topic
    return device


def authenticate_device(db: Session, username: str, password: str) -> Device | None:
    """Verify a device credential (used by the HTTP telemetry path)."""
    credential = db.scalar(select(DeviceCredential).where(DeviceCredential.username == username))
    if credential is None or not credential.enabled:
        return None
    if not verify_password(password, credential.password_hash):
        return None
    credential.last_authenticated_at = datetime.now(timezone.utc)
    db.commit()
    return credential.device


def credential_status(db: Session, device: Device) -> dict:
    """Non-sensitive view of a device's credential state for the dashboard."""
    credential = device.credential
    if credential is None:
        return {
            "device_id": device.device_id,
            "protocol": device.protocol,
            "has_credential": False,
            "username": None,
            "enabled": device.enabled,
            "issued_at": None,
            "rotated_at": None,
        }
    return {
        "device_id": device.device_id,
        "protocol": device.protocol,
        "has_credential": True,
        "username": credential.username,
        "enabled": credential.enabled and device.enabled,
        "issued_at": credential.issued_at,
        "rotated_at": credential.rotated_at,
    }
