"""Model loading and inference (joblib-backed pipeline).

Supports both legacy single-model artifacts and the new ensemble artifacts
containing XGBoost, Random Forest, Isolation Forest, and LSTM.

The trained artifact contains the scaler + classifier + feature order, so the
backend only depends on this wrapper — swapping models never touches API code.
Loaded once at startup; if the artifact is missing the backend runs in
rules-only mode (safety hierarchy degrades gracefully rather than crashing).
"""
from __future__ import annotations

import logging
from typing import Any

import joblib
import numpy as np
import pandas as pd

from app.config.settings import settings

logger = logging.getLogger("aeris.ml")


class HazardModel:
    """Thin wrapper exposing a stable predict interface for the risk engine.

    Supports both single-pipeline mode (legacy) and ensemble mode.
    """

    def __init__(
        self,
        pipeline: Any | None,
        feature_names: list[str],
        version: str,
        metrics: dict | None = None,
        ensemble: dict | None = None,
        label_encoder: Any | None = None,
        classes: list[str] | None = None,
        weights: dict | None = None,
    ):
        self.pipeline = pipeline
        self.feature_names = feature_names
        self.version = version
        self.metrics = metrics or {}
        self.ensemble = ensemble or {}
        self.label_encoder = label_encoder
        self.classes = classes or []
        self.weights = weights or {}

    def predict_label(self, vector: list[float]) -> tuple[str, float]:
        """Return (class_label, confidence) for one feature vector.

        If an ensemble is available, uses weighted voting; otherwise falls
        back to the single pipeline.
        """
        X = pd.DataFrame([vector], columns=self.feature_names)

        # Ensemble mode
        if self.ensemble:
            return self._predict_ensemble(X)

        # Legacy single-pipeline mode
        if self.pipeline is None:
            return "SAFE", 0.0

        proba = self.pipeline.predict_proba(X)[0]
        idx = int(np.argmax(proba))
        label = str(self.pipeline.classes_[idx])
        return label, float(proba[idx])

    def _predict_ensemble(self, X: pd.DataFrame) -> tuple[str, float]:
        """Weighted ensemble prediction across multiple models."""
        predictions = {}
        confidences = {}

        for name, model in self.ensemble.items():
            try:
                if name == "isolation_forest":
                    # Isolation Forest provides anomaly scores, not class probs
                    scores = model.decision_function(X.values)
                    anomaly = 1.0 / (1.0 + np.exp(scores[0]))  # Sigmoid
                    predictions[name] = 1 if anomaly > 0.6 else 0  # 1=anomaly
                    confidences[name] = anomaly
                elif name == "lstm":
                    # LSTM needs sequence input - skip for single frame
                    continue
                else:
                    proba = model.predict_proba(X.values)[0]
                    idx = int(np.argmax(proba))
                    predictions[name] = idx
                    confidences[name] = float(proba[idx])
            except Exception as e:
                logger.warning("Model %s prediction failed: %s", name, e)
                continue

        if not predictions:
            return "SAFE", 0.0

        # Weighted vote
        total_weight = sum(self.weights.get(name, 1.0) for name in predictions)
        weighted_votes = np.zeros(len(self.classes))

        for name, pred_idx in predictions.items():
            if isinstance(pred_idx, int) and pred_idx < len(self.classes):
                weight = self.weights.get(name, 1.0)
                weighted_votes[pred_idx] += weight

        final_idx = int(np.argmax(weighted_votes))
        confidence = float(weighted_votes[final_idx] / total_weight)

        return self.classes[final_idx], confidence


def load_model(path: str | None = None) -> HazardModel | None:
    """Load the trained model artifact; None if absent (rules-only mode).

    Supports both single-pipeline and ensemble artifacts. Falls back to
    legacy model path if the primary path is missing.
    """
    model_path = path or settings.ml_model_path
    try:
        payload = joblib.load(model_path)
    except FileNotFoundError:
        # Try legacy fallback
        if not path and settings.ml_model_legacy_path != settings.ml_model_path:
            try:
                payload = joblib.load(settings.ml_model_legacy_path)
                logger.info("Using legacy model: %s", settings.ml_model_legacy_path)
            except FileNotFoundError:
                logger.warning("No ML model at %s or legacy — running in rules-only mode", model_path)
                return None
        else:
            logger.warning("No ML model at %s — running in rules-only mode", model_path)
            return None
    except Exception:  # noqa: BLE001 — a corrupt artifact must not stop the backend
        logger.exception("Failed to load ML model at %s — rules-only mode", model_path)
        return None

    # Check if this is an ensemble artifact
    if "ensemble" in payload or "models" in payload:
        ensemble = payload.get("models", payload.get("ensemble", {}))
        model = HazardModel(
            pipeline=None,
            feature_names=payload.get("feature_names", []),
            version=payload.get("version", "unknown"),
            metrics=payload.get("metrics", {}),
            ensemble=ensemble,
            label_encoder=payload.get("label_encoder"),
            classes=payload.get("classes", []),
            weights=payload.get("weights", {}),
        )
        logger.info(
            "Ensemble ML model loaded: %s (models=%s)",
            model.version,
            list(ensemble.keys()),
        )
    else:
        # Legacy single-pipeline artifact
        model = HazardModel(
            pipeline=payload["pipeline"],
            feature_names=payload["feature_names"],
            version=payload.get("version", "unknown"),
            metrics=payload.get("metrics"),
        )
        logger.info(
            "ML model loaded: %s (classes=%s)",
            model.version,
            list(model.pipeline.classes_),
        )

    return model


# Populated by preload_model() during app startup.
hazard_model: HazardModel | None = None


def preload_model() -> HazardModel | None:
    """Load both the hazard classifier and the forward-looking regressor.

    Either one may be missing; the pipeline degrades (rules-only mode / trend
    extrapolation) rather than failing to start.
    """
    global hazard_model
    hazard_model = load_model()
    # Imported here to keep the classical inference module free of the forecast
    # dependency at import time (tests may load one without the other).
    from app.ml.forecast import load_forecast_model

    load_forecast_model()
    return hazard_model
