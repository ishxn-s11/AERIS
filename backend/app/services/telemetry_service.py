"""Telemetry ingestion + processing pipeline.

Per-frame flow:
1. Resolve device/zone rows — pre-registration enforced when
   DEVICE_AUTO_PROVISION=false (see app/services/device_auth_service.py).
2. Persist the validated SensorReading.
3. Update device health (status/last_seen).
4. Append to the per-device feature window and extract engineered features.
5. Run the hybrid risk engine (rules -> trend -> ML) on the snapshot.
6. Project the +N minute forecast (regressor or trend extrapolation).
7. Persist a RiskPrediction (+ rate-limited RiskForecast) and update the zone.
8. Raise alerts through the AlertManager (deduped; publishes to the alert bus).
9. Broadcast telemetry, risk and forecast updates to dashboards over WebSocket.
"""
import json
import logging
import statistics
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.settings import settings
from app.ml.features import ReadingSample, compute_features, feature_store
from app.ml.risk_engine import evaluate
from app.models.models import (
    AlertSeverity,
    Device,
    DeviceStatus,
    Factory,
    HazardType,
    RiskPrediction,
    SensorReading,
    Zone,
)
from app.schemas.schemas import TelemetryIn
from app.services import device_auth_service, forecast_service
from app.services.alert_service import AlertManager, AlertPayload, DatabaseProvider, WebSocketProvider
from app.services.device_auth_service import DisabledDeviceError, UnregisteredDeviceError
from app.services.notifications import bus as alert_bus
from app.websocket.manager import manager

logger = logging.getLogger("aeris.telemetry")

# Process-wide alert handling: in-process providers for the durable record and
# the live dashboard push, plus the bus for out-of-process notification workers.
alert_manager = AlertManager(
    providers=[DatabaseProvider(), WebSocketProvider(manager)],
    bus=alert_bus,
)


class DeviceAuthError(Exception):
    """Frame failed the device-identity check (HTTP path with DEVICE_AUTH_REQUIRED)."""

_SEVERITY_BY_LEVEL = {"WARNING": AlertSeverity.WARNING, "HIGH": AlertSeverity.HIGH, "CRITICAL": AlertSeverity.CRITICAL}

_HAZARD_MESSAGES = {
    HazardType.FIRE: "Fire signature detected in {zone}",
    HazardType.GAS_LEAK: "Possible combustible gas leak detected in {zone}",
    HazardType.OVERHEATING: "Equipment overheating detected in {zone}",
    HazardType.SMOKE: "Heavy smoke detected in {zone}",
    HazardType.GENERAL: "Elevated atmospheric risk detected in {zone}",
}


class UnknownFactoryError(Exception):
    """Telemetry references a factory that is not provisioned."""


def _hazard_from_string(hazard: str | None) -> HazardType:
    if hazard:
        try:
            return HazardType(hazard)
        except ValueError:
            pass
    return HazardType.GENERAL


def _resolve_device(db: Session, frame: TelemetryIn, observed_topic: str | None = None) -> tuple[Device, Zone]:
    """Resolve device+zone, honouring the configured registration policy.

    Strict mode (`DEVICE_AUTO_PROVISION=false`) is the production posture: an
    unknown or disabled node is rejected, because an unregistered sensor on a
    safety network is indistinguishable from an attacker or a mis-flashed unit.
    Auto-provisioning stays available for hackathon demos and is reported as
    such in the API/README rather than hidden.
    """
    factory = db.scalar(select(Factory).where(Factory.name == frame.factory_id))
    if factory is None:
        raise UnknownFactoryError(f"Factory '{frame.factory_id}' is not provisioned")

    if not settings.device_auto_provision:
        device = device_auth_service.resolve_registered_device(db, frame.device_id, observed_topic)
        zone = db.get(Zone, device.zone_id)
        if zone is None:
            raise UnregisteredDeviceError(f"Device '{frame.device_id}' points at a missing zone")
        return device, zone

    zone = db.scalar(
        select(Zone).where(Zone.factory_id == factory.id, Zone.name == (frame.zone_name or frame.zone_id))
    )
    if zone is None:
        zone = db.scalar(select(Zone).where(Zone.factory_id == factory.id, Zone.name == frame.zone_id))
    if zone is None:
        zone = Zone(factory_id=factory.id, name=frame.zone_name or frame.zone_id, description="Auto-provisioned")
        db.add(zone)
        db.flush()

    device = db.scalar(select(Device).where(Device.device_id == frame.device_id))
    if device is None:
        device = Device(device_id=frame.device_id, zone_id=zone.id)
        db.add(device)
        db.flush()
        logger.warning("Auto-provisioned device %s (DEVICE_AUTO_PROVISION=true)", device.device_id)
    elif not device.enabled:
        raise DisabledDeviceError(f"Device '{device.device_id}' is disabled")
    if device.zone_id != zone.id:
        device.zone_id = zone.id
    if observed_topic and device.mqtt_topic != observed_topic:
        device.mqtt_topic = observed_topic
    return device, zone


