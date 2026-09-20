"""Pydantic v2 schemas: MQTT telemetry validation + API request/response models."""
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


# ---------------------------------------------------------------------------
# MQTT boundary model — every incoming telemetry frame must pass this gate.
# Physical ranges reject obviously broken sensors (e.g. shorted ADC pin).
# ---------------------------------------------------------------------------
class TelemetryIn(BaseModel):
    device_id: str = Field(min_length=3, max_length=64)
    factory_id: str = Field(min_length=2, max_length=64)
    zone_id: str = Field(min_length=2, max_length=64)
    zone_name: str | None = Field(default=None, max_length=120)
    temperature: float = Field(ge=-50, le=150)          # °C
    humidity: float = Field(ge=0, le=100)               # %RH
    co: float = Field(ge=0, le=1023)                    # ADC counts (MQ-7)
    methane: float = Field(ge=0, le=1023)               # ADC counts (MQ-2/MQ-4)
    smoke: float = Field(ge=0, le=1023)                 # ADC counts (MQ-2)
    flame: bool = False
    timestamp: datetime | None = None  # node clock; backend re-stamps on receipt
    # Per-device secret for the HTTP telemetry path (DEVICE_AUTH_REQUIRED=true).
    # The MQTT path is authenticated by the broker ACL instead, and the two are
    # deliberately independent: a leaked payload secret cannot open the broker,
    # and a valid broker session cannot forge another node's device_id.
    auth_token: str | None = Field(default=None, max_length=200)

    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class RegisterRequest(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    role: str = "operator"  # validated in the service layer


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
    name: str


# ---------------------------------------------------------------------------
# Read models
# ---------------------------------------------------------------------------
class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    email: EmailStr
    role: str


class FactoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    location: str
    latitude: float | None = None
    longitude: float | None = None
    site_span_m: float | None = None


# ---------------------------------------------------------------------------
# IoT connections (broker onboarding + node provisioning)
# ---------------------------------------------------------------------------

class BrokerProbeRequest(BaseModel):
    """Target for a broker connectivity test.

    Every field is optional: omit them to probe the broker this backend is
    already configured against.
    """
    host: str | None = Field(default=None, max_length=200)
    port: int | None = Field(default=None, ge=1, le=65535)
    username: str | None = Field(default=None, max_length=100)
    password: str | None = Field(default=None, max_length=200)
    use_tls: bool = False
    timeout_seconds: float = Field(default=4.0, ge=0.5, le=20.0)


class DeviceConnectionRequest(BaseModel):
    """Onboard one IoT node onto a transport."""
    device_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.:-]+$")
    zone_id: int
    protocol: str = "mqtt"
    firmware_version: str = Field(default="unknown", max_length=32)
    mqtt_topic: str | None = Field(default=None, max_length=200)
    # Supplying a password rotates an existing credential deliberately; omitting
    # it keeps the credential already flashed onto the hardware.
    password: str | None = Field(default=None, min_length=8, max_length=128)

    @field_validator("protocol")
    @classmethod
    def _known_protocol(cls, value: str) -> str:
        if value not in {"mqtt", "http"}:
            raise ValueError("protocol must be 'mqtt' or 'http'")
        return value


class DeviceConnectionOut(BaseModel):
    """One row of the connection register."""
    id: int
    device_id: str
    protocol: str
    status: str
    enabled: bool
    last_seen: datetime | None
    firmware_version: str
    zone_id: int
    zone_name: str | None
    factory_id: int | None
    factory_name: str | None
    endpoint: str | None
    username: str | None
    has_credential: bool
    credential_enabled: bool
    issued_at: datetime | None
    rotated_at: datetime | None
    last_authenticated_at: datetime | None


class DeviceConnectionRecipe(BaseModel):
    """Everything needed to make a new node talk, in copy-paste form."""
    device_id: str
    protocol: str
    factory_name: str
    zone_name: str
    username: str | None = None
    password: str | None = None
    mqtt_host: str | None = None
    mqtt_port: int | None = None
    mqtt_topic: str | None = None
    http_url: str | None = None
    sample_payload: dict = Field(default_factory=dict)
    publish_command: str | None = None
    curl_command: str | None = None
    acl_line: str | None = None
    warning: str = ""


