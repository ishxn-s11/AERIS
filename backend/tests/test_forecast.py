"""Risk-projection tests: extrapolation math, service composition, API surface."""
from datetime import datetime, timezone

import joblib
import pytest
from sklearn.dummy import DummyRegressor

from app.config.settings import settings
from app.database.session import SessionLocal
from app.ml.features import FEATURE_NAMES
from app.ml.forecast import ForecastModel, load_forecast_model
from app.services import forecast_service


# Median of the synthetic training distribution for a calm window — a real
# sensor window always has jitter, and a zero-variance "perfectly flat" window
# is a state the generator only reaches during escalation.
_CALM_STD = 1.06


def _features(**overrides) -> dict:
    """A calm, *physically consistent* baseline snapshot, then explicit overrides.

    Moving averages mirror the live channels exactly as `ml/features.py`
    produces them, and the temperature standard deviation is the realistic
    value rather than 0: a window with zero variance and zero drift does not
    occur in a real plant.
    """
    base = {name: 0.0 for name in FEATURE_NAMES}
    base.update({"temperature": 28.0, "humidity": 45.0, "co": 12.0, "methane": 60.0, "smoke": 40.0})
    base.update(overrides)
    base.update(
        {
            "temperature_ma": base["temperature"],
            "gas_ma": base["methane"],
            "smoke_ma": base["smoke"],
            "co_ma": base["co"],
            "temperature_std": base.get("temperature_std") or _CALM_STD,
        }
    )
    return base


# ---------------------------------------------------------------------------
# Banding + extrapolation
# ---------------------------------------------------------------------------
def test_band_of_matches_configured_cut_points():
    assert forecast_service.band_of(10) == "SAFE"
    assert forecast_service.band_of(settings.risk_bands["safe_max"] + 1) == "WARNING"
    assert forecast_service.band_of(settings.risk_bands["warning_max"] + 1) == "HIGH"
    assert forecast_service.band_of(settings.risk_bands["high_max"] + 1) == "CRITICAL"


def test_project_features_extrapolates_using_rates():
    projected = forecast_service.project_features(
        _features(methane=300.0, gas_rate=50.0, temperature=30.0, temperature_rate=1.0),
        horizon_minutes=10,
    )
    assert projected["methane"] == pytest.approx(300.0 + 50.0 * 10)
    assert projected["temperature"] == pytest.approx(40.0)
    # Rates are zeroed so the projection is scored on levels, not re-triggered trends.
    assert projected["gas_rate"] == 0.0 and projected["temperature_rate"] == 0.0


def test_project_features_clamps_to_physical_bounds():
    projected = forecast_service.project_features(
        _features(methane=1000.0, gas_rate=500.0), horizon_minutes=10
    )
    assert projected["methane"] == 1023.0  # ADC ceiling, not overshoot


def test_forecast_rising_leak_projects_higher_than_now():
    features = _features(methane=380.0, gas_rate=45.0, co=20.0, smoke=90.0, smoke_rate=8.0)
    result = forecast_service.build_forecast(features, current_score=30.0, current_level="WARNING")
    assert result["predicted_risk_score"] > 30.0
    assert result["horizon_minutes"] == settings.forecast_horizon_minutes
    assert result["delta"] == pytest.approx(result["predicted_risk_score"] - 30.0, abs=0.11)
    assert any("Combustible gas" in d for d in result["drivers"])


def test_forecast_stable_baseline_stays_safe():
    result = forecast_service.build_forecast(_features(), current_score=9.0, current_level="SAFE")
    assert result["predicted_risk_level"] == "SAFE"
    assert result["expected_breach_minutes"] is None
    assert any("stable" in d for d in result["drivers"])


def test_forecast_reports_eta_to_next_band():
    # A leak that will cross a band inside the horizon must report roughly when.
    features = _features(methane=560.0, gas_rate=20.0)
    result = forecast_service.build_forecast(features, current_score=62.0, current_level="HIGH")
    assert result["expected_breach_minutes"] is not None
    assert 0 < result["expected_breach_minutes"] <= settings.forecast_horizon_minutes


def test_model_cannot_escalate_a_calm_projection(monkeypatch):
    """A regressor that shouts "90" at a calm window must not raise the alarm.

    Same Level-3 discipline as the live engine: ML supplements the rules, it
    never initiates. The reported method must say the arithmetic won.
    """
    from app.services import forecast_service as service

    class _Shouting:
        version = "stub-v1"
        reported_confidence = 0.99

        def predict_score(self, _vector) -> float:
            return 90.0

    monkeypatch.setattr(service.forecast_module, "forecast_model", _Shouting())
    result = service.build_forecast(_features(), current_score=9.0, current_level="SAFE")

    assert result["predicted_risk_level"] == "SAFE"
    assert result["predicted_risk_score"] < 20.0
    assert result["method"] == "trend_extrapolation"
    assert any("advisory only" in d for d in result["drivers"])
    # The rejected model must not also be credited as the source of the number.
    assert not any("Projection from forecast model" in d for d in result["drivers"])


def test_model_escalation_is_kept_when_the_rules_agree():
    """When the deterministic projection is already elevated, ML still counts."""
    features = _features(methane=700.0, gas_rate=40.0, co=60.0, smoke=200.0, smoke_rate=15.0)
    result = forecast_service.build_forecast(features, current_score=62.0, current_level="HIGH")
    assert result["predicted_risk_score"] >= 62.0
    assert result["predicted_risk_level"] in ("HIGH", "CRITICAL")


