"""AERIS domain models (SQLAlchemy 2.0 declarative style).

Design notes:
- Enum-like columns use String + Python Enum values (native_enum=False) to keep
  schema migrations trivial for a prototype while matching API JSON exactly.
- Indexes cover the query patterns used by the dashboard: by device, by zone,
  and time-ranged scans.
"""
import enum
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.session import Base


def utcnow() -> datetime:
    """Timezone-aware UTC now — the single source of truth for timestamps."""
    return datetime.now(timezone.utc)


class UserRole(str, enum.Enum):
    ADMIN = "admin"
    SAFETY_OFFICER = "safety_officer"
    OPERATOR = "operator"


class DeviceStatus(str, enum.Enum):
    ONLINE = "online"
    OFFLINE = "offline"
    DEGRADED = "degraded"  # reporting, but suspected sensor malfunction


class AlertSeverity(str, enum.Enum):
    WARNING = "warning"
    HIGH = "high"
    CRITICAL = "critical"


class HazardType(str, enum.Enum):
    FIRE = "fire"
    GAS_LEAK = "gas_leak"
    OVERHEATING = "overheating"
    SMOKE = "smoke"
    DEVICE_OFFLINE = "device_offline"
    SENSOR_MALFUNCTION = "sensor_malfunction"
    GENERAL = "general"


class RiskLevel(str, enum.Enum):
    SAFE = "SAFE"
    WARNING = "WARNING"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    email: Mapped[str] = mapped_column(String(180), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(200), nullable=False)  # bcrypt
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, native_enum=False, values_callable=lambda e: [i.value for i in e]),
        default=UserRole.OPERATOR,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Factory(Base):
    __tablename__ = "factories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    location: Mapped[str] = mapped_column(String(200), nullable=False)

    # Site geolocation. Zone coordinates are stored on a 0-100 plant grid, so
    # the GIS map needs three more numbers to put that grid on the earth:
    # the site's own lat/lng, and how many metres the 0-100 grid spans.
    # Null means "not surveyed" — the dispersion map then refuses to guess and
    # asks for coordinates instead of dropping a plume on a random city.
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    site_span_m: Mapped[float | None] = mapped_column(Float, nullable=True)

    zones: Mapped[list["Zone"]] = relationship(back_populates="factory", cascade="all, delete-orphan")


class Zone(Base):
    __tablename__ = "zones"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    factory_id: Mapped[int] = mapped_column(ForeignKey("factories.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    x_coordinate: Mapped[float] = mapped_column(Float, default=0.0)  # 0-100 map grid
    y_coordinate: Mapped[float] = mapped_column(Float, default=0.0)
    risk_level: Mapped[str] = mapped_column(String(16), default=RiskLevel.SAFE.value, nullable=False)

    factory: Mapped[Factory] = relationship(back_populates="zones")
    devices: Mapped[list["Device"]] = relationship(back_populates="zone")
    alerts: Mapped[list["Alert"]] = relationship(back_populates="zone")
    predictions: Mapped[list["RiskPrediction"]] = relationship(back_populates="zone")
    forecasts: Mapped[list["RiskForecast"]] = relationship(back_populates="zone")

    __table_args__ = (Index("ix_zones_factory", "factory_id"),)


class DeviceProtocol(str, enum.Enum):
    """How a node reaches the platform.

    MQTT nodes publish to the broker and are authorised by its ACL; HTTP nodes
    POST frames to /api/telemetry and present their issued credential as an
    auth_token. Same registration, same credential, different transport.
    """
    MQTT = "mqtt"
    HTTP = "http"


class Device(Base):
    __tablename__ = "devices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    device_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    zone_id: Mapped[int] = mapped_column(ForeignKey("zones.id"), nullable=False)
    # Transport this node was onboarded on; drives the connection recipe the
    # connections page shows and whether a broker topic is provisioned.
    protocol: Mapped[str] = mapped_column(
        String(16), default=DeviceProtocol.MQTT.value, nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(16), default=DeviceStatus.OFFLINE.value, nullable=False
    )
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    firmware_version: Mapped[str] = mapped_column(String(32), default="unknown")
    # Disabling a device revokes it: ingestion rejects its frames even if the
    # broker credentials are still valid.
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    provisioned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Fully-qualified publish topic this node is authorised to use; drives the
    # broker ACL generated by scripts/sync_mqtt_credentials.py.
    mqtt_topic: Mapped[str | None] = mapped_column(String(200), nullable=True)

    zone: Mapped[Zone] = relationship(back_populates="devices")
    readings: Mapped[list["SensorReading"]] = relationship(back_populates="device")
    alerts: Mapped[list["Alert"]] = relationship(back_populates="device")
    credential: Mapped["DeviceCredential | None"] = relationship(
        back_populates="device", cascade="all, delete-orphan", uselist=False
    )

    __table_args__ = (Index("ix_devices_zone", "zone_id"),)


class DeviceCredential(Base):
    """Per-device MQTT credential.

    The broker authenticates against these records (materialised into a
    mosquitto password file by `scripts/sync_mqtt_credentials.py`); the backend
    keeps the hash so credentials can be rotated without a broker restart of
    the whole stack. Plaintext is returned exactly once, at issue/rotation time.
    """

    __tablename__ = "device_credentials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id"), unique=True, nullable=False)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(200), nullable=False)  # bcrypt
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_authenticated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    device: Mapped[Device] = relationship(back_populates="credential")


