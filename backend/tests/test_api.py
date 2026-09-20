"""API endpoint tests (authenticated, SQLite-backed, no MQTT broker required)."""
from datetime import datetime, timezone

from app.database.session import SessionLocal
from app.models.models import Alert, AlertSeverity, HazardType


def _telemetry_payload(device_id: str = "NODE_02", **overrides) -> dict:
    payload = {
        "device_id": device_id,
        "factory_id": "FACTORY_01",
        "zone_id": "ZONE_02",
        "zone_name": "Chemical Storage",
        "temperature": 30.0,
        "humidity": 45.0,
        "co": 12,
        "methane": 60,
        "smoke": 40,
        "flame": False,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    payload.update(overrides)
    return payload


def _zone_id_by_name(client, auth_headers, name: str) -> int:
    zones = client.get("/api/zones", headers=auth_headers).json()
    return next(z["id"] for z in zones if z["name"] == name)


# ---------------------------------------------------------------------------
# Health & auth
# ---------------------------------------------------------------------------
def test_health(client):
    assert client.get("/api/health").json() == {"status": "ok", "service": "aeris-backend"}


def test_login_success(client):
    res = client.post("/api/auth/login", json={"email": "admin@aeris.io", "password": "admin123"})
    assert res.status_code == 200
    body = res.json()
    assert body["token_type"] == "bearer"
    assert body["role"] == "admin"
    assert "access_token" in body


def test_login_wrong_password(client):
    res = client.post("/api/auth/login", json={"email": "admin@aeris.io", "password": "wrong"})
    assert res.status_code == 401


def test_me_requires_auth(client):
    assert client.get("/api/auth/me").status_code == 401


def test_me_returns_user(client, auth_headers):
    res = client.get("/api/auth/me", headers=auth_headers)
    assert res.status_code == 200
    assert res.json()["email"] == "admin@aeris.io"


def test_registration_closed_after_seed(client):
    res = client.post("/api/auth/register", json={
        "name": "Sneaky", "email": "sneaky@aeris.io", "password": "password123", "role": "admin",
    })
    assert res.status_code == 403


def test_admin_can_register_operator(client, auth_headers):
    res = client.post("/api/auth/register", headers=auth_headers, json={
        "name": "Demo Operator", "email": "operator@aeris.io",
        "password": "operator123", "role": "operator",
    })
    assert res.status_code == 201
    assert res.json()["role"] == "operator"


# ---------------------------------------------------------------------------
# Telemetry ingestion (HTTP path — same service the MQTT subscriber calls)
# ---------------------------------------------------------------------------
def test_telemetry_ingestion_roundtrip(client, auth_headers):
    res = client.post("/api/telemetry", json=_telemetry_payload())
    assert res.status_code == 201, res.text

    zone_id = _zone_id_by_name(client, auth_headers, "Chemical Storage")
    latest = client.get(f"/api/zones/{zone_id}/telemetry", headers=auth_headers).json()
    assert len(latest) >= 1
    top = latest[0]
    assert top["device_id"] == "NODE_02"
    assert abs(top["temperature"] - 30.0) < 0.01
    assert top["methane"] == 60


def test_telemetry_rejects_out_of_range(client):
    res = client.post("/api/telemetry", json=_telemetry_payload(temperature=9999))
    assert res.status_code == 422


def test_telemetry_rejects_unknown_factory(client):
    res = client.post("/api/telemetry", json=_telemetry_payload(factory_id="FACTORY_99"))
    assert res.status_code == 422


def test_telemetry_rejects_extra_fields(client):
    payload = _telemetry_payload()
    payload["injected"] = "field"
    res = client.post("/api/telemetry", json=payload)
    assert res.status_code == 422


# ---------------------------------------------------------------------------
# Domain endpoints
# ---------------------------------------------------------------------------
def test_factories_list(client, auth_headers):
    res = client.get("/api/factories", headers=auth_headers)
    assert res.status_code == 200
    assert any(f["name"] == "FACTORY_01" for f in res.json())


def test_zones_seeded(client, auth_headers):
    zones = client.get("/api/zones", headers=auth_headers).json()
    names = {z["name"] for z in zones}
    assert {"Boiler Room", "Chemical Storage", "Generator Room", "Warehouse"} <= names
    # All zones belong to a provisioned factory, and the demo plant keeps its
    # original six-zone layout after the second factory was seeded.
    assert all(z.get("factory_id") for z in zones)
    demo_plant = client.get("/api/zones", headers=auth_headers, params={"factory_id": 1}).json()
    assert len(demo_plant) == 6


def test_zone_history_requires_auth(client):
    assert client.get("/api/zones/1/history").status_code == 401


def test_devices_show_online_after_ingestion(client, auth_headers):
    client.post("/api/telemetry", json=_telemetry_payload(device_id="NODE_04",
                                                          zone_name="Pipeline Section"))
    devices = client.get("/api/devices", headers=auth_headers).json()
    node04 = next(d for d in devices if d["device_id"] == "NODE_04")
    assert node04["status"] == "online"
    assert node04["last_seen"] is not None


def test_dashboard_summary_shape(client, auth_headers):
    client.post("/api/telemetry", json=_telemetry_payload())
    res = client.get("/api/dashboard/summary", headers=auth_headers)
    assert res.status_code == 200
    body = res.json()
    for key in ("active_sensors", "total_zones", "safe_zones", "warning_zones",
                "high_zones", "critical_zones",    "active_alerts", "average_risk_score"):
        assert key in body
    # The seed provisions the demo plant plus a second factory; scoping the
    # summary to the demo plant must yield exactly its six zones.
    zones = client.get("/api/zones", headers=auth_headers).json()
    assert body["total_zones"] == len(zones)
    scoped = client.get("/api/dashboard/summary", headers=auth_headers,
                        params={"factory_id": 1}).json()
    assert scoped["total_zones"] == 6


# ---------------------------------------------------------------------------
# Alerts & RBAC on acknowledgment
# ---------------------------------------------------------------------------
def _create_alert_for_test(zone_id: int = 2) -> int:
    with SessionLocal() as db:
        alert = Alert(
            zone_id=zone_id, severity=AlertSeverity.CRITICAL.value,
            hazard_type=HazardType.GAS_LEAK.value, message="Test: methane rising",
        )
        db.add(alert)
        db.commit()
        return alert.id


def test_alerts_require_auth(client):
    assert client.get("/api/alerts").status_code == 401


def test_alert_acknowledge_rbac(client, auth_headers, safety_officer_headers):
    alert_id = _create_alert_for_test()

    # Operator must not exist yet -> register one as admin first.
    client.post("/api/auth/register", headers=auth_headers, json={
        "name": "Op", "email": "op2@aeris.io", "password": "operator123", "role": "operator",
    })
    op_login = client.post("/api/auth/login", json={"email": "op2@aeris.io", "password": "operator123"})
    op_headers = {"Authorization": f"Bearer {op_login.json()['access_token']}"}

    # Operator forbidden:
    res = client.post(f"/api/alerts/{alert_id}/acknowledge", headers=op_headers)
    assert res.status_code == 403

    # Safety officer allowed:
    res = client.post(f"/api/alerts/{alert_id}/acknowledge", headers=safety_officer_headers)
    assert res.status_code == 200
    body = res.json()
    assert body["acknowledged"] is True
    assert body["acknowledged_by"] == "safety@aeris.io"

    # Unauthenticated forbidden:
    assert client.post(f"/api/alerts/{alert_id}/acknowledge").status_code == 401
