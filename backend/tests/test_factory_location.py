"""Factory site-fix tests.

The dispersion map can only draw a plume once the plant is placed on the earth:
latitude, longitude and the span its 0-100 zone grid covers. These tests cover
that endpoint, its validation, and its authorisation.
"""
from __future__ import annotations

import pytest


@pytest.fixture(scope="session")
def operator_headers(client, auth_headers):
    """Headers for a plain operator — created through the admin-only route."""
    client.post(
        "/api/auth/register",
        json={
            "name": "Site Operator",
            "email": "operator@aeris.io",
            "password": "operator123",
            "role": "operator",
        },
        headers=auth_headers,
    )
    token = client.post(
        "/api/auth/login",
        json={"email": "operator@aeris.io", "password": "operator123"},
    ).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


class TestSiteFix:
    def test_factories_expose_their_site_fix(self, client, auth_headers):
        rows = client.get("/api/factories", headers=auth_headers).json()
        assert rows
        for factory in rows:
            # A seeded plant must be locatable, or the GIS map is unusable.
            assert factory["latitude"] is not None
            assert factory["longitude"] is not None
            assert factory["site_span_m"] > 0

    def test_site_fix_reaches_the_live_zone_payload(self, client, auth_headers):
        zones = client.get("/api/dashboard/zones-live", headers=auth_headers).json()
        assert zones
        zone = zones[0]
        assert zone["factory_latitude"] is not None
        assert zone["factory_longitude"] is not None
        assert zone["factory_site_span_m"] > 0

    def test_admin_can_set_the_site_fix(self, client, auth_headers):
        before = client.get("/api/factories/1", headers=auth_headers).json()
        payload = {"latitude": 19.07609, "longitude": 72.877426, "site_span_m": 1200.0}
        res = client.patch("/api/factories/1/location", json=payload, headers=auth_headers)
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["latitude"] == payload["latitude"]
        assert body["longitude"] == payload["longitude"]
        assert body["site_span_m"] == payload["site_span_m"]

        # The change is persisted, not just echoed back.
        after = client.get("/api/factories/1", headers=auth_headers).json()
        assert after["latitude"] == payload["latitude"]

        # Restore the seeded fix so other tests see the documented topology.
        restore = {
            "latitude": before["latitude"],
            "longitude": before["longitude"],
            "site_span_m": before["site_span_m"],
        }
        client.patch("/api/factories/1/location", json=restore, headers=auth_headers)

    def test_safety_officer_may_set_the_site_fix(self, client, safety_officer_headers):
        """The person who owns the risk process owns the site fix."""
        before = client.get("/api/factories/2", headers=safety_officer_headers).json()
        res = client.patch(
            "/api/factories/2/location",
            json={"latitude": 28.61, "longitude": 77.21, "site_span_m": 700.0},
            headers=safety_officer_headers,
        )
        assert res.status_code == 200, res.text
        client.patch(
            "/api/factories/2/location",
            json={
                "latitude": before["latitude"],
                "longitude": before["longitude"],
                "site_span_m": before["site_span_m"],
            },
            headers=safety_officer_headers,
        )

    def test_operator_may_not_set_the_site_fix(self, client, operator_headers):
        res = client.patch(
            "/api/factories/1/location",
            json={"latitude": 0.0, "longitude": 0.0, "site_span_m": 500.0},
            headers=operator_headers,
        )
        assert res.status_code == 403

    def test_out_of_range_coordinates_are_rejected(self, client, auth_headers):
        for bad in (
            {"latitude": 91.0, "longitude": 0.0, "site_span_m": 500.0},
            {"latitude": 0.0, "longitude": 181.0, "site_span_m": 500.0},
            {"latitude": 0.0, "longitude": 0.0, "site_span_m": 10.0},
            {"latitude": 0.0, "longitude": 0.0, "site_span_m": 50_000.0},
        ):
            res = client.patch("/api/factories/1/location", json=bad, headers=auth_headers)
            assert res.status_code == 422, bad

    def test_unknown_factory_is_a_404(self, client, auth_headers):
        res = client.patch(
            "/api/factories/9999/location",
            json={"latitude": 1.0, "longitude": 1.0, "site_span_m": 500.0},
            headers=auth_headers,
        )
        assert res.status_code == 404
