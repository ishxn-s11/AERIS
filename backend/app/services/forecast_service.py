"""Zone risk projection service (10-minute horizon by default).

Composition rules:
- The regressor supplies the number when an artifact is loaded AND its reported
  confidence clears `settings.forecast_min_confidence`; otherwise the auditable
  trend extrapolation is used and `method` says so.
- The projected *hazard* always comes from re-scoring the projected snapshot
  with the production rule engine, so hazard naming stays consistent with the
  live pipeline.
- Forecasts are persisted at most once per `persist_interval` per zone; the
  live value is still broadcast on every frame.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.settings import settings
from app.ml import forecast as forecast_module
from app.ml.features import FEATURE_NAMES
from app.ml.risk_engine import evaluate, evaluate_rules
from app.models.models import RiskForecast

logger = logging.getLogger("aeris.forecast")

# Physical bounds used to keep projections plausible (mirrors TelemetryIn).
_BOUNDS = {
    "temperature": (-50.0, 150.0),
    "humidity": (0.0, 100.0),
    "co": (0.0, 1023.0),
    "methane": (0.0, 1023.0),
    "smoke": (0.0, 1023.0),
}

_BAND_ORDER = {"SAFE": 0, "WARNING": 1, "HIGH": 2, "CRITICAL": 3}

_last_persisted: dict[int, datetime] = {}
PERSIST_INTERVAL_SECONDS = 60


def _clamp(channel: str, value: float) -> float:
    low, high = _BOUNDS[channel]
    return max(low, min(high, value))


def band_of(score: float) -> str:
    """Same cut points as the live engine (single source: settings.risk_bands)."""
    b = settings.risk_bands
    if score <= b["safe_max"]:
        return "SAFE"
    if score <= b["warning_max"]:
        return "WARNING"
    if score <= b["high_max"]:
        return "HIGH"
    return "CRITICAL"


def project_features(features: dict, horizon_minutes: float) -> dict:
    """Extrapolate the measured channels forward using their robust rates.

    Documented approximation: CO has no independent robust rate in the feature
    contract (MQ-7 drifts with the same ambient as the combustible channel), so
    it is projected at the combustible channel's relative growth. Flame is
    instantaneous and is carried over unchanged — it cannot be "predicted" by
    arithmetic, only detected.
    """
    projected = dict(features)
    for channel, rate_key in (
        ("temperature", "temperature_rate"),
        ("methane", "gas_rate"),
        ("smoke", "smoke_rate"),
    ):
        rate = float(features.get(rate_key, 0.0) or 0.0)
        projected[channel] = _clamp(channel, float(features.get(channel, 0.0) or 0.0) + rate * horizon_minutes)

    gas_now = float(features.get("methane", 0.0) or 0.0)
    if gas_now > 0:
        growth = projected["methane"] / gas_now
        projected["co"] = _clamp("co", float(features.get("co", 0.0) or 0.0) * growth)
    projected["humidity"] = float(features.get("humidity", 0.0) or 0.0)
    projected["flame"] = float(features.get("flame", 0.0) or 0.0)

    # Rate features are zeroed for the projection pass: trends already happened,
    # scoring them again would double-count the escalation.
    for rate_key in ("temperature_rate", "gas_rate", "smoke_rate"):
        projected[rate_key] = 0.0
    return projected


def _extrapolated_score(features: dict, horizon_minutes: float) -> tuple[float, list[str]]:
    score, factors, _forced = evaluate_rules(project_features(features, horizon_minutes))
    return score, factors


def _breach_eta(features: dict, current_level: str, horizon_minutes: float) -> float | None:
    """Minutes until the projected score opens the next risk band, if it does."""
    current_rank = _BAND_ORDER.get(current_level, 0)
    steps = int(horizon_minutes * 4)  # quarter-minute resolution
    for i in range(1, steps + 1):
        minutes = i / 4.0
        score, _ = _extrapolated_score(features, minutes)
        if _BAND_ORDER[band_of(score)] > current_rank:
            return round(minutes, 1)
    return None


# Base channel for each moving-average feature, used to detect an inconsistent
# (partial) feature vector. A window mean of 0 next to a live reading of 60 ADC
# counts is physically impossible — it means the window was not populated, and
# handing that to the regressor makes it extrapolate a phantom ramp (the model
# reads "level high, history zero" as explosive growth).
_MA_SOURCE = {
    "temperature_ma": "temperature",
    "gas_ma": "methane",
    "smoke_ma": "smoke",
    "co_ma": "co",
}


def _vector_is_inconsistent(features: dict) -> bool:
    """True when a moving-average feature is missing while its live reading is not."""
    for ma_key, base_key in _MA_SOURCE.items():
        if ma_key not in features:
            continue
        if float(features.get(ma_key, 0.0) or 0.0) == 0.0 and float(features.get(base_key, 0.0) or 0.0) > 1.0:
            return True
    return False


def _model_forecast(features: dict) -> tuple[float, float, str] | None:
    """(predicted_score, confidence, version) from the trained regressor, if usable."""
    model = forecast_module.forecast_model
    if model is None:
        return None
    if not set(_MA_SOURCE).issubset(features):
        # Legacy/partial snapshot: the regressor's contract is not met, so the
        # auditable arithmetic is the only honest answer.
        logger.warning("Forecast model skipped: feature vector is missing window aggregates")
        return None
    if _vector_is_inconsistent(features):
        logger.warning("Forecast model skipped: window aggregates absent while readings are live")
        return None
    vector = [float(features.get(name, 0.0) or 0.0) for name in FEATURE_NAMES]
    try:
        score = model.predict_score(vector)
    except Exception:  # noqa: BLE001 — a bad vector must not break ingestion
        logger.exception("Forecast model inference failed — falling back to extrapolation")
        return None
    confidence = model.reported_confidence
    if confidence < settings.forecast_min_confidence:
        return None
    return score, confidence, model.version


def build_forecast(
    features: dict,
    current_score: float,
    current_level: str,
    horizon_minutes: int | None = None,
) -> dict:
    """Full projection payload for one zone snapshot."""
    horizon = horizon_minutes or settings.forecast_horizon_minutes
    projected = project_features(features, horizon)
    projected_eval = evaluate(projected)  # hazard naming + raised factors
    extrapolated_score, extrapolated_factors = _extrapolated_score(features, horizon)

    drivers: list[str] = []
    model_result = _model_forecast(features)
    if model_result is not None:
        score, confidence, version = model_result
        method = forecast_module.METHOD_MODEL
        model_driver = f"Projection from forecast model {version} (+{horizon} min horizon)"
        # Report the upper envelope: a projection must never make a worsening
        # situation look calmer than the auditable arithmetic suggests.
        if extrapolated_score > score:
            drivers.append(
                f"Trend extrapolation is higher ({extrapolated_score:.0f} vs {score:.0f}) — using it"
            )
            score, method, confidence = extrapolated_score, forecast_module.METHOD_EXTRAPOLATION, None
        elif score > current_score and extrapolated_score < settings.ml_advisory_min_rule_score:
            # Level-3 discipline, applied to the projection: the regressor may
            # only *escalate* a future the rules already see escalating. Without
            # this, one out-of-distribution vector (an unpopulated window, a
            # sensor swapping scale) puts "will breach in 10 min" on a calm zone
            # — the same false-alarm class the live engine forbids.
            drivers.append(
                f"ML projection {score:.0f} rejected — rules see a calm "
                f"{extrapolated_score:.0f}, ML is advisory only"
            )
            score, method, confidence = (
                max(current_score, extrapolated_score),
                forecast_module.METHOD_EXTRAPOLATION,
                None,
            )
        else:
            drivers.append(model_driver)
            score = max(score, current_score)
    else:
        score = extrapolated_score
        confidence = None
        method = forecast_module.METHOD_EXTRAPOLATION

    score = round(min(100.0, max(0.0, score)), 1)
    level = band_of(score)

    drivers.extend(_describe_drivers(features, horizon))
    drivers.extend(extrapolated_factors[:2] if method == forecast_module.METHOD_EXTRAPOLATION else [])

    return {
        "horizon_minutes": horizon,
        "predicted_risk_score": score,
        "predicted_risk_level": level,
        "predicted_hazard": projected_eval.get("predicted_hazard"),
        "confidence": confidence,
        "method": method,
        "expected_breach_minutes": _breach_eta(features, current_level, horizon),
        "drivers": drivers[:5],
        "delta": round(score - current_score, 1),
    }


def _describe_drivers(features: dict, horizon: int) -> list[str]:
    """Human-readable reasons the projection moves, in engineering units."""
    out: list[str] = []
    gas_rate = float(features.get("gas_rate", 0.0) or 0.0)
    temp_rate = float(features.get("temperature_rate", 0.0) or 0.0)
    smoke_rate = float(features.get("smoke_rate", 0.0) or 0.0)
    if abs(gas_rate) > 1.0:
        out.append(
            f"Combustible gas {gas_rate:+.0f}/min → {features.get('methane', 0) + gas_rate * horizon:.0f} in {horizon} min"
        )
    if abs(temp_rate) > 0.1:
        out.append(
            f"Temperature {temp_rate:+.1f}°C/min → {features.get('temperature', 0) + temp_rate * horizon:.1f}°C in {horizon} min"
        )
    if abs(smoke_rate) > 1.0:
        out.append(
            f"Smoke {smoke_rate:+.0f}/min → {features.get('smoke', 0) + smoke_rate * horizon:.0f} in {horizon} min"
        )
    if not out:
        out.append("Channels stable — no material drift detected in the window")
    return out


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------
def persist_forecast(
    db: Session, zone_id: int, device_id: int | None, payload: dict, force: bool = False
) -> RiskForecast | None:
    """Store a forecast, rate-limited per zone unless `force`."""
    now = datetime.now(timezone.utc)
    last = _last_persisted.get(zone_id)
    if not force and last is not None and now - last < timedelta(seconds=PERSIST_INTERVAL_SECONDS):
        return None
    _last_persisted[zone_id] = now
    row = RiskForecast(
        zone_id=zone_id,
        device_id=device_id,
        timestamp=now,
        horizon_minutes=payload["horizon_minutes"],
        predicted_risk_score=payload["predicted_risk_score"],
        predicted_risk_level=payload["predicted_risk_level"],
        predicted_hazard=payload["predicted_hazard"],
        confidence=payload["confidence"],
        method=payload["method"],
        expected_breach_minutes=payload["expected_breach_minutes"],
        drivers=json.dumps(payload["drivers"]),
    )
    db.add(row)
    return row


def latest_forecast(db: Session, zone_id: int) -> RiskForecast | None:
    return db.scalar(
        select(RiskForecast)
        .where(RiskForecast.zone_id == zone_id)
        .order_by(RiskForecast.timestamp.desc())
        .limit(1)
    )


def forecast_series(db: Session, zone_id: int, limit: int = 60) -> list[RiskForecast]:
    rows = db.scalars(
        select(RiskForecast)
        .where(RiskForecast.zone_id == zone_id)
        .order_by(RiskForecast.timestamp.desc())
        .limit(limit)
    ).all()
    return list(reversed(rows))


def forecast_to_dict(row: RiskForecast) -> dict:
    return {
        "id": row.id,
        "zone_id": row.zone_id,
        "device_id": row.device_id,
        "timestamp": row.timestamp,
        "horizon_minutes": row.horizon_minutes,
        "predicted_risk_score": row.predicted_risk_score,
        "predicted_risk_level": row.predicted_risk_level,
        "predicted_hazard": row.predicted_hazard,
        "confidence": row.confidence,
        "method": row.method,
        "expected_breach_minutes": row.expected_breach_minutes,
        "drivers": json.loads(row.drivers) if row.drivers else [],
    }