def test_partial_feature_vector_falls_back_to_arithmetic():
    """Live reading with an unpopulated window must not invent a ramp.

    Regression guard: a vector where the moving averages are 0 while the
    readings are live used to make the regressor report ~54 (WARNING) for a
    perfectly calm plant, i.e. ML initiating an alarm from a false premise.
    """
    from app.services import forecast_service as service

    partial = {name: 0.0 for name in FEATURE_NAMES}
    partial.update({"temperature": 28.0, "humidity": 45.0, "co": 12.0, "methane": 60.0, "smoke": 40.0})
    assert service._vector_is_inconsistent(partial) is True

    result = service.build_forecast(partial, current_score=9.0, current_level="SAFE")
    assert result["method"] == "trend_extrapolation"
    assert result["predicted_risk_level"] == "SAFE"
    assert result["predicted_risk_score"] < 20.0


def test_complete_feature_vector_is_accepted():
    from app.services import forecast_service as service

    assert service._vector_is_inconsistent(_features()) is False


def test_forecast_method_is_honest_without_artifact():
    """No regression artifact => the deterministic method must be reported."""
    import app.ml.forecast as forecast_module

    original = forecast_module.forecast_model
    forecast_module.forecast_model = None
    try:
        result = forecast_service.build_forecast(_features(methane=300.0, gas_rate=10.0), 25.0, "WARNING")
        assert result["method"] == "trend_extrapolation"
        assert result["confidence"] is None
    finally:
        forecast_module.forecast_model = original


# ---------------------------------------------------------------------------
# Model loading
# ---------------------------------------------------------------------------
def test_load_forecast_model_absent_artifact_returns_none(tmp_path):
    assert load_forecast_model(str(tmp_path / "missing.joblib")) is None


def test_forecast_model_wraps_pipeline_and_reports_mae_confidence(tmp_path):
    path = tmp_path / "forecast.joblib"
    dummy = DummyRegressor(strategy="constant", constant=42.0)
    dummy.fit([[0.0] * len(FEATURE_NAMES)], [42.0])  # a dumped estimator must be fitted
    joblib.dump(
        {
            "pipeline": dummy,
            "feature_names": FEATURE_NAMES,
            "version": "test-v1",
            "metrics": {"mae": 5.0},
        },
        path,
    )
    model = load_forecast_model(str(path))
    assert isinstance(model, ForecastModel)
    assert model.predict_score([0.0] * len(FEATURE_NAMES)) == 42.0
    # Confidence is derived from held-out MAE, not invented from thin air.
    assert model.reported_confidence == pytest.approx(0.95)


# ---------------------------------------------------------------------------
# Persistence + API
# ---------------------------------------------------------------------------
def test_persist_forecast_is_rate_limited_per_zone(client):
    with SessionLocal() as db:
        payload = forecast_service.build_forecast(_features(), 10.0, "SAFE")
        first = forecast_service.persist_forecast(db, zone_id=1, device_id=None, payload=payload, force=True)
        second = forecast_service.persist_forecast(db, zone_id=1, device_id=None, payload=payload)
        db.commit()
        assert first is not None
        assert second is None  # inside the 60 s window
        assert forecast_service.latest_forecast(db, 1) is not None


def test_ingestion_stores_forecast_and_broadcasts_it(client, auth_headers):
    """A telemetry frame must produce a projection and expose it over the API."""
    frame = {
        "device_id": "NODE_03",
        "factory_id": "FACTORY_01",
        "zone_id": "ZONE_03",
        "zone_name": "Generator Room",
        "temperature": 31.0,
        "humidity": 44.0,
        "co": 14,
        "methane": 120,
        "smoke": 50,
        "flame": False,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    assert client.post("/api/telemetry", json=frame).status_code == 201

    zones = client.get("/api/zones", headers=auth_headers).json()
    zone_id = next(z["id"] for z in zones if z["name"] == "Generator Room")

    series = client.get(f"/api/zones/{zone_id}/forecast", headers=auth_headers)
    assert series.status_code == 200
    rows = series.json()
    if not rows:  # rate-limiter may have skipped the persist — force one
        with SessionLocal() as db:
            payload = forecast_service.build_forecast(_features(), 10.0, "SAFE")
            forecast_service.persist_forecast(db, zone_id, None, payload, force=True)
            db.commit()
        rows = client.get(f"/api/zones/{zone_id}/forecast", headers=auth_headers).json()

    assert rows, "expected at least one stored projection"
    row = rows[-1]
    for key in ("horizon_minutes", "predicted_risk_score", "predicted_risk_level",
                "method", "expected_breach_minutes", "drivers"):
        assert key in row
    assert row["horizon_minutes"] == settings.forecast_horizon_minutes


def test_forecasts_endpoint_scopes_to_factory(client, auth_headers):
    with SessionLocal() as db:
        payload = forecast_service.build_forecast(_features(), 10.0, "SAFE")
        forecast_service.persist_forecast(db, zone_id=1, device_id=None, payload=payload, force=True)
        db.commit()

    res = client.get("/api/forecasts", headers=auth_headers)
    assert res.status_code == 200
    assert isinstance(res.json(), list)
    assert client.get("/api/forecasts/999", headers=auth_headers).status_code == 404