def _detect_sensor_malfunction(history: list[ReadingSample], device: Device) -> bool:
    """True when every measurement channel is unnaturally frozen.

    Real sensors always show independent noise; a stale firmware buffer or a
    wedged ADC shows identical values across ALL channels simultaneously —
    that signature is what this check targets.
    """
    if len(history) < settings.malfunction_min_samples:
        return False
    stds = [
        statistics.pstdev([s.temperature for s in history[-settings.malfunction_min_samples:]]),
        statistics.pstdev([s.methane for s in history[-settings.malfunction_min_samples:]]),
        statistics.pstdev([s.smoke for s in history[-settings.malfunction_min_samples:]]),
    ]
    return max(stds) < settings.malfunction_flatline_std


def ingest_telemetry(
    db: Session,
    frame: TelemetryIn,
    observed_topic: str | None = None,
    require_device_token: bool = False,
) -> SensorReading:
    """Full ingestion + processing for one telemetry frame.

    `require_device_token` is set by the HTTP endpoint when DEVICE_AUTH_REQUIRED
    is on: frames arriving over MQTT have already been authenticated by the
    broker ACL, so they only need the registration check.
    """
    device, zone = _resolve_device(db, frame, observed_topic)

    if require_device_token:
        if not frame.auth_token:
            raise DeviceAuthError(
                f"Device '{frame.device_id}' must present an auth_token (DEVICE_AUTH_REQUIRED=true)"
            )
        if device_auth_service.authenticate_device(db, device.device_id, frame.auth_token) is None:
            raise DeviceAuthError(f"Device '{frame.device_id}' presented an invalid credential")

    reading = SensorReading(
        device_id=device.id,
        timestamp=frame.timestamp or datetime.now(timezone.utc),
        temperature=frame.temperature,
        humidity=frame.humidity,
        co=frame.co,
        methane=frame.methane,
        smoke=frame.smoke,
        flame=frame.flame,
    )
    db.add(reading)

    # Device health: any valid frame proves liveness.
    device.last_seen = datetime.now(timezone.utc)
    if device.status != DeviceStatus.ONLINE.value:
        device.status = DeviceStatus.ONLINE.value

    db.flush()  # assign reading.id before commit; keeps error handling simple

    # ------------------------------------------------------------------
    # Phase 2 pipeline: features -> hybrid engine -> prediction -> alert
    # ------------------------------------------------------------------
    sample = ReadingSample(
        timestamp=reading.timestamp,
        temperature=reading.temperature,
        humidity=reading.humidity,
        co=reading.co,
        methane=reading.methane,
        smoke=reading.smoke,
        flame=reading.flame,
    )
    history = feature_store.append(device.device_id, sample)
    features = compute_features(history)

    if _detect_sensor_malfunction(history, device):
        if device.status != DeviceStatus.DEGRADED.value:
            device.status = DeviceStatus.DEGRADED.value
            alert_manager.raise_alert(db, AlertPayload(
                severity=AlertSeverity.WARNING,
                hazard_type=HazardType.SENSOR_MALFUNCTION,
                message=f"SENSOR MALFUNCTION: {device.device_id} in {zone.name} reports frozen values on all channels",
                device_db_id=device.id,
                zone_db_id=zone.id,
                device_id=device.device_id,
                zone_name=zone.name,
                sensor_values=features,
            ))
    else:
        result = evaluate(features)

        # Forward projection (+N minutes). Advisory only: forecasts never feed
        # the alert thresholds, so a bad projection cannot raise a false alarm.
        forecast = forecast_service.build_forecast(features, result["risk_score"], result["risk_level"])
        forecast_service.persist_forecast(db, zone.id, device.id, forecast)

        prediction = RiskPrediction(
            zone_id=zone.id,
            device_id=device.id,
            risk_score=result["risk_score"],
            risk_level=result["risk_level"],
            predicted_hazard=result["predicted_hazard"],
            model_confidence=result["model_confidence"],
            explanation=json.dumps(result["explanation"]),
        )
        db.add(prediction)
        zone.risk_level = result["risk_level"]

        if result["risk_level"] != "SAFE":
            hazard = _hazard_from_string(result["predicted_hazard"])
            severity = _SEVERITY_BY_LEVEL[result["risk_level"]]
            message = _HAZARD_MESSAGES[hazard].format(zone=zone.name)
            alert_manager.raise_alert(db, AlertPayload(
                severity=severity,
                hazard_type=hazard,
                message=message,
                device_db_id=device.id,
                zone_db_id=zone.id,
                device_id=device.device_id,
                zone_name=zone.name,
                sensor_values=features,
                risk_score=result["risk_score"],
                explanation=result["explanation"],
            ))

        _broadcast_risk(zone, device, result, forecast)

    db.commit()
    db.refresh(reading)

    _broadcast_reading(reading, device, zone)
    return reading


