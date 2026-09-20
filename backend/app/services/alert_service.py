"""Alert service with a provider interface so channels can be added without
touching detection logic.

Two delivery classes, deliberately separated:

* **In-process providers** (`DatabaseProvider`, `WebSocketProvider`) run inside
  the ingestion path: the durable record and the live dashboard push must happen
  immediately, and must not depend on any external service.
* **Out-of-process channels** (email, SMS, push) are consumed from the alert bus
  (`app/services/notifications/`). Ingestion publishes one event; a worker fans
  it out. A slow SMS gateway can therefore never stall telemetry processing.

The `AlertManager` is the only place that knows the order: persist -> broadcast
-> publish.
"""
import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.models import Alert, AlertSeverity, Device, HazardType
from app.websocket.manager import ConnectionManager

logger = logging.getLogger("aeris.alerts")

# Severity ordering used by channel thresholds ("SMS only for critical").
SEVERITY_RANK: dict[str, int] = {"warning": 1, "high": 2, "critical": 3}


def meets_severity(severity: str, floor: str | None) -> bool:
    """True when `severity` is at or above the configured floor."""
    if not floor:
        return True
    return SEVERITY_RANK.get(severity, 0) >= SEVERITY_RANK.get(floor, 0)


@dataclass
class AlertPayload:
    """Channel-agnostic alert description passed to every provider."""
    severity: AlertSeverity
    hazard_type: HazardType
    message: str
    device_db_id: int | None = None
    zone_db_id: int | None = None
    device_id: str | None = None
    zone_name: str | None = None
    sensor_values: dict = field(default_factory=dict)
    risk_score: float | None = None
    explanation: list[str] = field(default_factory=list)
    occurred_at: datetime | None = None

    def to_dict(self) -> dict:
        """Wire format for the alert bus (strings, not enums)."""
        return {
            "severity": self.severity.value if isinstance(self.severity, AlertSeverity) else str(self.severity),
            "hazard_type": self.hazard_type.value if isinstance(self.hazard_type, HazardType) else str(self.hazard_type),
            "message": self.message,
            "device_db_id": self.device_db_id,
            "zone_db_id": self.zone_db_id,
            "device_id": self.device_id,
            "zone_name": self.zone_name,
            "sensor_values": self.sensor_values,
            "risk_score": self.risk_score,
            "explanation": self.explanation,
            "occurred_at": (self.occurred_at or datetime.now(timezone.utc)).isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AlertPayload":
        """Rebuild a payload received from the bus."""
        occurred = data.get("occurred_at")
        return cls(
            severity=AlertSeverity(data.get("severity", AlertSeverity.WARNING.value)),
            hazard_type=HazardType(data.get("hazard_type", HazardType.GENERAL.value)),
            message=data.get("message", ""),
            device_db_id=data.get("device_db_id"),
            zone_db_id=data.get("zone_db_id"),
            device_id=data.get("device_id"),
            zone_name=data.get("zone_name"),
            sensor_values=data.get("sensor_values") or {},
            risk_score=data.get("risk_score"),
            explanation=data.get("explanation") or [],
            occurred_at=datetime.fromisoformat(occurred) if occurred else None,
        )


class AlertProvider(ABC):
    """Interface every alert channel implements."""

    name: str = "provider"
    # Lowest severity this channel cares about; None means "everything".
    min_severity: str | None = None

    def wants(self, payload: AlertPayload) -> bool:
        """Per-channel severity gate (e.g. SMS only for CRITICAL)."""
        severity = payload.severity.value if isinstance(payload.severity, AlertSeverity) else str(payload.severity)
        return meets_severity(severity, self.min_severity)

    @abstractmethod
    def send(self, db: Session | None, payload: AlertPayload) -> None: ...


class DatabaseProvider(AlertProvider):
    """Persists the alert — the primary record of truth."""

    name = "database"

    def send(self, db: Session, payload: AlertPayload) -> Alert | None:
        alert = Alert(
            device_id=payload.device_db_id,
            zone_id=payload.zone_db_id,
            timestamp=datetime.now(timezone.utc),
            severity=payload.severity.value,
            hazard_type=payload.hazard_type.value,
            message=payload.message,
            sensor_values=json.dumps(payload.sensor_values) if payload.sensor_values else None,
        )
        db.add(alert)
        db.commit()
        db.refresh(alert)
        return alert


class WebSocketProvider(AlertProvider):
    """Pushes the alert to connected dashboards in real time."""

    name = "websocket"

    def __init__(self, manager: ConnectionManager):
        self.manager = manager

    def send(self, db: Session, payload: AlertPayload) -> None:
        self.manager.broadcast_alert(payload)
        logger.info("Alert broadcast: [%s] %s", payload.severity.value, payload.message)


# EmailProvider and SmsProvider live in app/services/notifications/ — they are
# out-of-process channels (bus consumers) and importing them here would create
# a cycle: notifications import AlertPayload from this module.


# ---------------------------------------------------------------------------
# AlertManager — deduplication + provider fan-out.
# ---------------------------------------------------------------------------
class AlertManager:
    """Raises alerts through all registered providers.

    Dedup: identical hazard for the same device within `dedup_window` seconds
    is suppressed so a sustained condition does not flood the feed.
    """

    def __init__(
        self,
        providers: list[AlertProvider],
        dedup_window_seconds: int = 120,
        bus=None,
    ):
        self.providers = providers
        self.dedup_window = timedelta(seconds=dedup_window_seconds)
        # Optional AlertBus: publishes the event for notification channels that
        # live outside the ingestion path (email/SMS/push workers).
        self.bus = bus

    def raise_alert(self, db: Session, payload: AlertPayload) -> Alert | None:
        if self._is_duplicate(db, payload):
            return None
        created: Alert | None = None
        for provider in self.providers:
            result = provider.send(db, payload)
            if provider.name == "database":
                created = result
        # Only publish once the durable record exists: a consumer must never
        # receive an alert that failed to persist.
        if created is not None and self.bus is not None:
            self.bus.publish(payload)
        logger.info(
            "Alert raised: [%s] %s (%s)", payload.severity.value, payload.message, payload.hazard_type.value
        )
        return created

    def _is_duplicate(self, db: Session, payload: AlertPayload) -> bool:
        if payload.device_db_id is None:
            return False
        since = datetime.now(timezone.utc) - self.dedup_window
        stmt = (
            select(Alert)
            .where(
                Alert.device_id == payload.device_db_id,
                Alert.hazard_type == payload.hazard_type.value,
                Alert.timestamp >= since,
            )
            .limit(1)
        )
        return db.scalar(stmt) is not None


def acknowledge_alert(db: Session, alert_id: int, username: str) -> Alert | None:
    """Mark an alert acknowledged (safety officer / admin action)."""
    alert = db.get(Alert, alert_id)
    if alert is None:
        return None
    alert.acknowledged = True
    alert.acknowledged_by = username
    alert.acknowledged_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(alert)
    return alert


def resolve_device_and_zone(db: Session, device_id: str) -> tuple[Device | None, str | None]:
    """Helper to fetch the device row + its zone name from a device_id string."""
    device = db.scalar(select(Device).where(Device.device_id == device_id))
    if device is None:
        return None, None
    return device, device.zone.name if device.zone else None
