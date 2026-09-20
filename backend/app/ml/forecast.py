"""Forward-looking risk projection (default horizon: +10 minutes).

Two methods, reported honestly via the `method` field:

1. `ml_regressor`  — a trained regressor (or ensemble) that maps the current
   engineered features to the *worst rule-engine risk score inside the horizon*.
   Trained on the SYNTHETIC dataset (see ml/training/generate_dataset.py),
   where the target is computed with the same production scoring function the
   backend runs.
2. `trend_extrapolation` — deterministic fallback used when no artifact is
   loaded: projects the recent robust rates forward and re-scores them with the
   production rule engine. No model, no invented numbers — just arithmetic the
   operator can audit.

Nothing here raises a *real-time* alarm: forecasts are advisory and never feed
the alert thresholds. That keeps Level 1/2 rules the only alarm authority.
"""
from __future__ import annotations

import logging
from typing import Any

import joblib
import numpy as np
import pandas as pd

from app.config.settings import settings
from app.ml.features import FEATURE_NAMES

logger = logging.getLogger("aeris.ml.forecast")

METHOD_MODEL = "ml_regressor"
METHOD_EXTRAPOLATION = "trend_extrapolation"


class ForecastModel:
    """Wrapper around the trained risk-projection regressor(s).

    Supports both single-pipeline mode and ensemble mode.
    """

    def __init__(
        self,
        pipeline: Any | None,
        feature_names: list[str],
        version: str,
        metrics: dict | None = None,
        models: dict | None = None,
        weights: dict | None = None,
    ):
        self.pipeline = pipeline
        self.feature_names = feature_names
        self.version = version
        self.metrics = metrics or {}
        self.models = models or {}
        self.weights = weights or {}

    def predict_score(self, vector: list[float]) -> float:
        """Predict the worst risk score in the horizon.

        Uses ensemble if available, otherwise single pipeline.
        """
        X = pd.DataFrame([vector], columns=self.feature_names)

        # Ensemble mode
        if self.models:
            return self._predict_ensemble(X)

        # Legacy single-pipeline mode
        if self.pipeline is None:
            return 0.0

        value = float(self.pipeline.predict(X)[0])
        return max(0.0, min(100.0, value))

    def _predict_ensemble(self, X: pd.DataFrame) -> float:
        """Weighted ensemble prediction across multiple regressors."""
        predictions = {}

        for name, model in self.models.items():
            try:
                pred = float(model.predict(X.values)[0])
                predictions[name] = max(0.0, min(100.0, pred))
            except Exception as e:
                logger.warning("Forecast model %s failed: %s", name, e)
                continue

        if not predictions:
            return 0.0

        # Weighted average
        total_weight = sum(self.weights.get(name, 1.0) for name in predictions)
        weighted_sum = sum(
            predictions[name] * self.weights.get(name, 1.0)
            for name in predictions
        )

        return max(0.0, min(100.0, weighted_sum / total_weight))

    @property
    def reported_confidence(self) -> float:
        """Confidence proxy for the dashboard.

        Derived from held-out error (1 - MAE/100), not from a probability — a
        regressor has no class probability to report, and inventing one would
        mislead an operator.
        """
        mae = float(self.metrics.get("mae", 0.0) or 0.0)
        return max(0.0, min(1.0, 1.0 - mae / 100.0))


# Populated by load_forecast_model() during app startup (see app/ml/inference.py).
forecast_model: ForecastModel | None = None


def load_forecast_model(path: str | None = None) -> ForecastModel | None:
    """Load the forecast artifact and publish it to `forecast_model`.

    Supports both single-pipeline and ensemble artifacts. Falls back to
    legacy path if the primary is missing.

    Returns None (leaving the global as None) when the artifact is absent, in
    which case the service falls back to trend extrapolation.
    """
    global forecast_model
    model_path = path or settings.ml_forecast_model_path
    try:
        payload = joblib.load(model_path)
    except FileNotFoundError:
        # Try legacy fallback
        if not path and settings.ml_forecast_legacy_path != settings.ml_forecast_model_path:
            try:
                payload = joblib.load(settings.ml_forecast_legacy_path)
                logger.info("Using legacy forecast model: %s", settings.ml_forecast_legacy_path)
            except FileNotFoundError:
                logger.warning("No forecast model at %s or legacy — using trend extrapolation", model_path)
                forecast_model = None
                return None
        else:
            logger.warning("No forecast model at %s — using trend extrapolation", model_path)
            forecast_model = None
            return None
    except Exception:  # noqa: BLE001 — a corrupt artifact must not stop the backend
        logger.exception("Failed to load forecast model at %s — using extrapolation", model_path)
        forecast_model = None
        return None

    # Check if this is an ensemble artifact
    if "models" in payload and payload["models"]:
        model = ForecastModel(
            pipeline=None,
            feature_names=payload.get("feature_names", FEATURE_NAMES),
            version=payload.get("version", "unknown"),
            metrics=payload.get("metrics"),
            models=payload["models"],
            weights=payload.get("weights", {}),
        )
    else:
        # Legacy single-pipeline artifact
        model = ForecastModel(
            pipeline=payload.get("pipeline"),
            feature_names=payload.get("feature_names", FEATURE_NAMES),
            version=payload.get("version", "unknown"),
            metrics=payload.get("metrics"),
        )

    forecast_model = model
    logger.info(
        "Forecast model loaded: %s (MAE %.2f, horizon %s min)",
        model.version,
        float(model.metrics.get("mae", 0.0) or 0.0),
        payload.get("horizon_minutes", settings.forecast_horizon_minutes),
    )
    return model
