"""CSV import / export and HTTP gateway simulator tests."""
from __future__ import annotations

import csv
import io

import pytest


class TestCsvExport:
    def test_export_returns_a_csv_with_all_devices(self, client, auth_headers):
        res = client.get("/api/connections/devices/export", headers=auth_headers)
        assert res.status_code == 200
        assert "text/csv" in res.headers["content-type"]
        reader = csv.DictReader(io.StringIO(res.text))
        rows = list(reader)
        assert len(rows) >= 9
        assert {"device_id", "protocol", "zone", "factory", "status"}.issubset(set(reader.fieldnames))

    def test_export_lists_all_protocols(self, client, auth_headers):
        zones = client.get("/api/factories/1/zones", headers=auth_headers).json()
        client.post(
            "/api/connections/devices",
            json={"device_id": "EXPORT_HTTP_NODE", "zone_id": zones[0]["id"], "protocol": "http"},
            headers=auth_headers,
        )
        res = client.get("/api/connections/devices/export", headers=auth_headers)
        reader = csv.DictReader(io.StringIO(res.text))
        protocols = {row["protocol"] for row in reader}
        assert "mqtt" in protocols
        assert "http" in protocols

    def test_export_is_any_authenticated_user(self, client, operator_headers):
        res = client.get("/api/connections/devices/export", headers=operator_headers)
        assert res.status_code == 200


class TestCsvImport:
    def test_import_registers_new_devices(self, client, auth_headers):
        csv_content = "device_id,zone_name,protocol,firmware_version\nIMPORT_NODE_1,Boiler Room,mqtt,aeris-esp32-0.1.0\nIMPORT_NODE_2,Chemical Storage,http,gateway-1.0\n"
        res = client.post(
            "/api/connections/devices/import",
            files={"file": ("import.csv", csv_content.encode(), "text/csv")},
            headers=auth_headers,
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["total_rows"] == 2
        assert body["registered"] == 2
        assert body["skipped"] == 0
        assert body["errors"] == []

    def test_import_is_idempotent(self, client, auth_headers):
        csv_content = "device_id,zone_name,protocol\nIMPORT_IDEMPOTENT,Boiler Room,mqtt\n"
        client.post(
            "/api/connections/devices/import",
            files={"file": ("import.csv", csv_content.encode(), "text/csv")},
            headers=auth_headers,
        )
        res = client.post(
            "/api/connections/devices/import",
            files={"file": ("import.csv", csv_content.encode(), "text/csv")},
            headers=auth_headers,
        )
        body = res.json()
        assert body["registered"] == 1
        overview = client.get("/api/connections/overview", headers=auth_headers).json()
        import_nodes = [d for d in overview["devices"] if d["device_id"] == "IMPORT_IDEMPOTENT"]
        assert len(import_nodes) == 1

    def test_import_skips_bad_rows_with_errors(self, client, auth_headers):
        csv_content = "device_id,zone_name,protocol\nGOOD_NODE,Boiler Room,mqtt\n,Boiler Room,mqtt\nBAD_PROTO,Boiler Room,zigbee\nNO_ZONE,Nonexistent Zone,mqtt\n"
        res = client.post(
            "/api/connections/devices/import",
            files={"file": ("import.csv", csv_content.encode(), "text/csv")},
            headers=auth_headers,
        )
        body = res.json()
        assert body["total_rows"] == 4
        assert body["registered"] == 1
        assert body["skipped"] == 3
        assert len(body["errors"]) == 3
        for err in body["errors"]:
            assert "row" in err
            assert "error" in err

    def test_import_requires_admin_or_safety(self, client, operator_headers):
        csv_content = "device_id,zone_name,protocol\nNODE_X,Boiler Room,mqtt\n"
        res = client.post(
            "/api/connections/devices/import",
            files={"file": ("import.csv", csv_content.encode(), "text/csv")},
            headers=operator_headers,
        )
        assert res.status_code == 403

    def test_import_with_zone_id_column(self, client, auth_headers):
        zones = client.get("/api/factories/1/zones", headers=auth_headers).json()
        zone_id = zones[0]["id"]
        csv_content = f"device_id,zone_id,protocol\nIMPORT_BY_ID,{zone_id},mqtt\n"
        res = client.post(
            "/api/connections/devices/import",
            files={"file": ("import.csv", csv_content.encode(), "text/csv")},
            headers=auth_headers,
        )
        assert res.status_code == 200
        body = res.json()
        assert body["registered"] == 1

    def test_import_with_password_column(self, client, auth_headers):
        csv_content = "device_id,zone_name,protocol,password\nIMPORT_WITH_PASS,Boiler Room,mqtt,my-secret-123\n"
        res = client.post(
            "/api/connections/devices/import",
            files={"file": ("import.csv", csv_content.encode(), "text/csv")},
            headers=auth_headers,
        )
        body = res.json()
        assert body["registered"] == 1
        overview = client.get("/api/connections/overview", headers=auth_headers).json()
        node = next(d for d in overview["devices"] if d["device_id"] == "IMPORT_WITH_PASS")
        assert node["has_credential"] is True


class TestSimulator:
    def test_simulator_status_endpoint(self, client, auth_headers):
        res = client.get("/api/connections/simulator", headers=auth_headers)
        assert res.status_code == 200
        body = res.json()
        assert "running" in body
        assert isinstance(body["running"], bool)

    def test_simulator_start_stop_cycle(self, client, auth_headers):
        start = client.post("/api/connections/simulator/start", headers=auth_headers)
        assert start.status_code == 200
        assert start.json()["running"] is True
        status = client.get("/api/connections/simulator", headers=auth_headers).json()
        assert status["running"] is True
        stop = client.post("/api/connections/simulator/stop", headers=auth_headers)
        assert stop.status_code == 200
        assert stop.json()["running"] is False

    def test_simulator_requires_admin_or_safety(self, client, operator_headers):
        res = client.post("/api/connections/simulator/start", headers=operator_headers)
        assert res.status_code == 403


@pytest.fixture(scope="session")
def operator_headers(client, auth_headers):
    client.post(
        "/api/auth/register",
        json={
            "name": "CSV Operator",
            "email": "csv-operator@aeris.io",
            "password": "operator123",
            "role": "operator",
        },
        headers=auth_headers,
    )
    token = client.post(
        "/api/auth/login",
        json={"email": "csv-operator@aeris.io", "password": "operator123"},
    ).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}
