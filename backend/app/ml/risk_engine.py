"""AERIS hybrid risk engine.

Evaluation hierarchy (documented engineering principle #33):

  Level 1 — HARD SAFETY RULES: immediate known dangers (flame, critical gas).
            These alone can force CRITICAL regardless of ML.
  Level 2 — TREND ANALYSIS: rate-of-change and threshold-band escalation.
  Level 3 — ML PREDICTION: pattern recognition that supplements, never
            replaces, the rules.

Output: fused risk score 0-100, risk level band, predicted hazard type, and a
human-readable explanation list. All thresholds/weights come from
`settings.hazard_thresholds` / `risk_bands` / `risk_weights` — nothing is
hard-coded here, so limits can be re-tuned per deployment without code changes.
"""
from __future__ import annotations

from app.config.settings import settings
from app.ml.features import FEATURE_NAMES

SIGNALS = ("methane", "co", "smoke", "temperature")


def _threshold(channel: str, level: str) -> float:
    return float(settings.hazard_thresholds[channel][level])


def _band(score: float) -> str:
    """Map a 0-100 score to a risk band using configured cut points."""
    b = settings.risk_bands
    if score <= b["safe_max"]:
        return "SAFE"
    if score <= b["warning_max"]:
        return "WARNING"
    if score <= b["high_max"]:
        return "HIGH"
    return "CRITICAL"


def _band_floor(level: str) -> float:
    """Lowest possible score of a band — used when hard rules force escalation."""
    return {"SAFE": 0.0, "WARNING": settings.risk_bands["safe_max"] + 1,
            "HIGH": settings.risk_bands["warning_max"] + 1,
            "CRITICAL": settings.risk_bands["high_max"] + 1}[level]


def _score_channel(signal: str, value: float, factors: list[str]) -> float:
    """0-100 score for one signal against its configured warning/high/critical
    thresholds (linear interpolation between bands)."""
    warning = _threshold(signal, "warning")
    high = _threshold(signal, "high")
    critical = _threshold(signal, "critical")
    if value <= warning:
        score = 25.0 * (value / warning) if warning > 0 else 0.0
    elif value <= high:
        score = 25.0 + 35.0 * ((value - warning) / (high - warning))
    elif value <= critical:
        score = 60.0 + 30.0 * ((value - high) / (critical - high))
    else:
        overshoot = min(10.0, (value - critical) / critical * 10.0) if critical > 0 else 10.0
        score = 90.0 + overshoot
    if value > high:
        factors.append(f"{signal} above HIGH threshold ({value:.0f} > {high:.0f})")
    elif value > warning:
        factors.append(f"{signal} above warning threshold ({value:.0f} > {warning:.0f})")
    return min(100.0, score)


def evaluate_rules(features: dict) -> tuple[float, list[str], str | None]:
    """Level 1 + Level 2: weighted channel scores, hard rules, trend analysis.

    Returns (score, contributing_factors, forced_level_or_None).
    """
    factors: list[str] = []
    weights = settings.risk_weights
    total_weight = sum(weights[s] for s in SIGNALS)

    weighted = 0.0
    for signal in SIGNALS:
        value = features.get(signal, 0.0)
        weighted += weights[signal] * _score_channel(signal, value, factors)
    score = weighted / total_weight if total_weight else 0.0

    forced_level: str | None = None

    # --- Level 1: hard safety rules (immediate known dangers) -------------
    if features.get("flame", 0.0) >= 1.0:
        forced_level = "CRITICAL"
        score = max(score, 95.0)
        factors.append("FLAME DETECTED — immediate ignition source present")
    if features.get("methane", 0.0) > _threshold("methane", "critical"):
        forced_level = "CRITICAL"
        score = max(score, 90.0)
        factors.append("Methane beyond critical limit — explosion risk")
    if features.get("co", 0.0) > _threshold("co", "critical"):
        forced_level = "CRITICAL"
        score = max(score, 90.0)
        factors.append("CO beyond critical limit — toxic atmosphere")
    if features.get("smoke", 0.0) > _threshold("smoke", "critical"):
        forced_level = forced_level or "CRITICAL"
        score = max(score, 88.0)
        factors.append("Smoke beyond critical limit — active combustion likely")

    # --- Level 2: trend analysis (deteriorating conditions) ---------------
    roc = settings.hazard_thresholds["rate_of_change"]
    temp_rate = features.get("temperature_rate", 0.0)
    gas_rate = features.get("gas_rate", 0.0)
    smoke_rate = features.get("smoke_rate", 0.0)

    if gas_rate > roc["gas"]:
        score = max(score, _band_floor("HIGH"))
        factors.append(f"Gas rising rapidly ({gas_rate:+.0f}/min)")
    if temp_rate > roc["temperature"]:
        score = max(score, _band_floor("WARNING"))
        factors.append(f"Temperature rising rapidly ({temp_rate:+.1f}°C/min)")
    if smoke_rate > roc["smoke"]:
        score = max(score, _band_floor("WARNING"))
        factors.append(f"Smoke rising rapidly ({smoke_rate:+.0f}/min)")

    # Combined fire signature: smoke up AND temperature up together.
    if smoke_rate > roc["smoke"] * 0.6 and temp_rate > roc["temperature"] * 0.6:
        forced_level = forced_level or "HIGH"
        score = max(score, _band_floor("HIGH"))
        factors.append("Smoke AND temperature rising together — fire signature")

    return min(100.0, score), factors, forced_level


