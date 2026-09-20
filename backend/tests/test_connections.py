"""IoT connection onboarding tests.

Covers the three things the connections page promises:
  * the broker this backend is actually attached to is reported honestly,
  * a probe says WHICH stage failed rather than just "failed",
  * a newly onboarded node gets a connection recipe that matches its transport —
    an HTTP node must not be handed an MQTT topic or a broker ACL.
"""
from __future__ import annotations

import socket
import threading

import pytest

from app.services import connection_probe


class TestOverview:
    def test_overview_reports_the_configured_broker(self, client, auth_headers):
        body = client.get("/api/connections/overview", headers=auth_headers).json()
        broker = body["broker"]
        assert broker["host"]
        assert isinstance(broker["port"], int)
        assert broker["subscribe_filter"].endswith("/+/+/telemetry")
        assert broker["auth_mode"] in {"anonymous", "username/password"}
        # Liveness is reported from the real subscriber handle, never assumed.
        assert isinstance(broker["connected"], bool)

    def test_liveness_is_unknown_until_a_subscriber_exists(self, monkeypatch):
        """Before the ingestion loop starts there is nothing to report, and the
        endpoint must not claim a healthy broker it has never talked to."""
        from app.mqtt import subscriber as subscriber_module

        monkeypatch.setattr(subscriber_module, "get_active_subscriber", lambda: None)
        report = connection_probe.active_broker_report()
        assert report["connected"] is False
        assert report["client_id"]
        assert report["subscribe_filter"].endswith("/+/+/telemetry")

    def test_register_covers_every_device(self, client, auth_headers):
        body = client.get("/api/connections/overview", headers=auth_headers).json()
        devices = client.get("/api/devices", headers=auth_headers).json()
        assert body["total"] == len(devices)
        assert body["total"] > 0
        assert sum(body["by_protocol"].values()) == body["total"]

    def test_mqtt_rows_expose_a_topic_http_rows_do_not(self, client, auth_headers):
        body = client.get("/api/connections/overview", headers=auth_headers).json()
        for row in body["devices"]:
            if row["protocol"] == "mqtt":
                assert row["endpoint"] and row["endpoint"].startswith("aeris/")
            else:
                assert row["endpoint"] == "/api/telemetry"

    def test_http_ingest_description_points_at_the_telemetry_route(self, client, auth_headers):
        body = client.get("/api/connections/overview", headers=auth_headers).json()
        assert body["http_ingest"]["endpoint"] == "/api/telemetry"
        assert body["http_ingest"]["url"].endswith("/api/telemetry")


class _StubBroker:
    """A socket that speaks just enough MQTT to answer a CONNECT with CONNACK.

    Exists because the obvious wrong implementation of the probe — trusting
    that the socket opened — passes every other test and fails only against a
    real broker. A raw CONNECT here gets `20 02 00 00` back, so the probe must
    actually read the reply to report success.
    """

    def __init__(self, return_code: int = 0) -> None:
        self.return_code = return_code
        self._server = socket.socket()
        self._server.bind(("127.0.0.1", 0))
        self._server.listen(2)
        self.port = self._server.getsockname()[1]
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._server.accept()
            except OSError:
                return
            try:
                self._handle(conn)
            except OSError:
                pass
            finally:
                conn.close()

    def _handle(self, conn: socket.socket) -> None:
        conn.settimeout(2.0)
        header = conn.recv(1)
        if not header or header[0] >> 4 != 1:  # must be a CONNECT (type 1)
            return
        remaining, multiplier = 0, 1
        while True:
            byte = conn.recv(1)[0]
            remaining += (byte & 0x7F) * multiplier
            if not byte & 0x80:
                break
            multiplier *= 128
        conn.recv(remaining)
        conn.sendall(bytes([0x20, 0x02, 0x00, self.return_code]))

    def close(self) -> None:
        self._stop.set()
        self._server.close()


