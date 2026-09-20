"""Tests for the AERIS multi-model ensemble pipeline.

Tests the ensemble model loading, prediction, and fallback behavior.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import joblib
import numpy as np
import pandas as pd
import pytest
from sklearn.ensemble import RandomForestClassifier

from app.ml.inference import HazardModel, load_model
from app.ml.forecast import ForecastModel, load_forecast_model


FEATURES = [
    "temperature", "humidity", "co", "methane", "smoke", "flame",
    "temperature_ma", "gas_ma", "smoke_ma", "co_ma",
    "temperature_rate", "gas_rate", "smoke_rate", "temperature_std",
]
CLASSES = ["CRITICAL", "FIRE_RISK", "GAS_LEAK", "SAFE", "WARNING"]


# ---------------------------------------------------------------------------
# Fixtures — use real sklearn estimators so artifacts can be pickled
# ---------------------------------------------------------------------------
def _make_trivial_rf():
    """A tiny RandomForestClassifier that fits on 2 samples."""
    X = np.array([[28, 45, 12, 60, 40, 0, 28, 60, 40, 12, 0, 0, 0, 0.5],
                  [80, 20, 200, 900, 600, 1, 80, 900, 600, 200, 10, 100, 80, 5]])
    y = np.array(["SAFE", "CRITICAL"])
    clf = RandomForestClassifier(n_estimators=2, random_state=0)
    clf.fit(X, y)
    return clf


@pytest.fixture
def mock_ensemble_artifact():
    """Create a real (picklable) ensemble artifact for testing."""
    rf = _make_trivial_rf()
    return {
        "models": {"random_forest": rf, "xgboost": rf},  # same model for simplicity
        "feature_names": FEATURES,
        "classes": CLASSES,
        "weights": {"random_forest": 0.9995, "xgboost": 0.9851},
        "version": "ensemble-test-v1",
        "metrics": {"individual": {"random_forest": 0.9995, "xgboost": 0.9851}},
    }


@pytest.fixture
def mock_legacy_artifact():
    """Create a real legacy single-pipeline artifact for testing."""
    rf = _make_trivial_rf()
    return {
        "pipeline": rf,
        "feature_names": FEATURES,
        "version": "legacy-test-v1",
        "metrics": {"macro_f1": 0.96},
    }


@pytest.fixture
def mock_forecast_ensemble():
    """Create a real forecast ensemble artifact."""
    from sklearn.ensemble import RandomForestRegressor

    X = np.array([[28, 45, 12, 60, 40, 0, 28, 60, 40, 12, 0, 0, 0, 0.5]])
    reg = RandomForestRegressor(n_estimators=2, random_state=0)
    reg.fit(X, np.array([30.0]))

    return {
        "models": {"random_forest": reg, "xgboost": reg},
        "feature_names": FEATURES,
        "weights": {"random_forest": 0.8, "xgboost": 0.2},
        "version": "forecast-ensemble-test-v1",
        "horizon_minutes": 10,
        "metrics": {"mae": 0.12},
    }


# ---------------------------------------------------------------------------
# HazardModel tests
# ---------------------------------------------------------------------------
class TestHazardModelEnsemble:
    def test_ensemble_prediction(self, mock_ensemble_artifact):
        """Test that ensemble model produces predictions via weighted voting."""
        model = HazardModel(
            pipeline=None,
            feature_names=mock_ensemble_artifact["feature_names"],
            version=mock_ensemble_artifact["version"],
            ensemble=mock_ensemble_artifact["models"],
            classes=mock_ensemble_artifact["classes"],
            weights=mock_ensemble_artifact["weights"],
        )

        vector = [28.0, 45.0, 12.0, 60.0, 40.0, 0.0,
                  28.0, 60.0, 40.0, 12.0, 0.0, 0.0, 0.0, 0.5]
        label, confidence = model.predict_label(vector)

        assert label in mock_ensemble_artifact["classes"]
        assert 0.0 <= confidence <= 1.0

    def test_legacy_prediction(self, mock_legacy_artifact):
        """Test that legacy single-pipeline model works."""
        model = HazardModel(
            pipeline=mock_legacy_artifact["pipeline"],
            feature_names=mock_legacy_artifact["feature_names"],
            version=mock_legacy_artifact["version"],
        )

        vector = [28.0, 45.0, 12.0, 60.0, 40.0, 0.0,
                  28.0, 60.0, 40.0, 12.0, 0.0, 0.0, 0.0, 0.5]
        label, confidence = model.predict_label(vector)

        assert label in ["CRITICAL", "FIRE_RISK", "GAS_LEAK", "SAFE", "WARNING"]
        assert 0.0 <= confidence <= 1.0

    def test_empty_ensemble_fallback(self):
        """Test that empty ensemble returns SAFE."""
        model = HazardModel(
            pipeline=None,
            feature_names=[],
            version="test",
            ensemble={},
            classes=["SAFE"],
        )

        label, confidence = model.predict_label([])
        assert label == "SAFE"
        assert confidence == 0.0


# ---------------------------------------------------------------------------
# ForecastModel tests
# ---------------------------------------------------------------------------
class TestForecastModelEnsemble:
    def test_ensemble_forecast(self, mock_forecast_ensemble):
        """Test that ensemble forecast model produces predictions."""
        model = ForecastModel(
            pipeline=None,
            feature_names=mock_forecast_ensemble["feature_names"],
            version=mock_forecast_ensemble["version"],
            models=mock_forecast_ensemble["models"],
            weights=mock_forecast_ensemble["weights"],
        )

        vector = [28.0, 45.0, 12.0, 60.0, 40.0, 0.0,
                  28.0, 60.0, 40.0, 12.0, 0.0, 0.0, 0.0, 0.5]
        score = model.predict_score(vector)

        assert 0.0 <= score <= 100.0

    def test_legacy_forecast(self):
        """Test that legacy single-pipeline forecast model works."""
        mock_pipeline = MagicMock()
        mock_pipeline.predict.return_value = np.array([65.0])

        model = ForecastModel(
            pipeline=mock_pipeline,
            feature_names=["f1", "f2"],
            version="legacy",
        )

        score = model.predict_score([1.0, 2.0])
        assert 0.0 <= score <= 100.0

    def test_reported_confidence(self, mock_forecast_ensemble):
        """Test that reported confidence is derived from MAE."""
        model = ForecastModel(
            pipeline=None,
            feature_names=[],
            version="test",
            metrics={"mae": 5.0},
        )

        # confidence = 1 - mae/100 = 1 - 5/100 = 0.95
        assert abs(model.reported_confidence - 0.95) < 0.01


# ---------------------------------------------------------------------------
# Model loading tests
# ---------------------------------------------------------------------------
class TestModelLoading:
    def test_load_ensemble_artifact(self, mock_ensemble_artifact, tmp_path):
        """Test loading an ensemble artifact."""
        artifact_path = tmp_path / "test_ensemble.joblib"
        joblib.dump(mock_ensemble_artifact, artifact_path)

        with patch("app.ml.inference.settings") as mock_settings:
            mock_settings.ml_model_path = str(artifact_path)
            mock_settings.ml_model_legacy_path = str(artifact_path)
            model = load_model(str(artifact_path))

        assert model is not None
        assert set(model.ensemble.keys()) == {"random_forest", "xgboost"}
        assert model.classes == mock_ensemble_artifact["classes"]

    def test_load_legacy_artifact(self, mock_legacy_artifact, tmp_path):
        """Test loading a legacy single-pipeline artifact."""
        artifact_path = tmp_path / "test_legacy.joblib"
        joblib.dump(mock_legacy_artifact, artifact_path)

        with patch("app.ml.inference.settings") as mock_settings:
            mock_settings.ml_model_path = str(artifact_path)
            mock_settings.ml_model_legacy_path = str(artifact_path)
            model = load_model(str(artifact_path))

        assert model is not None
        assert model.pipeline is not None
        assert model.ensemble == {}

    def test_missing_model_returns_none(self, tmp_path):
        """Test that missing model returns None."""
        with patch("app.ml.inference.settings") as mock_settings:
            mock_settings.ml_model_path = str(tmp_path / "nonexistent.joblib")
            mock_settings.ml_model_legacy_path = str(tmp_path / "also_nonexistent.joblib")
            model = load_model()

        assert model is None

    def test_load_forecast_ensemble(self, mock_forecast_ensemble, tmp_path):
        """Test loading a forecast ensemble artifact."""
        artifact_path = tmp_path / "test_forecast_ensemble.joblib"
        joblib.dump(mock_forecast_ensemble, artifact_path)

        with patch("app.ml.forecast.settings") as mock_settings:
            mock_settings.ml_forecast_model_path = str(artifact_path)
            mock_settings.ml_forecast_legacy_path = str(artifact_path)
            model = load_forecast_model(str(artifact_path))

        assert model is not None
        assert set(model.models.keys()) == {"random_forest", "xgboost"}
