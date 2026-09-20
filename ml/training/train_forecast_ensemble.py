"""Train ensemble forecast models for 10-minute risk prediction.

Trains multiple regressors and selects the best by held-out MAE.
Always reports the "no-change" baseline for honest comparison.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import (
    GradientBoostingRegressor,
    RandomForestRegressor,
)
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import GroupShuffleSplit
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "backend"))

from ml.training.generate_dataset import FEATURE_NAMES

TARGET = "future_risk_score"


def load_dataset(path: str) -> tuple[pd.DataFrame, np.ndarray, np.ndarray]:
    """Load and validate forecast dataset."""
    df = pd.read_csv(path)
    
    missing = [c for c in FEATURE_NAMES + [TARGET] if c not in df.columns]
    if missing:
        raise SystemExit(
            f"Dataset missing columns: {missing}\n"
            "Regenerate with: python ml/training/generate_dataset.py --task forecast"
        )
    
    X = df[FEATURE_NAMES].values
    y = df[TARGET].values
    
    return df, X, y


def train_xgboost_regressor(X_train, y_train, X_test, y_test, seed):
    """Train XGBoost regressor."""
    try:
        import xgboost as xgb
    except ImportError:
        print("XGBoost not installed, skipping...")
        return None
    
    model = xgb.XGBRegressor(
        n_estimators=150,
        max_depth=6,
        learning_rate=0.1,
        subsample=0.8,
        colsample_bytree=0.8,
        random_state=seed,
        n_jobs=-1,
    )
    
    model.fit(X_train, y_train, eval_set=[(X_test, y_test)], verbose=False)
    
    pred = np.clip(model.predict(X_test), 0.0, 100.0)
    mae = float(mean_absolute_error(y_test, pred))
    rmse = float(mean_squared_error(y_test, pred) ** 0.5)
    r2 = float(r2_score(y_test, pred))
    
    print(f"XGBoost Regressor: MAE={mae:.3f}, RMSE={rmse:.3f}, R²={r2:.4f}")
    
    return {
        'model': model,
        'name': 'xgboost',
        'mae': mae,
        'rmse': rmse,
        'r2': r2,
    }


def train_random_forest_regressor(X_train, y_train, X_test, y_test, seed):
    """Train Random Forest regressor."""
    model = RandomForestRegressor(
        n_estimators=180,
        max_depth=16,
        min_samples_leaf=4,
        n_jobs=-1,
        random_state=seed,
    )
    
    model.fit(X_train, y_train)
    
    pred = np.clip(model.predict(X_test), 0.0, 100.0)
    mae = float(mean_absolute_error(y_test, pred))
    rmse = float(mean_squared_error(y_test, pred) ** 0.5)
    r2 = float(r2_score(y_test, pred))
    
    print(f"Random Forest Regressor: MAE={mae:.3f}, RMSE={rmse:.3f}, R²={r2:.4f}")
    
    return {
        'model': model,
        'name': 'random_forest',
        'mae': mae,
        'rmse': rmse,
        'r2': r2,
    }


def train_gradient_boosting_regressor(X_train, y_train, X_test, y_test, seed):
    """Train Gradient Boosting regressor."""
    model = GradientBoostingRegressor(
        n_estimators=100,
        max_depth=5,
        learning_rate=0.1,
        random_state=seed,
    )
    
    model.fit(X_train, y_train)
    
    pred = np.clip(model.predict(X_test), 0.0, 100.0)
    mae = float(mean_absolute_error(y_test, pred))
    rmse = float(mean_squared_error(y_test, pred) ** 0.5)
    r2 = float(r2_score(y_test, pred))
    
    print(f"Gradient Boosting Regressor: MAE={mae:.3f}, RMSE={rmse:.3f}, R²={r2:.4f}")
    
    return {
        'model': model,
        'name': 'gradient_boosting',
        'mae': mae,
        'rmse': rmse,
        'r2': r2,
    }


def train_forecast_ensemble(dataset_path: str, output_path: str, seed: int = 42):
    """Train all forecast models and create ensemble artifact."""
    print("=" * 60)
    print("AERIS Forecast Ensemble Training (10-min horizon)")
    print("=" * 60)
    
    # Load dataset
    df, X, y = load_dataset(dataset_path)
    print(f"\nDataset: {len(df)} rows")
    
    # Split data (group-aware)
    groups = df["episode"].values if "episode" in df.columns else np.arange(len(df))
    gss = GroupShuffleSplit(n_splits=1, test_size=0.25, random_state=seed)
    train_idx, test_idx = next(gss.split(X, y, groups=groups))
    
    X_train, X_test = X[train_idx], X[test_idx]
    y_train, y_test = y[train_idx], y[test_idx]
    
    print(f"Split: {len(train_idx)} train / {len(test_idx)} test rows")
    
    # Baselines
    no_change_mae = float(mean_absolute_error(y_test, df.iloc[test_idx]["risk_score_now"].values))
    mean_mae = float(mean_absolute_error(y_test, np.full(len(y_test), y_train.mean())))
    print(f"\nBaseline no_change MAE: {no_change_mae:.3f}")
    print(f"Baseline mean MAE: {mean_mae:.3f}")
    
    # Train individual models
    print("\n" + "=" * 60)
    print("Training Individual Regressors")
    print("=" * 60)
    
    models = {}
    
    # XGBoost
    print("\n--- XGBoost ---")
    xgb_result = train_xgboost_regressor(X_train, y_train, X_test, y_test, seed)
    if xgb_result:
        models['xgboost'] = xgb_result
    
    # Random Forest
    print("\n--- Random Forest ---")
    rf_result = train_random_forest_regressor(X_train, y_train, X_test, y_test, seed)
    if rf_result:
        models['random_forest'] = rf_result
    
    # Gradient Boosting
    print("\n--- Gradient Boosting ---")
    gbm_result = train_gradient_boosting_regressor(X_train, y_train, X_test, y_test, seed)
    if gbm_result:
        models['gradient_boosting'] = gbm_result
    
    # Select best model
    best_name = min(models.keys(), key=lambda k: models[k]['mae'])
    best_result = models[best_name]
    
    print(f"\n{'=' * 60}")
    print(f"Best Model: {best_name} (MAE {best_result['mae']:.3f})")
    print(f"{'=' * 60}")
    
    # Check if model beats baseline
    if best_result['mae'] >= no_change_mae:
        print("\n⚠ Warning: Best model does NOT beat the no-change baseline.")
        print("  Backend will use trend extrapolation as fallback.")
    
    # Ensemble prediction (weighted average)
    if len(models) > 1:
        weights = {name: 1.0 / result['mae'] for name, result in models.items()}
        total_weight = sum(weights.values())
        weights = {k: v / total_weight for k, v in weights.items()}
        
        ensemble_pred = np.zeros(len(y_test))
        for name, result in models.items():
            pred = np.clip(result['model'].predict(X_test), 0.0, 100.0)
            ensemble_pred += pred * weights[name]
        
        ensemble_mae = float(mean_absolute_error(y_test, ensemble_pred))
        ensemble_rmse = float(mean_squared_error(y_test, ensemble_pred) ** 0.5)
        ensemble_r2 = float(r2_score(y_test, ensemble_pred))
        
        print(f"\nEnsemble (weighted): MAE={ensemble_mae:.3f}, RMSE={ensemble_rmse:.3f}, R²={ensemble_r2:.4f}")
        
        # Use ensemble if it's better than best single model
        if ensemble_mae < best_result['mae']:
            best_name = 'ensemble'
            best_result = {
                'name': 'ensemble',
                'mae': ensemble_mae,
                'rmse': ensemble_rmse,
                'r2': ensemble_r2,
                'models': models,
                'weights': weights,
            }
    
    # Save artifact
    version = f"forecast-{best_name}-v{pd.Timestamp.utcnow().strftime('%Y%m%d%H%M')}"
    
    artifact = {
        'pipeline': best_result.get('model'),  # May be None for ensemble
        'models': {name: r['model'] for name, r in models.items()} if best_name == 'ensemble' else None,
        'weights': best_result.get('weights', {}),
        'feature_names': FEATURE_NAMES,
        'version': version,
        'horizon_minutes': 10,
        'metrics': {
            'selected': best_name,
            'mae': best_result['mae'],
            'rmse': best_result.get('rmse', 0.0),
            'r2': best_result.get('r2', 0.0),
            'candidates': {name: r['mae'] for name, r in models.items()},
            'baseline_no_change_mae': no_change_mae,
            'baseline_mean_mae': mean_mae,
            'beats_baseline': best_result['mae'] < no_change_mae,
        },
    }
    
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(artifact, output)
    
    print(f"\nSaved forecast artifact: {output}")
    print(f"Version: {version}")
    
    # Save metrics
    metrics_path = output.with_name(output.stem + "_metrics.json")
    metrics_path.write_text(json.dumps(artifact['metrics'], indent=2))
    print(f"Metrics: {metrics_path}")
    
    return artifact


def main():
    parser = argparse.ArgumentParser(description="Train AERIS forecast ensemble")
    parser.add_argument("--dataset", default="ml/datasets/aeris_forecast_synthetic.csv")
    parser.add_argument("--output", default="ml/models/aeris_forecast_ensemble.joblib")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    
    train_forecast_ensemble(args.dataset, args.output, args.seed)


if __name__ == "__main__":
    main()