class TestBrokerProbe:
    def test_accepts_a_broker_that_returns_connack_success(self):
        broker = _StubBroker(return_code=0)
        try:
            result = connection_probe.probe_mqtt_broker("127.0.0.1", broker.port, timeout=3.0)
        finally:
            broker.close()
        assert result.ok is True
        assert result.connack_code == 0
        assert result.stage == "mqtt"
        assert [s["ok"] for s in result.steps] == [True, True]

    def test_a_rejection_connack_reports_the_reason(self):
        broker = _StubBroker(return_code=5)  # not authorised
        try:
            result = connection_probe.probe_mqtt_broker(
                "127.0.0.1", broker.port, username="someone", password="wrong", timeout=3.0
            )
        finally:
            broker.close()
        assert result.ok is False
        assert result.connack_code != 0
        # Paho wraps the MQTT 3.1.1 return code into its own numeric encoding
        # (128 + code), so assert on the human-readable name instead.
        assert "Not authorized" in result.detail or "not authorized" in result.detail.lower()
        # The socket opened, so the failure belongs to the handshake stage.
        assert result.steps[0]["ok"] is True

    def test_unreachable_host_fails_at_the_tcp_stage(self):
        result = connection_probe.probe_mqtt_broker("127.0.0.1", 1, timeout=1.0)
        assert result.ok is False
        assert result.stage == "tcp"
        assert result.steps and result.steps[0]["ok"] is False

    def test_a_socket_that_is_not_mqtt_fails_at_the_handshake(self):
        """A listening TCP port is not a broker — the probe must say so."""
        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]

        def _accept_once():
            try:
                conn, _ = server.accept()
                conn.close()
            except OSError:
                pass

        thread = threading.Thread(target=_accept_once, daemon=True)
        thread.start()
        try:
            result = connection_probe.probe_mqtt_broker("127.0.0.1", port, timeout=1.5)
            assert result.ok is False
            assert result.stage == "mqtt"
            assert result.steps[0]["ok"] is True  # TCP really did open
        finally:
            server.close()

    def test_endpoint_is_admin_or_safety_only(self, client, operator_headers):
        res = client.post(
            "/api/connections/broker/test",
            json={"host": "127.0.0.1", "port": 1, "timeout_seconds": 1},
            headers=operator_headers,
        )
        assert res.status_code == 403

    def test_endpoint_never_echoes_the_password(self, client, auth_headers):
        res = client.post(
            "/api/connections/broker/test",
            json={
                "host": "127.0.0.1",
                "port": 1,
                "username": "someone",
                "password": "sup3r-secret-value",
                "timeout_seconds": 1,
            },
            headers=auth_headers,
        )
        assert res.status_code == 200
        assert "sup3r-secret-value" not in res.text