def _broadcast_reading(reading: SensorReading, device: Device, zone: Zone) -> None:
    manager.broadcast_threadsafe({
        "type": "telemetry",
        "device_id": device.device_id,
        "zone_id": zone.id,
        "zone_name": zone.name,
        "reading_id": reading.id,
        "timestamp": reading.timestamp.isoformat(),
        "temperature": reading.temperature,
        "humidity": reading.humidity,
        "co": reading.co,
        "methane": reading.methane,
        "smoke": reading.smoke,
        "flame": reading.flame,
    })


def _broadcast_risk(zone: Zone, device: Device, result: dict, forecast: dict | None = None) -> None:
    manager.broadcast_threadsafe({
        "type": "risk_update",
        "zone_id": zone.id,
        "zone_name": zone.name,
        "device_id": device.device_id,
        "risk_level": result["risk_level"],
        "risk_score": result["risk_score"],
        "predicted_hazard": result["predicted_hazard"],
        "model_confidence": result["model_confidence"],
        "explanation": result["explanation"],
        "forecast": forecast,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


# ---------------------------------------------------------------------------
# Device health monitoring (offline detection + DEVICE_OFFLINE alerts)
# ---------------------------------------------------------------------------
def check_device_health(db: Session) -> list[Device]:
    """Mark devices offline when telemetry stops arriving and alert."""
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=settings.device_offline_after_seconds)
    changed: list[Device] = []
    devices = db.scalars(select(Device).where(Device.status != DeviceStatus.OFFLINE.value)).all()
    for device in devices:
        seen = device.last_seen
        is_stale = seen is None or seen < cutoff
        if is_stale and device.status != DeviceStatus.OFFLINE.value:
            device.status = DeviceStatus.OFFLINE.value
            changed.append(device)
    if changed:
        db.commit()
    for device in changed:
        zone_name = device.zone.name if device.zone else "unknown zone"
        alert_manager.raise_alert(db, AlertPayload(
            severity=AlertSeverity.WARNING,
            hazard_type=HazardType.DEVICE_OFFLINE,
            message=(
                f"DEVICE OFFLINE: {device.device_id} in {zone_name} has not reported for "
                f">{settings.device_offline_after_seconds}s"
            ),
            device_db_id=device.id,
            zone_db_id=device.zone_id,
            device_id=device.device_id,
            zone_name=zone_name,
        ))
    return changed


def latest_reading(db: Session, device_db_id: int) -> SensorReading | None:
    """Most recent stored frame from one device, or None if it never reported.

    Backs the connections page's "is this node actually delivering?" check —
    a provisioned node with no readings is configured but not working, which
    is a different problem from a misconfigured one.
    """
    return db.scalar(
        select(SensorReading)
        .where(SensorReading.device_id == device_db_id)
        .order_by(SensorReading.timestamp.desc())
        .limit(1)
    )


def device_health_snapshot(db: Session, device_ids: list[int] | None = None) -> dict:
    """Device status counts, optionally scoped to a set of devices (factory view)."""
    stmt = select(Device)
    if device_ids is not None:
        if not device_ids:
            return {"online": 0, "offline": 0, "degraded": 0}
        stmt = stmt.where(Device.id.in_(device_ids))
    devices = db.scalars(stmt).all()
    counts = {"online": 0, "offline": 0, "degraded": 0}
    for d in devices:
        counts[d.status] = counts.get(d.status, 0) + 1
    return counts
