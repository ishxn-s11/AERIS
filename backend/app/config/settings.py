"""Central, environment-driven configuration for AERIS.

All tunable values (thresholds, timeouts, secrets) live here so behavior can be
changed via environment variables without touching code. Hazard thresholds are
defined in `settings.hazard_thresholds` and consumed by the risk engine.
"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment / .env (12-factor style)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- App ---
    app_name: str = "AERIS"
    environment: str = "development"
    # Public base URL of this API. Used to render copy-paste connection recipes
    # for HTTP IoT nodes, so it must be the address a device can actually reach
    # (not necessarily the bind address).
    api_base_url: str = "http://localhost:8000"
    mqtt_tls: bool = False  # broker listener uses TLS; mirrors the client config

    # --- Database ---
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_user: str = "aeris"
    postgres_password: str = "aeris_dev_password"
    postgres_db: str = "aeris"
    database_url: str | None = None  # optional full override (tests use sqlite)

    # --- MQTT ---
    mqtt_host: str = "localhost"
    mqtt_port: int = 1883
    mqtt_username: str | None = None
    mqtt_password: str | None = None
    mqtt_topic_prefix: str = "aeris"
    mqtt_client_id: str = "aeris-backend"

    # --- Security ---
    secret_key: str = "change-me-generate-a-real-secret"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 720

    # --- CORS ---
    cors_origins: str = "http://localhost:3000"  # comma-separated

    # --- Device health ---
    device_offline_after_seconds: int = 60
    device_health_check_interval_seconds: int = 20

    # --- Processing pipeline ---
    # Trailing window (seconds) used for moving averages / rates / std.
    feature_window_seconds: int = 120
    # A temperature channel frozen within this std for this many samples is
    # flagged as a stuck sensor (SENSOR_MALFUNCTION).
    malfunction_min_samples: int = 40
    malfunction_flatline_std: float = 0.05

    # --- Alert fan-out bus (Redis pub/sub) ---
    # When REDIS_URL is set, alerts are published to a Redis channel so out-of-
    # process workers (email/SMS/WhatsApp) can consume them. Without Redis the
    # bus degrades to in-process dispatch — the prototype demo never needs Redis.
    redis_url: str | None = None
    alert_fanout_channel: str = "aeris:alerts"

    # --- Notification channels (email / SMS) ---
    # Both are OFF by default so the prototype never sends anything outbound
    # unless an operator explicitly configures and enables a channel.
    notify_email_enabled: bool = False
    notify_email_min_severity: str = "high"  # warning | high | critical
    notify_email_recipients: str = ""  # comma-separated
    smtp_host: str = "localhost"
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_use_tls: bool = True
    smtp_timeout_seconds: float = 10.0
    notify_email_from: str = "aeris-alerts@example.com"

    notify_sms_enabled: bool = False
    notify_sms_min_severity: str = "critical"
    notify_sms_recipients: str = ""  # comma-separated MSISDNs
    # Provider-agnostic HTTP endpoint receiving a Twilio-compatible payload
    # ({To, From, Body}); swap for your gateway without touching alert logic.
    sms_webhook_url: str | None = None
    sms_auth_token: str | None = None
    notify_sms_from: str = "AERIS"
    sms_timeout_seconds: float = 10.0

    # --- Device authentication ---
    # auto_provision=True keeps the hackathon demo friction-free: an unknown
    # NODE_xx appears in the dashboard on first frame. Set False to require
    # pre-registration (production posture).
    device_auto_provision: bool = True
    # Require a per-device token in the frame (defence in depth for the HTTP
    # telemetry endpoint; the MQTT broker ACL is the primary control).
    device_auth_required: bool = False
    # When set, seeded/provisioned devices receive '<value>-<DEVICE_ID>' as
    # their initial password so a demo can generate broker credentials.
    device_default_password: str | None = None

    # --- ML inference ---
    # Ensemble model (XGBoost + Random Forest + Isolation Forest + LSTM)
    ml_model_path: str = "app/ml/model_store/aeris_ensemble.joblib"
    # Legacy single-model fallback (used if ensemble not found)
    ml_model_legacy_path: str = "app/ml/model_store/aeris_model.joblib"
    # Forward-looking regressor ensemble: predicts the worst risk score
    # inside the horizon below. Missing artifact => trend extrapolation fallback.
    ml_forecast_model_path: str = "app/ml/model_store/aeris_forecast_ensemble.joblib"
    ml_forecast_legacy_path: str = "app/ml/model_store/aeris_forecast.joblib"
    forecast_horizon_minutes: int = 10
    # Minimum forecast-model confidence to report the model method (otherwise
    # the deterministic extrapolation is reported instead).
    forecast_min_confidence: float = 0.5
    # Below this confidence the ML verdict is advisory only (rules dominate).
    ml_min_confidence: float = 0.55
    # ML may only ESCALATE situations the deterministic layer already flags:
    # below this rule score the ML verdict cannot raise the risk at all. This
    # enforces the Level 1/2/3 hierarchy (ML supplements rules, never
    # initiates an alarm from a calm baseline).
    ml_advisory_min_rule_score: float = 20.0

    @property
    def sqlalchemy_database_uri(self) -> str:
        """Async/sync-agnostic SQLAlchemy URL; defaults to local Postgres."""
        if self.database_url:
            return self.database_url
        return (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def email_recipient_list(self) -> list[str]:
        return [r.strip() for r in self.notify_email_recipients.split(",") if r.strip()]

    @property
    def sms_recipient_list(self) -> list[str]:
        return [r.strip() for r in self.notify_sms_recipients.split(",") if r.strip()]

    # ------------------------------------------------------------------
    # Hazard thresholds (shared by simulator realism and the rule engine).
    # MQ-series sensors report arbitrary ADC counts (0-1023 typical) so these
    # limits are expressed in the same units, not ppm. CALIBRATION_REQUIRED
    # before any real deployment.
    # ------------------------------------------------------------------
    hazard_thresholds: dict = {
        "methane": {"warning": 200, "high": 400, "critical": 650},
        "co": {"warning": 50, "high": 90, "critical": 150},
        "smoke": {"warning": 180, "high": 300, "critical": 500},
        "temperature": {"warning": 45.0, "high": 60.0, "critical": 80.0},
        "humidity": {"low": 10.0, "high": 90.0},  # informational
        # Absolute rate-of-change limits (units/minute) for trend detection.
        "rate_of_change": {"temperature": 2.0, "gas": 60.0, "smoke": 40.0},
    }

    # Risk score banding: SAFE / WARNING / HIGH / CRITICAL.
    risk_bands: dict = {
        "safe_max": 30,
        "warning_max": 60,
        "high_max": 80,
        # anything above high_max is CRITICAL (max 100)
    }

    # Weights used by the rule engine to combine per-signal contributions.
    risk_weights: dict = {
        "temperature": 0.20,
        "co": 0.20,
        "methane": 0.30,
        "smoke": 0.20,
        "flame": 0.10,
    }


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
