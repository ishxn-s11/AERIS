"""Multi-factory scoping tests.

The data model is hierarchical (Factory -> Zone -> Device), so every aggregate
must be filterable by factory without leaking another plant's data. These tests
pin that contract on the API surface the dashboard relies on.
"""
import pytest
from sqlalchemy import select

from app.database.session import SessionLocal
from app.models.models import Device, Factory, Zone


@pytest.fixture(scope="module")
def factories(client, auth_headers):
    return client.get("/api/factories", headers=auth_headers).json()


PRIMARY_FACTORY = "FACTORY_01"
SECONDARY_FACTORY = "FACTORY_02"


def _factory_id(factories: list[dict], name: str) -> int:
    return next(f["id"] for f in factories if f["name"] == name)


def test_seed_provides_at_least_two_factories(factories):
    """A factory switcher is only meaningful with more than one plant."""
    assert len(factories) >= 2
    assert all({"id", "name"} <= set(f) for f in factories)
    names = {f["name"] for f in factories}
    assert {PRIMARY_FACTORY, SECONDARY_FACTORY} <= names


def test_every_zone_belongs_to_exactly_one_seeded_factory():
    with SessionLocal() as db:
        zones = db.scalars(select(Zone)).all()
        factory_ids = {f.id for f in db.scalars(select(Factory)).all()}
        assert zones
        for zone in zones:
            assert zone.factory_id in factory_ids


def test_zone_list_is_filtered_by_factory(client, auth_headers, factories):
    first = _factory_id(factories, PRIMARY_FACTORY)
    all_zones = client.get("/api/zones", headers=auth_headers).json()
    scoped = client.get("/api/zones", params={"factory_id": first}, headers=auth_headers).json()

    assert scoped, "the primary plant must have zones"
    assert len(scoped) < len(all_zones)
    assert {z["id"] for z in scoped} <= {z["id"] for z in all_zones}

    with SessionLocal() as db:
        expected = {z.id for z in db.scalars(select(Zone).where(Zone.factory_id == first))}
    assert {z["id"] for z in scoped} == expected


def test_devices_are_filtered_by_factory(client, auth_headers, factories):
    first = _factory_id(factories, PRIMARY_FACTORY)
    scoped = client.get("/api/devices", params={"factory_id": first}, headers=auth_headers).json()
    assert scoped
    with SessionLocal() as db:
        expected = {
            d.device_id
            for d in db.scalars(
                select(Device).join(Zone, Zone.id == Device.zone_id).where(Zone.factory_id == first)
            )
        }
    assert {d["device_id"] for d in scoped} == expected


def test_dashboard_summary_scopes_zone_counts(client, auth_headers, factories):
    first = _factory_id(factories, PRIMARY_FACTORY)
    summary = client.get(
        "/api/dashboard/summary", params={"factory_id": first}, headers=auth_headers
    ).json()
    with SessionLocal() as db:
        zone_count = len(db.scalars(select(Zone).where(Zone.factory_id == first)).all())
    assert summary["total_zones"] == zone_count
    assert (
        summary["safe_zones"] + summary["warning_zones"] + summary["high_zones"] + summary["critical_zones"]
        == zone_count
    )


def test_zones_live_scopes_to_the_requested_factory(client, auth_headers, factories):
    first = _factory_id(factories, PRIMARY_FACTORY)
    rows = client.get("/api/dashboard/zones-live", params={"factory_id": first}, headers=auth_headers).json()
    assert isinstance(rows, list)
    assert rows, "the live view must return the scoped zones"
    assert all(set(r) >= {"zone_id", "name", "risk_level", "risk_score"} for r in rows)
    with SessionLocal() as db:
        scoped_ids = {z.id for z in db.scalars(select(Zone).where(Zone.factory_id == first))}
    assert {r["zone_id"] for r in rows} <= scoped_ids


def test_alerts_and_predictions_accept_a_factory_filter(client, auth_headers, factories):
    first = _factory_id(factories, PRIMARY_FACTORY)
    for path in ("/api/alerts", "/api/predictions"):
        res = client.get(path, params={"factory_id": first}, headers=auth_headers)
        assert res.status_code == 200, (path, res.text)
        assert isinstance(res.json(), list)


def test_unknown_factory_filter_returns_an_empty_list_not_an_error(client, auth_headers):
    res = client.get("/api/zones", params={"factory_id": 987_654}, headers=auth_headers)
    assert res.status_code == 200
    assert res.json() == []
    devices = client.get("/api/devices", params={"factory_id": 987_654}, headers=auth_headers)
    assert devices.status_code == 200
    assert devices.json() == []


def test_factory_endpoints_require_authentication(client):
    assert client.get("/api/factories").status_code == 401
    assert client.get("/api/zones").status_code == 401


def test_zone_detail_route_is_addressable_across_factories(client, auth_headers, factories):
    for factory in factories:
        zones = client.get("/api/zones", params={"factory_id": factory["id"]}, headers=auth_headers).json()
        for zone in zones:
            assert client.get(f"/api/zones/{zone['id']}", headers=auth_headers).status_code == 200
