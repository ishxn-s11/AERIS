"""Devices, alerts, dashboard summary and WebSocket endpoints."""
import json
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_roles
from app.config.settings import settings
from app.database.session import SessionLocal, get_db
from app.models.models import (
    Alert,
    Device,
    DeviceStatus,
    Factory,
    RiskLevel,
    RiskPrediction,
    SensorReading,
    User,
    UserRole,
    Zone,
)
from app.schemas.schemas import AlertOut, DashboardSummary, DeviceOut, PredictionOut, TelemetryIn
from app.services import forecast_service, telemetry_service
from app.services.device_auth_service import DisabledDeviceError, UnregisteredDeviceError
from app.services.telemetry_service import DeviceAuthError, UnknownFactoryError, ingest_telemetry
from app.websocket.manager import manager

devices_router = APIRouter(prefix="/devices", tags=["devices"])
alerts_router = APIRouter(prefix="/alerts", tags=["alerts"])
dashboard_router = APIRouter(prefix="/dashboard", tags=["dashboard"])
telemetry_router = APIRouter(prefix="/telemetry", tags=["telemetry"])
# `/api/predictions` is part of the documented public API surface; the same
# handler is also served under `/api/dashboard/predictions` for the dashboard's
# own calls. One implementation, two entry points — no duplicated query logic.
predictions_router = APIRouter(prefix="/predictions", tags=["predictions"])


def _alert_out(alert: Alert) -> AlertOut:
    """ORM -> API mapping; sensor_values is stored as a JSON string and must be
    parsed before validation (model_validate on the raw row fails)."""
    return AlertOut(
        id=alert.id,
        device_id=alert.device_id,
        zone_id=alert.zone_id,
        timestamp=alert.timestamp,
        severity=alert.severity,
        hazard_type=alert.hazard_type,
        message=alert.message,
        sensor_values=json.loads(alert.sensor_values) if alert.sensor_values else None,
        acknowledged=alert.acknowledged,
        acknowledged_by=alert.acknowledged_by,
        acknowledged_at=alert.acknowledged_at,
    )


