"""Database bootstrap: schema creation, demo topology seed, admin users.

Idempotent — safe to run on every backend start. Two demo factories are seeded
so the multi-factory dashboard has something real to switch between; the
topology mirrors the simulator's default node map.

Device rows are created as *pre-registered* devices with an MQTT credential, so
the production posture (registration before trust) is what the demo exercises.
"""
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.settings import settings
from app.database.schema_upgrade import apply_prototype_upgrades
from app.database.session import Base, SessionLocal, engine
from app.models.models import Factory, User, Zone
from app.services import device_auth_service, user_service

logger = logging.getLogger("aeris.seed")

# Zone layout on a 0-100 map grid. Zone coordinates are the CENTRE of each bay
# (the floor plan draws a card around the anchor).
#
# Each factory also carries a real site fix — latitude, longitude and how many
# metres the 0-100 grid spans — so the GIS map can put the plant on the earth
# and draw its actual footprint. The two demo sites are genuine Delhi industrial
# estates: a plume drawn anywhere else would put a fictitious hazard on top of
# whatever real buildings happened to be there.
SEED_FACTORIES: list[dict] = [
    {
        "name": "FACTORY_01",
        "location": "Naraina Industrial Area, New Delhi",
        # Naraina Industrial Area Phase-I (28°38'00"N 77°08'38"E)
        "latitude": 28.63333,
        "longitude": 77.14389,
        # 0-100 grid spans 900 m, so one grid unit is 9 m. Typical for a
        # mid-size process site; a real deployment would survey this.
        "site_span_m": 900.0,
        "zones": [
            # (zone_id, zone_name, x, y, device_id)
            ("ZONE_01", "Boiler Room", 14, 20, "NODE_01"),
            ("ZONE_02", "Chemical Storage", 50, 16, "NODE_02"),
            ("ZONE_03", "Generator Room", 84, 22, "NODE_03"),
            ("ZONE_04", "Pipeline Section", 30, 52, "NODE_04"),
            ("ZONE_05", "Production Area", 64, 58, "NODE_05"),
            ("ZONE_06", "Warehouse", 88, 78, "NODE_06"),
        ],
    },
    {
        "name": "FACTORY_02",
        "location": "Bawana Industrial Area, New Delhi",
        # Bawana Industrial Area, sector 3 (28°47'26"N 77°02'07"E)
        "latitude": 28.79056,
        "longitude": 77.03528,
        "site_span_m": 700.0,
        "zones": [
            ("ZONE_11", "Loading Bay", 20, 26, "NODE_11"),
            ("ZONE_12", "Tank Farm", 54, 18, "NODE_12"),
            ("ZONE_13", "Control Room", 82, 52, "NODE_13"),
        ],
    },
]

DEFAULT_ADMIN = {"name": "AERIS Admin", "email": "admin@aeris.io", "password": "admin123", "role": "admin"}
DEMO_SAFETY_OFFICER = {
    "name": "Demo Safety Officer", "email": "safety@aeris.io",
    "password": "safety123", "role": "safety_officer",
}

# Backwards-compatible alias (tests and docs refer to the primary topology).
SEED_TOPOLOGY = SEED_FACTORIES[0]["zones"]


def init_db() -> None:
    """Create tables, apply additive upgrades, seed demo data."""
    Base.metadata.create_all(bind=engine)
    apply_prototype_upgrades()
    with SessionLocal() as db:
        for spec in SEED_FACTORIES:
            _seed_factory(db, spec)
        _seed_user(db, DEFAULT_ADMIN)
        _seed_user(db, DEMO_SAFETY_OFFICER)
    logger.info("Database initialised (%d factories)", len(SEED_FACTORIES))


def _seed_factory(db: Session, spec: dict) -> None:
    factory = db.scalar(select(Factory).where(Factory.name == spec["name"]))
    if factory is None:
        factory = Factory(name=spec["name"], location=spec["location"])
        db.add(factory)
        db.flush()
        logger.info("Seeded factory %s (%s)", factory.name, factory.location)
    # Backfill the site fix for factories seeded before geolocation existed,
    # and keep it in step with the seed spec on every boot.
    factory.location = spec.get("location", factory.location)
    factory.latitude = spec.get("latitude", factory.latitude)
    factory.longitude = spec.get("longitude", factory.longitude)
    factory.site_span_m = spec.get("site_span_m", factory.site_span_m)
    if factory.latitude is None:
        logger.warning("Factory %s has no coordinates — GIS map will ask for them", factory.name)

    for zone_id, zone_name, x, y, device_id in spec["zones"]:
        zone = db.scalar(
            select(Zone).where(Zone.factory_id == factory.id, Zone.name == zone_name)
        )
        if zone is None:
            zone = Zone(
                factory_id=factory.id,
                name=zone_name,
                description=f"Seeded demo zone ({zone_id})",
                x_coordinate=x,
                y_coordinate=y,
            )
            db.add(zone)
            db.flush()
        # Pre-register the node and issue a broker credential on first sight.
        device_auth_service.register_device(
            db,
            device_id=device_id,
            zone_id=zone.id,
            firmware_version="aeris-esp32-0.1.0",
        )
    db.commit()


def _seed_user(db: Session, spec: dict) -> None:
    # PROTOTYPE ONLY: seeded demo credentials, documented in the README.
    if user_service.get_user_by_email(db, spec["email"]) is None:
        user_service.create_user(db, **spec)
        logger.info("Seeded user %s (%s)", spec["email"], spec["role"])


__all__ = ["init_db", "SEED_FACTORIES", "SEED_TOPOLOGY"]
