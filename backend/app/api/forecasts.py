"""Risk projection endpoints (+N minute horizon).

Kept separate from the monitoring router because forecasts are advisory output,
not incident data: they have their own retention, their own method field and
their own dashboard surface.
"""
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.database.session import get_db
from app.models.models import RiskForecast, User, Zone
from app.schemas.schemas import ForecastOut
from app.services import forecast_service

forecasts_router = APIRouter(prefix="/forecasts", tags=["forecasts"])


@forecasts_router.get("", response_model=list[ForecastOut])
def latest_forecasts(
    factory_id: int | None = Query(default=None),
    horizon_minutes: int | None = Query(default=None, ge=1, le=120),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Most recent projection per zone (optionally scoped to one factory)."""
    zone_stmt = select(Zone)
    if factory_id is not None:
        zone_stmt = zone_stmt.where(Zone.factory_id == factory_id)
    zones = db.scalars(zone_stmt.order_by(Zone.id)).all()

    out: list[ForecastOut] = []
    for zone in zones:
        row = forecast_service.latest_forecast(db, zone.id)
        if row is None:
            continue
        if horizon_minutes is not None and row.horizon_minutes != horizon_minutes:
            continue
        out.append(ForecastOut(**forecast_service.forecast_to_dict(row)))
    return out


@forecasts_router.get("/{zone_id}", response_model=list[ForecastOut])
def zone_forecast_series(
    zone_id: int,
    limit: int = Query(default=60, ge=1, le=500),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Projection history for one zone, oldest first (drives the projection chart)."""
    if db.get(Zone, zone_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Zone not found")
    rows: list[RiskForecast] = forecast_service.forecast_series(db, zone_id, limit)
    return [ForecastOut(**forecast_service.forecast_to_dict(r)) for r in rows]