class ConnectionOverview(BaseModel):
    """Broker state, transports and the full connection register."""
    broker: dict
    http_ingest: dict
    devices: list[DeviceConnectionOut]
    total: int
    by_protocol: dict
    online: int
    revoked: int
    unprovisioned: int
    auto_provision: bool


class FactoryLocationUpdate(BaseModel):
    """A plant's site fix: where it is, and how big its 0-100 grid is."""
    latitude: float = Field(ge=-90.0, le=90.0)
    longitude: float = Field(ge=-180.0, le=180.0)
    # 50 m (a single building) to 20 km (a refinery complex); beyond that the
    # zone grid is no longer a useful abstraction.
    site_span_m: float = Field(ge=50.0, le=20_000.0)
    location: str | None = Field(default=None, max_length=200)


class ZoneOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    factory_id: int
    name: str
    description: str
    x_coordinate: float
    y_coordinate: float
    risk_level: str


class DeviceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    device_id: str
    zone_id: int
    protocol: str = "mqtt"
    status: str
    last_seen: datetime | None
    firmware_version: str


class ReadingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    device_id: str  # hardware/device identifier string (e.g. NODE_02)
    timestamp: datetime
    temperature: float
    humidity: float
    co: float
    methane: float
    smoke: float
    flame: bool


class AlertOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    device_id: int | None
    zone_id: int | None
    timestamp: datetime
    severity: str
    hazard_type: str
    message: str
    sensor_values: dict | None = None
    acknowledged: bool
    acknowledged_by: str | None
    acknowledged_at: datetime | None


class PredictionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    zone_id: int
    device_id: int | None
    timestamp: datetime
    risk_score: float
    risk_level: str
    predicted_hazard: str | None
    model_confidence: float | None
    explanation: list[str] | None = None  # ordered contributing-factor strings


class AckRequest(BaseModel):
    note: str | None = Field(default=None, max_length=500)


class ForecastOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    zone_id: int
    device_id: int | None
    timestamp: datetime
    horizon_minutes: int
    predicted_risk_score: float
    predicted_risk_level: str
    predicted_hazard: str | None
    confidence: float | None
    method: str  # ml_regressor | trend_extrapolation
    expected_breach_minutes: float | None
    drivers: list[str] | None = None


class DeviceCredentialOut(BaseModel):
    """Non-sensitive credential status (never the hash, never the plaintext)."""
    device_id: str
    protocol: str = "mqtt"
    has_credential: bool
    username: str | None
    enabled: bool
    issued_at: datetime | None
    rotated_at: datetime | None


class DeviceCredentialIssued(BaseModel):
    """Returned exactly once, at issue/rotation time."""
    device_id: str
    username: str
    password: str
    mqtt_topic: str | None
    warning: str = (
        "Store this password now — it is not recoverable. Regenerate the broker "
        "files with backend/scripts/sync_mqtt_credentials.py."
    )


class DeviceRegisterRequest(BaseModel):
    device_id: str = Field(min_length=3, max_length=64)
    zone_id: int
    protocol: str = "mqtt"
    firmware_version: str = Field(default="unknown", max_length=32)
    mqtt_topic: str | None = Field(default=None, max_length=200)
    password: str | None = Field(default=None, min_length=8, max_length=128)

    @field_validator("protocol")
    @classmethod
    def _known_protocol(cls, value: str) -> str:
        if value not in {"mqtt", "http"}:
            raise ValueError("protocol must be 'mqtt' or 'http'")
        return value


class DashboardSummary(BaseModel):
    active_sensors: int
    total_zones: int
    safe_zones: int
    warning_zones: int
    high_zones: int
    critical_zones: int
    active_alerts: int
    average_risk_score: float
    factory_id: int
    factory_name: str