def evaluate_ml(features: dict) -> tuple[str | None, float, float, list[str]]:
    """Level 3: ML verdict. Returns (hazard_label, confidence, ml_score, factors).

    ml_score maps the model's hazard probability to a 0-100 contribution.
    Returns (None, 0, 0, []) when no model is loaded (rules-only mode).
    """
    from app.ml.inference import hazard_model  # local import avoids a cycle

    model = hazard_model
    if model is None:
        return None, 0.0, 0.0, []

    vector = [float(features.get(name, 0.0)) for name in FEATURE_NAMES]
    label, confidence = model.predict_label(vector)
    if confidence < settings.ml_min_confidence:
        return label, confidence, 0.0, ["ML confidence below advisory threshold — ignored"]

    factors: list[str] = []
    if label in ("GAS_LEAK", "CRITICAL") and features.get("gas_rate", 0.0) > 0:
        factors.append(f"ML pattern match: {label} ({confidence:.0%} confidence)")
    elif label == "FIRE_RISK" and features.get("smoke_rate", 0.0) > 0:
        factors.append(f"ML pattern match: {label} ({confidence:.0%} confidence)")
    return label, confidence, confidence * 100.0, factors# ML verdict classes that describe a situation rather than a hazard type;
# they must not leak into predicted_hazard (a HazardType value).
_SITUATIONAL_ML_LABELS = {"SAFE", "WARNING", "CRITICAL"}


def fuse(rule_score: float, forced_level: str | None,
         ml_label: str | None, ml_score: float, ml_confidence: float) -> tuple[float, str, str | None]:
    """Combine rule score with the (advisory) ML score.

    Rules dominate: ML can nudge a score upward within/between bands, but only
    a hard rule can force CRITICAL. This keeps false alarms governed by the
    deterministic layer — the layer that is auditable.

    Returns (score, level, ml_hazard_or_None). The ML class is only forwarded
    as a hazard when it names an actual hazard type (FIRE_RISK -> fire,
    GAS_LEAK -> gas_leak); situational classes (SAFE/WARNING/CRITICAL) fall
    through to the rule-derived hazard mapping in evaluate()."""
    score = rule_score
    ml_allowed = (
        ml_score > 0
        and ml_confidence >= settings.ml_min_confidence
        # Level 3 supplements Levels 1-2: ML escalates elevated situations,
        # it never initiates an alarm from a calm deterministic baseline.
        and rule_score >= settings.ml_advisory_min_rule_score
    )
    if ml_allowed:
        # Blend at most 30% ML influence, only if it pushes upward.
        blended = rule_score * 0.7 + ml_score * 0.3
        score = max(score, blended)

    level = forced_level or _band(score)
    # A CRITICAL-forcing rule wins over any band the blend suggests.
    if forced_level == "CRITICAL":
        level = "CRITICAL"

    ml_hazard = None
    if level != "SAFE" and ml_label is not None and ml_label not in _SITUATIONAL_ML_LABELS:
        ml_hazard = "fire" if ml_label == "FIRE_RISK" else ml_label.lower()
    return round(min(100.0, score), 1), level, ml_hazard


def evaluate(features: dict) -> dict:
    """Full pipeline evaluation for one device snapshot.

    Returns a dict with score, level, hazard, confidence, factors and the
    per-level breakdown (kept for explainability and tests).
    """
    rule_score, factors, forced = evaluate_rules(features)
    ml_label, ml_conf, ml_score, ml_factors = evaluate_ml(features)
    factors = factors + ml_factors

    score, level, hazard = fuse(rule_score, forced, ml_label, ml_score, ml_conf)

    if hazard is None and level != "SAFE":
        # Rule-derived fallback so every alert carries a hazard type.
        if features.get("flame", 0.0) >= 1.0 or features.get("smoke", 0.0) > _threshold("smoke", "high"):
            hazard = "fire"
        elif features.get("methane", 0.0) > _threshold("methane", "warning") \
                or features.get("gas_rate", 0.0) > settings.hazard_thresholds["rate_of_change"]["gas"]:
            hazard = "gas_leak"
        elif features.get("temperature", 0.0) > _threshold("temperature", "high") \
                or features.get("temperature_rate", 0.0) > settings.hazard_thresholds["rate_of_change"]["temperature"]:
            hazard = "overheating"
        elif features.get("co", 0.0) > _threshold("co", "warning"):
            hazard = "smoke"
        else:
            hazard = "general"

    return {
        "risk_score": score,
        "risk_level": level,
        "predicted_hazard": hazard,
        "model_confidence": round(ml_conf, 3) if ml_conf else None,
        "explanation": factors,
        "breakdown": {
            "rule_score": round(rule_score, 1),
            "ml_score": round(ml_score, 1),
            "forced_level": forced,
        },
    }
