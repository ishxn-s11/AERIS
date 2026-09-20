"""Factory and zone routers (locations, telemetry, history)."""
import json
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_roles
from app.database.session import get_db
from app.models.models import (
    Device, Factory, RiskPrediction, SensorReading, User, UserRole, Zone,
)
from app.schemas.schemas import (
    FactoryLocationUpdate, FactoryOut, ForecastOut, PredictionOut, ReadingOut, ZoneOut,
)
from app.services import forecast_service

logger = logging.getLogger("aeris.factories")

factories_router = APIRouter(prefix="/factories", tags=["factories"])
zones_router = APIRouter(prefix="/zones", tags=["zones"])


@factories_router.get("", response_model=list[FactoryOut])
def list_factories(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return db.scalars(select(Factory).order_by(Factory.id)).all()


@factories_router.get("/{factory_id}", response_model=FactoryOut)
def get_factory(factory_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    factory = db.get(Factory, factory_id)
    if factory is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Factory not found")
    return factory


@factories_router.get("/{factory_id}/zones", response_model=list[ZoneOut])
def list_factory_zones(factory_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    if db.get(Factory, factory_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Factory not found")
    return db.scalars(select(Zone).where(Zone.factory_id == factory_id).order_by(Zone.id)).all()


@factories_router.patch("/{factory_id}/location", response_model=FactoryOut)
def set_factory_location(
    factory_id: int,
    payload: FactoryLocationUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.ADMIN, UserRole.SAFETY_OFFICER)),
):
    """Record a plant's site fix (coordinates + grid span).

    Dispersion modelling is meaningless without this: the zone grid is local to
    the site, so the map cannot place a plume until the site itself is on the
    earth. Only the roles that own the risk process may set it.
    """
    factory = db.get(Factory, factory_id)
    if factory is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Factory not found")

    factory.latitude = payload.latitude
    factory.longitude = payload.longitude
    factory.site_span_m = payload.site_span_m
    if payload.location:
        factory.location = payload.location
    db.commit()
    db.refresh(factory)
    logger.info(
        "Site fix for %s set to %.5f, %.5f (span %.0f m) by %s",
        factory.name, factory.latitude, factory.longitude, factory.site_span_m, user.email,
    )
    return factory


def _zone_or_404(db: Session, zone_id: int) -> Zone:
    zone = db.get(Zone, zone_id)
    if zone is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Zone not found")
    return zone


def _rows_to_readings(rows) -> list[ReadingOut]:
    """Convert (SensorReading, Device) rows to API models keyed by device_id string."""
    return [
        ReadingOut(
            id=r.id,
            device_id=d.device_id,
            timestamp=r.timestamp,
            temperature=r.temperature,
            humidity=r.humidity,
            co=r.co,
            methane=r.methane,
            smoke=r.smoke,
            flame=r.flame,
        )
        for r, d in rows
    ]


@zones_router.get("", response_model=list[ZoneOut])
def list_zones(
    factory_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """All zones, or only those of one factory (multi-site console)."""
    stmt = select(Zone).order_by(Zone.id)
    if factory_id is not None:
        stmt = stmt.where(Zone.factory_id == factory_id)
    return db.scalars(stmt).all()


@zones_router.get("/{zone_id}", response_model=ZoneOut)
def get_zone(zone_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return _zone_or_404(db, zone_id)


@zones_router.get("/{zone_id}/telemetry", response_model=list[ReadingOut])
def zone_latest_telemetry(zone_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    """Most recent reading per device in the zone."""
    _zone_or_404(db, zone_id)
    device_rows = db.scalars(select(Device).where(Device.zone_id == zone_id)).all()
    out: list[ReadingOut] = []
    for device in device_rows:
        row = db.execute(
            select(SensorReading, Device)
            .join(Device, Device.id == SensorReading.device_id)
            .where(SensorReading.device_id == device.id)
            .order_by(SensorReading.timestamp.desc())
            .limit(1)
        ).first()
        if row:
            out.extend(_rows_to_readings([row]))
    return out


@zones_router.get("/{zone_id}/history", response_model=list[ReadingOut])
def zone_history(
    zone_id: int,
    hours: float = Query(default=1, gt=0, le=24 * 30),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Time-ranged reading history for all devices in the zone."""
    _zone_or_404(db, zone_id)
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    rows = db.execute(
        select(SensorReading, Device)
        .join(Device, Device.id == SensorReading.device_id)
        .where(Device.zone_id == zone_id, SensorReading.timestamp >= since)
        .order_by(SensorReading.timestamp.asc())
        .limit(10000)
    ).all()
    return _rows_to_readings(rows)


@zones_router.get("/{zone_id}/forecast", response_model=list[ForecastOut])
def zone_forecast(
    zone_id: int,
    limit: int = Query(default=60, ge=1, le=500),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Projection history for the zone, oldest first.

    Convenience alias of /api/forecasts/{zone_id} kept with the other
    zone-scoped series endpoints so a zone view needs one base path.
    """
    _zone_or_404(db, zone_id)
    rows = forecast_service.forecast_series(db, zone_id, limit)
    return [ForecastOut(**forecast_service.forecast_to_dict(r)) for r in rows]


@zones_router.get("/{zone_id}/predictions", response_model=list[PredictionOut])
def zone_predictions(
    zone_id: int,
    limit: int = Query(default=20, ge=1, le=200),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Recent risk predictions for the zone (newest first)."""
    _zone_or_404(db, zone_id)
    rows = db.scalars(
        select(RiskPrediction)
        .where(RiskPrediction.zone_id == zone_id)
        .order_by(RiskPrediction.timestamp.desc())
        .limit(limit)
    ).all()
    result = []
    for p in rows:
        result.append(
            PredictionOut(
                id=p.id,
                zone_id=p.zone_id,
                device_id=p.device_id,
                timestamp=p.timestamp,
                risk_score=p.risk_score,
                risk_level=p.risk_level,
                predicted_hazard=p.predicted_hazard,
                model_confidence=p.model_confidence,
                explanation=json.loads(p.explanation) if p.explanation else None,
            )
        )
    return result
