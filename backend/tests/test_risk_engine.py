"""Phase 2 tests: feature extraction, risk engine, alert generation, ML contract."""
from datetime import datetime, timedelta, timezone

import pytest

from app.ml.features import ReadingSample, compute_features
from app.ml.risk_engine import _band, evaluate
from app.ml.features import feature_store
from app.services.telemetry_service import _detect_sensor_malfunction

NOW = datetime.now(timezone.utc)


def make_history(methane: float = 60, temperature: float = 28.0, smoke: float = 40,
                 co: float = 12, flame: bool = False, n: int = 6, step_seconds: int = 30,
                 methane_ramp: float = 0.0, temp_ramp: float = 0.0):
    """Build a synthetic history list ending at the target values."""
    samples = []
    for i in range(n):
        samples.append(ReadingSample(
            timestamp=NOW - timedelta(seconds=step_seconds * (n - 1 - i)),
            temperature=temperature - temp_ramp * (n - 1 - i) * step_seconds / 60.0,
            humidity=45.0,
            co=co,
            methane=methane - methane_ramp * (n - 1 - i) * step_seconds / 60.0,
            smoke=smoke,
            flame=flame,
        ))
    return samples


# ---------------------------------------------------------------------------
# Risk banding (configured cut points)
# ---------------------------------------------------------------------------
def test_band_boundaries_match_configuration():
    b = __import__("app.config.settings", fromlist=["settings"]).settings.risk_bands
    assert _band(0) == "SAFE"
    assert _band(b["safe_max"]) == "SAFE"
    assert _band(b["safe_max"] + 1) == "WARNING"
    assert _band(b["warning_max"]) == "WARNING"
    assert _band(b["warning_max"] + 1) == "HIGH"
    assert _band(b["high_max"]) == "HIGH"
    assert _band(b["high_max"] + 1) == "CRITICAL"
    assert _band(100) == "CRITICAL"


# ---------------------------------------------------------------------------
# Level 1 — hard safety rules
# ---------------------------------------------------------------------------
def test_baseline_is_safe():
    result = evaluate(compute_features(make_history()))
    assert result["risk_level"] == "SAFE"
    assert result["risk_score"] <= 30


def test_critical_methane_forces_critical():
    features = compute_features(make_history(methane=700))
    result = evaluate(features)
    assert result["risk_level"] == "CRITICAL"
    assert result["predicted_hazard"] == "gas_leak"
    assert any("Methane beyond critical" in f for f in result["explanation"])


def test_flame_forces_critical():
    features = compute_features(make_history(flame=True))
    result = evaluate(features)
    assert result["risk_level"] == "CRITICAL"
    assert any("FLAME DETECTED" in f for f in result["explanation"])


def test_co_critical_forces_critical():
    features = compute_features(make_history(co=200))
    result = evaluate(features)
    assert result["risk_level"] == "CRITICAL"


# ---------------------------------------------------------------------------
# Level 2 — trend analysis
# ---------------------------------------------------------------------------
def test_rapid_gas_rise_escalates_to_high():
    # 90 counts/min ramp over a 4.5-min window -> robust rate ~70/min > 60/min.
    features = compute_features(make_history(methane=450, methane_ramp=90.0, n=10))
    result = evaluate(features)
    assert result["risk_level"] in ("HIGH", "CRITICAL")
    assert any("Gas rising rapidly" in f for f in result["explanation"])


def test_rapid_temperature_rise_flags_trend():
    # +3 C/min > configured 2.0 C/min threshold.
    features = compute_features(make_history(temperature=55, temp_ramp=3.0, n=10))
    result = evaluate(features)
    assert any("Temperature rising rapidly" in f for f in result["explanation"])
    assert result["risk_level"] != "SAFE"


def test_slow_drift_does_not_trigger_trend():
    # +0.5 counts/min: far below the 60/min gas threshold.
    features = compute_features(make_history(methane=70, methane_ramp=0.5, n=10))
    result = evaluate(features)
    assert not any("Gas rising rapidly" in f for f in result["explanation"])


# ---------------------------------------------------------------------------
# Sensor malfunction detection
# ---------------------------------------------------------------------------
def test_flatline_all_channels_is_malfunction():
    frozen = [ReadingSample(timestamp=NOW - timedelta(seconds=i), temperature=77.7,
                            humidity=45.0, co=12, methane=60, smoke=40, flame=False)
              for i in range(50)]
    device = type("D", (), {"device_id": "NODE_X", "status": "online"})()
    assert _detect_sensor_malfunction(frozen, device) is True


def test_natural_noise_is_not_malfunction():
    varied = [ReadingSample(timestamp=NOW - timedelta(seconds=i), temperature=28.0 + (i % 5),
                            humidity=45.0, co=12 + (i % 3), methane=60 + (i % 7),
                            smoke=40 + (i % 4), flame=False)
              for i in range(50)]
    device = type("D", (), {"device_id": "NODE_X", "status": "online"})()
    assert _detect_sensor_malfunction(varied, device) is False


# ---------------------------------------------------------------------------
# Full pipeline: prediction + alert persistence (rules-only mode)
# ---------------------------------------------------------------------------
def test_ingestion_creates_prediction_and_alert():
    from app.database.session import SessionLocal
    from app.models.models import Alert, RiskPrediction
    from app.schemas.schemas import TelemetryIn
    from app.services.telemetry_service import ingest_telemetry

    frame = TelemetryIn(
        device_id="NODE_TEST_RISK", factory_id="FACTORY_01", zone_id="ZONE_02",
        zone_name="Chemical Storage", temperature=34.0, humidity=44.0,
        co=20, methane=720, smoke=90, flame=False,
        timestamp=datetime.now(timezone.utc),
    )
    with SessionLocal() as db:
        reading = ingest_telemetry(db, frame)
        assert reading.id is not None

        zone_id = reading.device.zone_id
        prediction = db.query(RiskPrediction).filter(
            RiskPrediction.zone_id == zone_id).order_by(RiskPrediction.id.desc()).first()
        assert prediction is not None
        assert prediction.risk_level == "CRITICAL"
        assert prediction.risk_score >= 80
        assert prediction.explanation  # explainability payload present

        alert = db.query(Alert).filter(
            Alert.device_id == reading.device_id, Alert.hazard_type == "gas_leak"
        ).order_by(Alert.id.desc()).first()
        assert alert is not None
        assert alert.severity == "critical"
        assert "Chemical Storage" in alert.message


# ---------------------------------------------------------------------------
# ML contract (skipped automatically if no artifact has been trained yet)
# ---------------------------------------------------------------------------
import os  # noqa: E402

MODEL_PATH = os.path.join(os.path.dirname(__file__), "..", "app", "ml", "model_store", "aeris_model.joblib")


@pytest.mark.skipif(not os.path.exists(MODEL_PATH), reason="model artifact not trained yet")
def test_ml_model_loads_and_predicts_valid_format():
    from app.ml.inference import load_model
    from app.ml.features import FEATURE_NAMES

    model = load_model(os.path.abspath(MODEL_PATH))
    assert model is not None
    features = compute_features(make_history(methane=500, methane_ramp=80.0, n=10))
    vector = [float(features[name]) for name in FEATURE_NAMES]
    label, confidence = model.predict_label(vector)
    assert isinstance(label, str) and label
    assert 0.0 <= confidence <= 1.0