@dashboard_router.get("/predictions", response_model=list[PredictionOut])
def list_predictions(
    zone_id: int | None = Query(default=None),
    factory_id: int | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Recent risk predictions across all zones (or one zone/factory), newest first."""
    stmt = select(RiskPrediction).order_by(RiskPrediction.timestamp.desc()).limit(limit)
    if zone_id is not None:
        stmt = stmt.where(RiskPrediction.zone_id == zone_id)
    if factory_id is not None:
        stmt = stmt.join(Zone, Zone.id == RiskPrediction.zone_id).where(Zone.factory_id == factory_id)
    rows = db.scalars(stmt).all()
    return [
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
        for p in rows
    ]


# Same handler, second mount point: keeps `/api/predictions` (documented API)
# and `/api/dashboard/predictions` (dashboard) byte-identical.
predictions_router.add_api_route(
    "", list_predictions, methods=["GET"], response_model=list[PredictionOut]
)


@telemetry_router.post("", status_code=status.HTTP_201_CREATED)
def ingest_http_telemetry(frame: TelemetryIn, db: Session = Depends(get_db)):
    """HTTP ingestion path — calls the same service as the MQTT subscriber.

    Used by tests and by deployments where a node has HTTP but no MQTT. The
    device must be pre-registered when DEVICE_AUTO_PROVISION=false, and must
    present its `auth_token` when DEVICE_AUTH_REQUIRED=true.
    """
    try:
        reading = ingest_telemetry(db, frame, require_device_token=settings.device_auth_required)
    except UnknownFactoryError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from None
    except UnregisteredDeviceError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from None
    except DisabledDeviceError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from None
    except DeviceAuthError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from None
    return {"id": reading.id, "device_id": frame.device_id, "status": "accepted"}


@devices_router.get("", response_model=list[DeviceOut])
def list_devices(
    factory_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    stmt = select(Device).order_by(Device.id)
    if factory_id is not None:
        stmt = stmt.join(Zone, Zone.id == Device.zone_id).where(Zone.factory_id == factory_id)
    devices = db.scalars(stmt).all()
    return [DeviceOut.model_validate(d) for d in devices]


@devices_router.get("/{device_db_id}", response_model=DeviceOut)
def get_device(device_db_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    device = db.get(Device, device_db_id)
    if device is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Device not found")
    return DeviceOut.model_validate(device)


@devices_router.post("/{device_db_id}/ping")
def ping_device(device_db_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    """Manual operator action to mark a device seen (prototype helper)."""
    device = db.get(Device, device_db_id)
    if device is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Device not found")
    device.last_seen = datetime.now(timezone.utc)
    device.status = DeviceStatus.ONLINE.value
    db.commit()
    return {"ok": True, "device_id": device.device_id, "status": device.status}


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------
@alerts_router.get("", response_model=list[AlertOut])
def list_alerts(
    severity: str | None = Query(default=None),
    acknowledged: bool | None = Query(default=None),
    zone_id: int | None = Query(default=None),
    factory_id: int | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    stmt = select(Alert).order_by(Alert.timestamp.desc()).limit(limit)
    if severity:
        stmt = stmt.where(Alert.severity == severity)
    if acknowledged is not None:
        stmt = stmt.where(Alert.acknowledged == acknowledged)
    if zone_id is not None:
        stmt = stmt.where(Alert.zone_id == zone_id)
    if factory_id is not None:
        stmt = stmt.join(Zone, Zone.id == Alert.zone_id).where(Zone.factory_id == factory_id)
    rows = db.scalars(stmt).all()
    return [_alert_out(a) for a in rows]


@alerts_router.post("/{alert_id}/acknowledge", response_model=AlertOut)
def acknowledge(
    alert_id: int,
    note: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.ADMIN, UserRole.SAFETY_OFFICER)),
):
    """Acknowledge an alert — safety officers and admins only."""
    from app.services.alert_service import acknowledge_alert

    alert = acknowledge_alert(db, alert_id, user.email)
    if alert is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Alert not found")
    return _alert_out(alert)


# ---------------------------------------------------------------------------
# Dashboard summary
# ---------------------------------------------------------------------------
@dashboard_router.get("/summary", response_model=DashboardSummary)
def dashboard_summary(
    factory_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Plant-level KPIs. Scoped to one factory when `factory_id` is given."""
    factory = db.get(Factory, factory_id) if factory_id is not None else None
    zone_stmt = select(Zone)
    if factory_id is not None:
        zone_stmt = zone_stmt.where(Zone.factory_id == factory_id)
    zones = db.scalars(zone_stmt.order_by(Zone.id)).all()
    zone_ids = [z.id for z in zones]

    if not zone_ids:
        # Empty factory (or none provisioned): report zeros instead of crashing
        # on an `IN ()` predicate.
        return DashboardSummary(
            active_sensors=0,
            total_zones=0,
            safe_zones=0,
            warning_zones=0,
            high_zones=0,
            critical_zones=0,
            active_alerts=0,
            average_risk_score=0.0,
            factory_id=factory_id or 0,
            factory_name=factory.name if factory else "unprovisioned",
        )

    # Latest prediction per zone (subquery: max timestamp per zone).
    latest_ts = (
        select(RiskPrediction.zone_id, func.max(RiskPrediction.timestamp).label("ts"))
        .where(RiskPrediction.zone_id.in_(zone_ids))
        .group_by(RiskPrediction.zone_id)
        .subquery()
    )
    latest_predictions = db.execute(
        select(RiskPrediction)
        .join(
            latest_ts,
            (RiskPrediction.zone_id == latest_ts.c.zone_id)
            & (RiskPrediction.timestamp == latest_ts.c.ts),
        )
    ).scalars().all()

    levels = {p.zone_id: p.risk_level for p in latest_predictions}
    counts = {"SAFE": 0, "WARNING": 0, "HIGH": 0, "CRITICAL": 0}
    for zone in zones:
        counts[levels.get(zone.id, RiskLevel.SAFE.value)] = (
            counts.get(levels.get(zone.id, RiskLevel.SAFE.value), 0) + 1
        )
    scores = [p.risk_score for p in latest_predictions]

    alert_stmt = select(func.count(Alert.id)).where(Alert.acknowledged.is_(False))
    device_stmt = select(Device.id).where(Device.zone_id.in_(zone_ids))
    if factory_id is not None:
        alert_stmt = alert_stmt.where(Alert.zone_id.in_(zone_ids))
    active_alerts = db.scalar(alert_stmt) or 0
    device_ids = list(db.scalars(device_stmt))
    health = telemetry_service.device_health_snapshot(db, device_ids)

    zone = zones[0] if zones else None
    factory_name = zone.factory.name if zone and zone.factory else "unprovisioned"
    return DashboardSummary(
        active_sensors=health.get("online", 0),
        total_zones=len(zones),
        safe_zones=counts["SAFE"],
        warning_zones=counts["WARNING"],
        high_zones=counts["HIGH"],
        critical_zones=counts["CRITICAL"],
        active_alerts=active_alerts,
        average_risk_score=round(sum(scores) / len(scores), 1) if scores else 0.0,
        factory_id=zone.factory_id if zone else 0,
        factory_name=factory_name,
    )


@dashboard_router.get("/alerts-summary")
def alerts_summary(
    hours: float = Query(default=24, gt=0, le=24 * 30),
    factory_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Alert counts grouped by hour and severity for the analytics page."""
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    rows_stmt = select(Alert.timestamp, Alert.severity).where(Alert.timestamp >= since)
    hazard_stmt = select(Alert.hazard_type, func.count(Alert.id)).group_by(Alert.hazard_type)
    if factory_id is not None:
        factory_zone_ids = list(
            db.scalars(select(Zone.id).where(Zone.factory_id == factory_id))
        )
        rows_stmt = rows_stmt.where(Alert.zone_id.in_(factory_zone_ids or [-1]))
        hazard_stmt = hazard_stmt.where(Alert.zone_id.in_(factory_zone_ids or [-1]))
    rows = db.execute(rows_stmt).all()
    by_hour: dict[str, int] = {}
    by_severity = {"critical": 0, "high": 0, "warning": 0}
    for ts, sev in rows:
        bucket = ts.strftime("%Y-%m-%d %H:00")
        by_hour[bucket] = by_hour.get(bucket, 0) + 1
        if sev in by_severity:
            by_severity[sev] += 1
    hazard_rows = db.execute(hazard_stmt).all()
    hazard_counts = {h: c for h, c in hazard_rows}
    return {
        "hours": hours,
        "total": len(rows),
        "by_severity": by_severity,
        "by_hour": sorted(by_hour.items()),
        "by_hazard": hazard_counts,
    }


@dashboard_router.get("/zones-live")
def zones_live(
    factory_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Current risk level, latest reading and projection per zone (drives the map)."""
    zone_stmt = select(Zone)
    if factory_id is not None:
        zone_stmt = zone_stmt.where(Zone.factory_id == factory_id)
    zones = db.scalars(zone_stmt.order_by(Zone.id)).all()
    latest_ts = (
        select(RiskPrediction.zone_id, func.max(RiskPrediction.timestamp).label("ts"))
        .group_by(RiskPrediction.zone_id)
        .subquery()
    )
    predictions = db.execute(
        select(RiskPrediction)
        .join(latest_ts, (RiskPrediction.zone_id == latest_ts.c.zone_id) & (RiskPrediction.timestamp == latest_ts.c.ts))
    ).scalars().all()
    pred_by_zone = {p.zone_id: p for p in predictions}

    out = []
    for zone in zones:
        latest_reading = db.execute(
            select(Device.device_id, SensorReading)
            .join(SensorReading, SensorReading.device_id == Device.id)
            .where(Device.zone_id == zone.id)
            .order_by(SensorReading.timestamp.desc())
            .limit(1)
        ).first()
        p = pred_by_zone.get(zone.id)
        forecast = forecast_service.latest_forecast(db, zone.id)
        out.append({
            "zone_id": zone.id,
            # Plant identity travels with the zone so the floor plan can draw
            # each factory as its own process diagram instead of merging every
            # plant when no scope is selected.
            "factory_id": zone.factory_id,
            "factory_name": zone.factory.name if zone.factory else None,
            # Site fix, so the GIS map can place the plant on the earth and
            # draw its footprint instead of guessing a coordinate.
            "factory_latitude": zone.factory.latitude if zone.factory else None,
            "factory_longitude": zone.factory.longitude if zone.factory else None,
            "factory_site_span_m": zone.factory.site_span_m if zone.factory else None,
            "name": zone.name,
            "x": zone.x_coordinate,
            "y": zone.y_coordinate,
            "risk_level": p.risk_level if p else RiskLevel.SAFE.value,
            "risk_score": p.risk_score if p else 0.0,
            "predicted_hazard": p.predicted_hazard if p else None,
            "forecast": {
                "horizon_minutes": forecast.horizon_minutes,
                "predicted_risk_score": forecast.predicted_risk_score,
                "predicted_risk_level": forecast.predicted_risk_level,
                "predicted_hazard": forecast.predicted_hazard,
                "confidence": forecast.confidence,
                "method": forecast.method,
                "expected_breach_minutes": forecast.expected_breach_minutes,
                "timestamp": forecast.timestamp.isoformat(),
            } if forecast else None,
            "latest": {
                "device_id": latest_reading[0],
                "timestamp": latest_reading[1].timestamp.isoformat(),
                "temperature": latest_reading[1].temperature,
                "humidity": latest_reading[1].humidity,
                "co": latest_reading[1].co,
                "methane": latest_reading[1].methane,
                "smoke": latest_reading[1].smoke,
                "flame": latest_reading[1].flame,
            } if latest_reading else None,
        })
    return out


# ---------------------------------------------------------------------------
# WebSocket (live dashboard feed)
# ---------------------------------------------------------------------------
ws_router = APIRouter()


@ws_router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """Live feed: telemetry frames, alerts and risk updates.

    Authentication is deferred to Phase 3 frontend wiring; the connection is
    read-only telemetry so exposure risk is minimal for the prototype.
    """
    await manager.connect(websocket)
    try:
        while True:
            # Client messages are optional control frames (e.g. zone filters).
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if msg.get("type") == "subscribe_zones" and isinstance(msg.get("zones"), list):
                manager.set_zone_filter(websocket, set(str(z) for z in msg["zones"]))
            elif msg.get("type") == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception:  # noqa: BLE001
        manager.disconnect(websocket)