class SensorReading(Base):
    """One telemetry frame from a node. Raw values are stored as received;
    derived features live with the processing engine, not here."""

    __tablename__ = "sensor_readings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id"), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    temperature: Mapped[float] = mapped_column(Float, nullable=False)
    humidity: Mapped[float] = mapped_column(Float, nullable=False)
    co: Mapped[float] = mapped_column(Float, nullable=False)
    methane: Mapped[float] = mapped_column(Float, nullable=False)
    smoke: Mapped[float] = mapped_column(Float, nullable=False)
    flame: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    device: Mapped[Device] = relationship(back_populates="readings")

    __table_args__ = (
        Index("ix_readings_device_ts", "device_id", "timestamp"),
        Index("ix_readings_ts", "timestamp"),
    )


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    device_id: Mapped[int | None] = mapped_column(ForeignKey("devices.id"), nullable=True)
    zone_id: Mapped[int | None] = mapped_column(ForeignKey("zones.id"), nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    hazard_type: Mapped[str] = mapped_column(String(32), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    sensor_values: Mapped[dict | None] = mapped_column(Text, nullable=True)  # JSON string
    acknowledged: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    acknowledged_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    device: Mapped[Device | None] = relationship(back_populates="alerts")
    zone: Mapped[Zone | None] = relationship(back_populates="alerts")

    __table_args__ = (
        Index("ix_alerts_ts", "timestamp"),
        Index("ix_alerts_zone_ts", "zone_id", "timestamp"),
        Index("ix_alerts_ack_sev", "acknowledged", "severity"),
    )


class RiskPrediction(Base):
    """Output of the hybrid detection pipeline (rules + ML) for a zone snapshot."""

    __tablename__ = "risk_predictions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    zone_id: Mapped[int] = mapped_column(ForeignKey("zones.id"), nullable=False)
    device_id: Mapped[int | None] = mapped_column(ForeignKey("devices.id"), nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    risk_score: Mapped[float] = mapped_column(Float, nullable=False)
    risk_level: Mapped[str] = mapped_column(String(16), nullable=False)
    predicted_hazard: Mapped[str | None] = mapped_column(String(32), nullable=True)
    model_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    explanation: Mapped[dict | None] = mapped_column(Text, nullable=True)  # JSON string

    zone: Mapped[Zone] = relationship(back_populates="predictions")

    __table_args__ = (
        Index("ix_predictions_zone_ts", "zone_id", "timestamp"),
    )


class RiskForecast(Base):
    """Forward-looking projection for a zone (default horizon: +10 minutes).

    `method` records how the number was produced (ml_regressor vs
    trend_extrapolation) so the dashboard never presents a fallback as if it
    were a model output.
    """

    __tablename__ = "risk_forecasts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    zone_id: Mapped[int] = mapped_column(ForeignKey("zones.id"), nullable=False)
    device_id: Mapped[int | None] = mapped_column(ForeignKey("devices.id"), nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    horizon_minutes: Mapped[int] = mapped_column(Integer, default=10, nullable=False)
    predicted_risk_score: Mapped[float] = mapped_column(Float, nullable=False)
    predicted_risk_level: Mapped[str] = mapped_column(String(16), nullable=False)
    predicted_hazard: Mapped[str | None] = mapped_column(String(32), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    method: Mapped[str] = mapped_column(String(32), default="trend_extrapolation", nullable=False)
    # Extrapolated minutes until the next risk band opens; None when stable.
    expected_breach_minutes: Mapped[float | None] = mapped_column(Float, nullable=True)
    drivers: Mapped[dict | None] = mapped_column(Text, nullable=True)  # JSON list

    zone: Mapped[Zone] = relationship(back_populates="forecasts")

    __table_args__ = (
        Index("ix_forecasts_zone_ts", "zone_id", "timestamp"),
    )