class TestDeviceOnboarding:
    def test_mqtt_node_gets_a_broker_recipe(self, client, auth_headers):
        zones = client.get("/api/factories/1/zones", headers=auth_headers).json()
        res = client.post(
            "/api/connections/devices",
            json={"device_id": "NODE_TEST_MQTT", "zone_id": zones[0]["id"], "protocol": "mqtt"},
            headers=auth_headers,
        )
        assert res.status_code == 201, res.text
        recipe = res.json()
        assert recipe["protocol"] == "mqtt"
        assert recipe["mqtt_topic"].startswith("aeris/")
        assert recipe["username"] and recipe["password"]
        assert "mosquitto_pub" in recipe["publish_command"]
        assert recipe["acl_line"] and "topic write" in recipe["acl_line"]
        assert recipe["curl_command"] is None

    def test_http_node_gets_a_rest_recipe_and_no_broker_topic(self, client, auth_headers):
        zones = client.get("/api/factories/1/zones", headers=auth_headers).json()
        res = client.post(
            "/api/connections/devices",
            json={"device_id": "GATEWAY_TEST_HTTP", "zone_id": zones[1]["id"], "protocol": "http"},
            headers=auth_headers,
        )
        assert res.status_code == 201, res.text
        recipe = res.json()
        assert recipe["protocol"] == "http"
        assert recipe["http_url"].endswith("/api/telemetry")
        assert "curl" in recipe["curl_command"]
        # Least privilege: an HTTP node has no broker grant at all.
        assert recipe["mqtt_topic"] is None
        assert recipe["acl_line"] is None
        assert recipe["publish_command"] is None

    def test_recipe_is_actually_accepted_by_the_ingest_endpoint(self, client, auth_headers):
        """The recipe must work, not just look plausible."""
        zones = client.get("/api/factories/1/zones", headers=auth_headers).json()
        recipe = client.post(
            "/api/connections/devices",
            json={"device_id": "GATEWAY_E2E_HTTP", "zone_id": zones[0]["id"], "protocol": "http"},
            headers=auth_headers,
        ).json()

        frame = {
            **recipe["sample_payload"],
            "device_id": recipe["device_id"],
            "factory_id": recipe["factory_name"],
            "zone_id": "ZONE_01",
        }
        res = client.post("/api/telemetry", json=frame)
        assert res.status_code == 201, res.text

        verify = client.post(
            f"/api/connections/devices/{self._device_db_id(client, auth_headers, recipe['device_id'])}/verify",
            headers=auth_headers,
        ).json()
        assert verify["verdict"] == "delivering telemetry"
        assert verify["last_reading_at"] is not None
        # HTTP nodes have no broker to be connected to.
        assert verify["broker_connected"] is None

    def test_existing_node_keeps_its_credential_unless_rotated(self, client, auth_headers):
        zones = client.get("/api/factories/1/zones", headers=auth_headers).json()
        body = {"device_id": "NODE_IDEMPOTENT", "zone_id": zones[0]["id"], "protocol": "mqtt"}
        first = client.post("/api/connections/devices", json=body, headers=auth_headers).json()
        second = client.post("/api/connections/devices", json=body, headers=auth_headers).json()
        assert second["password"] is None
        assert second["username"] == first["username"]

    def test_rotation_can_be_requested_deliberately(self, client, auth_headers):
        zones = client.get("/api/factories/1/zones", headers=auth_headers).json()
        body = {"device_id": "NODE_ROTATE_ME", "zone_id": zones[0]["id"], "protocol": "mqtt"}
        first = client.post("/api/connections/devices", json=body, headers=auth_headers).json()
        rotated = client.post(
            "/api/connections/devices",
            json={**body, "password": "brand-new-secret-123"},
            headers=auth_headers,
        ).json()
        assert rotated["password"] == "brand-new-secret-123"
        assert rotated["username"] == first["username"]

    def test_unknown_zone_is_rejected(self, client, auth_headers):
        res = client.post(
            "/api/connections/devices",
            json={"device_id": "NODE_NO_ZONE", "zone_id": 999_999, "protocol": "mqtt"},
            headers=auth_headers,
        )
        assert res.status_code == 404

    def test_unknown_protocol_is_rejected(self, client, auth_headers):
        zones = client.get("/api/factories/1/zones", headers=auth_headers).json()
        res = client.post(
            "/api/connections/devices",
            json={"device_id": "NODE_BAD_PROTO", "zone_id": zones[0]["id"], "protocol": "zigbee"},
            headers=auth_headers,
        )
        assert res.status_code == 422

    def test_operator_cannot_onboard_nodes(self, client, operator_headers):
        zones = client.get("/api/factories/1/zones", headers=operator_headers).json()
        res = client.post(
            "/api/connections/devices",
            json={"device_id": "NODE_UNAUTHORISED", "zone_id": zones[0]["id"], "protocol": "mqtt"},
            headers=operator_headers,
        )
        assert res.status_code == 403

    def test_recipe_lookup_never_returns_the_password(self, client, auth_headers):
        zones = client.get("/api/factories/1/zones", headers=auth_headers).json()
        created = client.post(
            "/api/connections/devices",
            json={"device_id": "NODE_RECIPE_LOOKUP", "zone_id": zones[0]["id"], "protocol": "mqtt"},
            headers=auth_headers,
        ).json()
        secret = created["password"]
        device_db_id = self._device_db_id(client, auth_headers, "NODE_RECIPE_LOOKUP")

        res = client.get(
            f"/api/connections/devices/{device_db_id}/recipe", headers=auth_headers
        )
        assert res.status_code == 200
        assert res.json()["password"] is None
        assert secret not in res.text

    def test_verify_flags_a_silent_node(self, client, auth_headers):
        zones = client.get("/api/factories/1/zones", headers=auth_headers).json()
        client.post(
            "/api/connections/devices",
            json={"device_id": "NODE_NEVER_REPORTS", "zone_id": zones[0]["id"], "protocol": "mqtt"},
            headers=auth_headers,
        )
        device_db_id = self._device_db_id(client, auth_headers, "NODE_NEVER_REPORTS")
        verify = client.post(
            f"/api/connections/devices/{device_db_id}/verify", headers=auth_headers
        ).json()
        assert verify["verdict"] == "no frames received yet"

    def test_verify_flags_a_revoked_node(self, client, auth_headers):
        zones = client.get("/api/factories/1/zones", headers=auth_headers).json()
        created = client.post(
            "/api/connections/devices",
            json={"device_id": "NODE_TO_REVOKE", "zone_id": zones[0]["id"], "protocol": "mqtt"},
            headers=auth_headers,
        ).json()
        device_db_id = self._device_db_id(client, auth_headers, created["device_id"])
        client.post(
            f"/api/security/devices/{device_db_id}/enabled",
            params={"enabled": False},
            headers=auth_headers,
        )
        verify = client.post(
            f"/api/connections/devices/{device_db_id}/verify", headers=auth_headers
        ).json()
        assert verify["enabled"] is False
        assert "revoked" in verify["verdict"]

    @staticmethod
    def _device_db_id(client, headers, device_id: str) -> int:
        for row in client.get("/api/devices", headers=headers).json():
            if row["device_id"] == device_id:
                return row["id"]
        raise AssertionError(f"device {device_id} not found")


class TestProtocolPersistence:
    def test_protocol_survives_a_reread(self, client, auth_headers):
        zones = client.get("/api/factories/1/zones", headers=auth_headers).json()
        client.post(
            "/api/connections/devices",
            json={"device_id": "GATEWAY_PERSISTED", "zone_id": zones[0]["id"], "protocol": "http"},
            headers=auth_headers,
        )
        rows = client.get("/api/connections/overview", headers=auth_headers).json()["devices"]
        row = next(r for r in rows if r["device_id"] == "GATEWAY_PERSISTED")
        assert row["protocol"] == "http"
        assert row["has_credential"] is True
        assert row["factory_name"] and row["zone_name"]


@pytest.fixture(scope="session")
def operator_headers(client, auth_headers):
    client.post(
        "/api/auth/register",
        json={
            "name": "Connection Operator",
            "email": "conn-operator@aeris.io",
            "password": "operator123",
            "role": "operator",
        },
        headers=auth_headers,
    )
    token = client.post(
        "/api/auth/login",
        json={"email": "conn-operator@aeris.io", "password": "operator123"},
    ).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}
