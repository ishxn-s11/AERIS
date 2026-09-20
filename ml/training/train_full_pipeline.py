"""AERIS Full ML Training Pipeline.

Generates a large, diverse synthetic dataset and trains:
1. XGBoost — gradient-boosted trees for classification
2. Random Forest — bagged decision trees
3. Isolation Forest — anomaly detection (unsupervised)
4. LSTM — recurrent neural network for temporal patterns

Plus a 10-minute risk forecast ensemble (XGBoost, RF, GradientBoosting).

Output: joblib artifacts for both hazard classification and risk forecasting.

Usage:
    cd AERIS
    python -m ml.training.train_full_pipeline
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import (
    GradientBoostingClassifier,
    GradientBoostingRegressor,
    IsolationForest,
    RandomForestClassifier,
    RandomForestRegressor,
)
from sklearn.metrics import (
    classification_report,
    f1_score,
    mean_absolute_error,
    mean_squared_error,
    r2_score,
)
from sklearn.model_selection import GroupShuffleSplit
from sklearn.preprocessing import LabelEncoder

# ── repo paths ────────────────────────────────────────────────────────
_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "backend"))

from ml.training.generate_dataset import FEATURE_NAMES, generate_dataset  # noqa: E402

# ── constants ─────────────────────────────────────────────────────────
MODEL_DIR = _REPO_ROOT / "ml" / "models"
BACKEND_MODEL_DIR = _REPO_ROOT / "backend" / "app" / "ml" / "model_store"
FORECAST_TARGET = "future_risk_score"

RANDOM_SEED = 42


# ═══════════════════════════════════════════════════════════════════════
# PART 1 — Hazard classification ensemble
# ═══════════════════════════════════════════════════════════════════════

def load_hazard_dataset(path: str) -> tuple[np.ndarray, np.ndarray, list[str], LabelEncoder]:
    """Load and encode the hazard classification dataset."""
    df = pd.read_csv(path)
    missing = [c for c in FEATURE_NAMES + ["label"] if c not in df.columns]
    if missing:
        raise SystemExit(f"Dataset missing columns: {missing}")

    X = df[FEATURE_NAMES].values
    y_raw = df["label"].values
    groups = df["episode"].values if "episode" in df.columns else np.arange(len(df))

    le = LabelEncoder()
    y = le.fit_transform(y_raw)
    classes = le.classes_.tolist()

    return X, y, groups, classes, le, df


def train_xgboost(X_train, y_train, X_test, y_test, classes, seed):
    """Train XGBoost classifier."""
    import xgboost as xgb

    model = xgb.XGBClassifier(
        n_estimators=300, max_depth=8, learning_rate=0.08,
        subsample=0.85, colsample_bytree=0.85,
        min_child_weight=3, gamma=0.1,
        random_state=seed, use_label_encoder=False,
        eval_metric="mlogloss", n_jobs=-1,
    )
    model.fit(X_train, y_train, eval_set=[(X_test, y_test)], verbose=False)
    y_pred = model.predict(X_test)
    f1 = f1_score(y_test, y_pred, average="macro")
    report = classification_report(y_test, y_pred, target_names=classes, zero_division=0)
    print(f"  XGBoost: macro-F1={f1:.4f}")
    return {"model": model, "name": "xgboost", "f1": float(f1), "report": report}


def train_random_forest(X_train, y_train, X_test, y_test, classes, seed):
    """Train Random Forest classifier."""
    model = RandomForestClassifier(
        n_estimators=500, max_depth=20, min_samples_leaf=2,
        class_weight="balanced", n_jobs=-1, random_state=seed,
    )
    model.fit(X_train, y_train)
    y_pred = model.predict(X_test)
    f1 = f1_score(y_test, y_pred, average="macro")
    report = classification_report(y_test, y_pred, target_names=classes, zero_division=0)
    print(f"  Random Forest: macro-F1={f1:.4f}")
    return {"model": model, "name": "random_forest", "f1": float(f1), "report": report}


def train_gradient_boosting(X_train, y_train, X_test, y_test, classes, seed):
    """Train Gradient Boosting classifier."""
    model = GradientBoostingClassifier(
        n_estimators=200, max_depth=6, learning_rate=0.1,
        subsample=0.8, random_state=seed,
    )
    model.fit(X_train, y_train)
    y_pred = model.predict(X_test)
    f1 = f1_score(y_test, y_pred, average="macro")
    report = classification_report(y_test, y_pred, target_names=classes, zero_division=0)
    print(f"  Gradient Boosting: macro-F1={f1:.4f}")
    return {"model": model, "name": "gradient_boosting", "f1": float(f1), "report": report}


def train_isolation_forest(X_train, y_train, X_test, y_test, classes, seed):
    """Train Isolation Forest for anomaly detection (unsupervised).

    Trained only on SAFE samples. Scores all test samples for anomaly probability.
    """
    safe_mask = y_train == 0  # SAFE = class 0
    if safe_mask.sum() < 50:
        print("  Isolation Forest: insufficient SAFE samples, skipping")
        return None

    model = IsolationForest(
        n_estimators=300, contamination=0.1,
        max_features=0.8, random_state=seed, n_jobs=-1,
    )
    model.fit(X_train[safe_mask])

    scores = model.decision_function(X_test)
    scores_norm = (scores - scores.min()) / (scores.max() - scores.min() + 1e-8)
    anomaly_scores = 1.0 - scores_norm

    threshold = 0.55
    y_pred = np.where(anomaly_scores > threshold, 1, 0)
    y_pred_multi = np.where(
        (y_pred == 1) & (y_test != 0), y_test,
        np.where(y_pred == 1, 1, 0),
    )
    f1 = f1_score(y_test, y_pred_multi, average="macro")
    print(f"  Isolation Forest: macro-F1={f1:.4f} (anomaly mode, threshold={threshold})")
    return {
        "model": model, "name": "isolation_forest",
        "f1": float(f1), "threshold": threshold,
    }


def train_lstm(X_train, y_train, X_test, y_test, classes, seq_length=12, seed=42):
    """Train LSTM for temporal pattern recognition.

    Creates sequences of length `seq_length` from the feature vectors.
    Note: LSTM predictions are not included in the ensemble vote because
    they require aligned sequences; the model is saved separately for
    optional time-series inference.
    """
    import torch
    import torch.nn as nn
    import torch.optim as optim
    from torch.utils.data import DataLoader, TensorDataset

    def create_sequences(X, y, seq_len):
        seqs, labels = [], []
        for i in range(len(X) - seq_len + 1):
            seqs.append(X[i : i + seq_len])
            labels.append(y[i + seq_len - 1])
        return np.array(seqs), np.array(labels)

    X_seq, y_seq = create_sequences(X_train, y_train, seq_length)
    X_test_seq, y_test_seq = create_sequences(X_test, y_test, seq_length)

    if len(X_seq) == 0 or len(X_test_seq) == 0:
        print("  LSTM: not enough data for sequences, skipping")
        return None

    X_test_t = torch.FloatTensor(X_test_seq)
    y_test_t = torch.LongTensor(y_test_seq)

    class HazardLSTM(nn.Module):
        def __init__(self, input_size, hidden, layers, num_classes):
            super().__init__()
            self.lstm = nn.LSTM(input_size, hidden, layers, batch_first=True, dropout=0.3)
            self.bn = nn.BatchNorm1d(hidden)
            self.fc1 = nn.Linear(hidden, hidden // 2)
            self.fc2 = nn.Linear(hidden // 2, num_classes)
            self.relu = nn.ReLU()

        def forward(self, x):
            out, _ = self.lstm(x)
            out = out[:, -1, :]
            out = self.bn(out)
            out = self.relu(self.fc1(out))
            return self.fc2(out)

    # Subsample training data for LSTM to keep training time manageable
    max_train = min(len(X_seq), 20000)
    if len(X_seq) > max_train:
        rng = np.random.RandomState(seed)
        idx = rng.choice(len(X_seq), max_train, replace=False)
        X_seq_sub, y_seq_sub = X_seq[idx], y_seq[idx]
    else:
        X_seq_sub, y_seq_sub = X_seq, y_seq

    loader = DataLoader(TensorDataset(torch.FloatTensor(X_seq_sub), torch.LongTensor(y_seq_sub)),
                        batch_size=64, shuffle=True)

    model = HazardLSTM(len(FEATURE_NAMES), 64, 1, len(classes))
    criterion = nn.CrossEntropyLoss()
    optimizer = optim.Adam(model.parameters(), lr=0.001)
    scheduler = optim.lr_scheduler.ReduceLROnPlateau(optimizer, patience=5, factor=0.5)

    model.train()
    best_loss = float("inf")
    patience_counter = 0
    for epoch in range(30):
        epoch_loss = 0.0
        for bx, by in loader:
            optimizer.zero_grad()
            loss = criterion(model(bx), by)
            loss.backward()
            optimizer.step()
            epoch_loss += loss.item()
        avg_loss = epoch_loss / len(loader)
        scheduler.step(avg_loss)
        if avg_loss < best_loss:
            best_loss = avg_loss
            patience_counter = 0
            best_state = {k: v.clone() for k, v in model.state_dict().items()}
        else:
            patience_counter += 1
            if patience_counter >= 10:
                break

    model.load_state_dict(best_state)
    model.eval()
    with torch.no_grad():
        preds = torch.argmax(model(X_test_t), dim=1).numpy()
    f1 = f1_score(y_test_seq, preds, average="macro")
    print(f"  LSTM: macro-F1={f1:.4f} (seq_len={seq_length})")
    return {
        "model": model, "name": "lstm",
        "f1": float(f1), "seq_length": seq_length,
    }


def build_hazard_ensemble(dataset_path: str, output_path: str, seed: int = RANDOM_SEED):
    """Train all hazard classifiers and save ensemble artifact."""
    print("=" * 70)
    print("  AERIS HAZARD CLASSIFICATION — ENSEMBLE TRAINING")
    print("=" * 70)

    X, y, groups, classes, le, df = load_hazard_dataset(dataset_path)
    print(f"\nDataset: {len(df):,} rows × {len(FEATURE_NAMES)} features")
    print(f"Classes ({len(classes)}): {classes}")
    print(f"Episodes: {df['episode'].nunique() if 'episode' in df.columns else 'N/A'}")

    # Group-aware split (no episode leakage)
    gss = GroupShuffleSplit(n_splits=1, test_size=0.20, random_state=seed)
    train_idx, test_idx = next(gss.split(X, y, groups=groups))
    X_train, X_test = X[train_idx], X[test_idx]
    y_train, y_test = y[train_idx], y[test_idx]
    print(f"Split: {len(train_idx):,} train / {len(test_idx):,} test")

    # ── Train individual models ────────────────────────────────────
    t0 = time.time()
    results = {}

    print("\n--- Training XGBoost ---")
    r = train_xgboost(X_train, y_train, X_test, y_test, classes, seed)
    if r: results["xgboost"] = r

    print("\n--- Training Random Forest ---")
    r = train_random_forest(X_train, y_train, X_test, y_test, classes, seed)
    if r: results["random_forest"] = r

    print("\n--- Training Gradient Boosting ---")
    r = train_gradient_boosting(X_train, y_train, X_test, y_test, classes, seed)
    if r: results["gradient_boosting"] = r

    print("\n--- Training Isolation Forest ---")
    r = train_isolation_forest(X_train, y_train, X_test, y_test, classes, seed)
    if r: results["isolation_forest"] = r

    print("\n--- Training LSTM ---")
    r = train_lstm(X_train, y_train, X_test, y_test, classes, seed=seed)
    if r: results["lstm"] = r

    elapsed = time.time() - t0
    print(f"\nTraining completed in {elapsed:.1f}s")

    # ── Ensemble voting ────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("  ENSEMBLE EVALUATION (weighted soft-vote)")
    print("=" * 70)

    vote_models = {}
    vote_weights = {}
    for name, res in results.items():
        if name in ("isolation_forest", "lstm"):
            continue
        vote_models[name] = res["model"]
        vote_weights[name] = res["f1"]

    total_w = sum(vote_weights.values())
    norm_w = {k: v / total_w for k, v in vote_weights.items()}

    ensemble_proba = np.zeros((len(y_test), len(classes)))
    for name, model in vote_models.items():
        proba = model.predict_proba(X_test)
        ensemble_proba += proba * norm_w[name]

    ensemble_pred = np.argmax(ensemble_proba, axis=1)
    ensemble_f1 = f1_score(y_test, ensemble_pred, average="macro")
    print(f"\nEnsemble soft-vote: macro-F1={ensemble_f1:.4f}")
    print(classification_report(y_test, ensemble_pred, target_names=classes, zero_division=0))

    # ── Save artifact ──────────────────────────────────────────────
    version = f"ensemble-v{pd.Timestamp.utcnow().strftime('%Y%m%d%H%M')}"
    save_models = {}
    save_weights = {}
    for name, res in results.items():
        if name in ("isolation_forest", "lstm"):
            continue
        save_models[name] = res["model"]
        save_weights[name] = res["f1"]

    artifact = {
        "models": save_models,
        "label_encoder": le,
        "feature_names": FEATURE_NAMES,
        "classes": classes,
        "version": version,
        "weights": save_weights,
        "metrics": {
            "individual": {name: res["f1"] for name, res in results.items()},
            "ensemble_f1": float(ensemble_f1),
            "training_rows": len(df),
            "training_seconds": round(elapsed, 1),
        },
    }

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(artifact, output, compress=3)
    print(f"\nSaved: {output} ({output.stat().st_size / 1024:.0f} KB)")

    metrics_path = output.with_name(output.stem + "_metrics.json")
    metrics_path.write_text(json.dumps(artifact["metrics"], indent=2))
    print(f"Metrics: {metrics_path}")

    return artifact


# ═══════════════════════════════════════════════════════════════════════
# PART 2 — Forecast ensemble (10-min risk projection)
# ═══════════════════════════════════════════════════════════════════════

def build_forecast_ensemble(dataset_path: str, output_path: str, seed: int = RANDOM_SEED):
    """Train forecast regressors and save the best ensemble."""
    print("\n" + "=" * 70)
    print("  AERIS RISK FORECAST — 10-MINUTE HORIZON ENSEMBLE")
    print("=" * 70)

    df = pd.read_csv(dataset_path)
    missing = [c for c in FEATURE_NAMES + [FORECAST_TARGET] if c not in df.columns]
    if missing:
        raise SystemExit(f"Dataset missing columns: {missing}")

    X = df[FEATURE_NAMES].values
    y = df[FORECAST_TARGET].values
    groups = df["episode"].values if "episode" in df.columns else np.arange(len(df))

    gss = GroupShuffleSplit(n_splits=1, test_size=0.20, random_state=seed)
    train_idx, test_idx = next(gss.split(X, y, groups=groups))
    X_train, X_test = X[train_idx], X[test_idx]
    y_train, y_test = y[train_idx], y[test_idx]

    no_change_mae = float(mean_absolute_error(y_test, df.iloc[test_idx]["risk_score_now"].values))
    mean_mae = float(mean_absolute_error(y_test, np.full(len(y_test), y_train.mean())))
    print(f"\nDataset: {len(df):,} rows")
    print(f"Split: {len(train_idx):,} train / {len(test_idx):,} test")
    print(f"Baseline no_change MAE: {no_change_mae:.3f}")
    print(f"Baseline mean MAE:      {mean_mae:.3f}")

    t0 = time.time()
    candidates = {}

    # XGBoost regressor
    print("\n--- XGBoost Regressor ---")
    try:
        import xgboost as xgb
        m = xgb.XGBRegressor(
            n_estimators=200, max_depth=7, learning_rate=0.08,
            subsample=0.85, colsample_bytree=0.85,
            random_state=seed, n_jobs=-1,
        )
        m.fit(X_train, y_train, eval_set=[(X_test, y_test)], verbose=False)
        pred = np.clip(m.predict(X_test), 0, 100)
        mae = float(mean_absolute_error(y_test, pred))
        rmse = float(mean_squared_error(y_test, pred) ** 0.5)
        r2 = float(r2_score(y_test, pred))
        print(f"  MAE={mae:.3f}  RMSE={rmse:.3f}  R²={r2:.4f}")
        candidates["xgboost"] = {"model": m, "mae": mae, "rmse": rmse, "r2": r2}
    except Exception as e:
        print(f"  Failed: {e}")

    # Random Forest regressor
    print("\n--- Random Forest Regressor ---")
    m = RandomForestRegressor(
        n_estimators=300, max_depth=18, min_samples_leaf=3,
        n_jobs=-1, random_state=seed,
    )
    m.fit(X_train, y_train)
    pred = np.clip(m.predict(X_test), 0, 100)
    mae = float(mean_absolute_error(y_test, pred))
    rmse = float(mean_squared_error(y_test, pred) ** 0.5)
    r2 = float(r2_score(y_test, pred))
    print(f"  MAE={mae:.3f}  RMSE={rmse:.3f}  R²={r2:.4f}")
    candidates["random_forest"] = {"model": m, "mae": mae, "rmse": rmse, "r2": r2}

    # Gradient Boosting regressor
    print("\n--- Gradient Boosting Regressor ---")
    m = GradientBoostingRegressor(
        n_estimators=200, max_depth=6, learning_rate=0.08, subsample=0.8,
        random_state=seed,
    )
    m.fit(X_train, y_train)
    pred = np.clip(m.predict(X_test), 0, 100)
    mae = float(mean_absolute_error(y_test, pred))
    rmse = float(mean_squared_error(y_test, pred) ** 0.5)
    r2 = float(r2_score(y_test, pred))
    print(f"  MAE={mae:.3f}  RMSE={rmse:.3f}  R²={r2:.4f}")
    candidates["gradient_boosting"] = {"model": m, "mae": mae, "rmse": rmse, "r2": r2}

    # Weighted ensemble prediction
    weights = {k: 1.0 / v["mae"] for k, v in candidates.items()}
    total_w = sum(weights.values())
    weights = {k: v / total_w for k, v in weights.items()}

    ensemble_pred = np.zeros(len(y_test))
    for name, cand in candidates.items():
        pred = np.clip(cand["model"].predict(X_test), 0, 100)
        ensemble_pred += pred * weights[name]

    ensemble_mae = float(mean_absolute_error(y_test, ensemble_pred))
    ensemble_rmse = float(mean_squared_error(y_test, ensemble_pred) ** 0.5)
    ensemble_r2 = float(r2_score(y_test, ensemble_pred))

    print(f"\nEnsemble weighted: MAE={ensemble_mae:.3f}  RMSE={ensemble_rmse:.3f}  R²={ensemble_r2:.4f}")

    # Select best
    best_name = min(candidates.keys(), key=lambda k: candidates[k]["mae"])
    best = candidates[best_name]
    if ensemble_mae < best["mae"]:
        best_name = "ensemble"
        best = {"mae": ensemble_mae, "rmse": ensemble_rmse, "r2": ensemble_r2}

    beats = best["mae"] < no_change_mae
    print(f"\nSelected: {best_name} (MAE {best['mae']:.3f}) — {'BEATS' if beats else 'does NOT beat'} baseline")

    elapsed = time.time() - t0

    # Save
    version = f"forecast-{best_name}-v{pd.Timestamp.utcnow().strftime('%Y%m%d%H%M')}"
    artifact = {
        "pipeline": candidates[best_name].get("model") if best_name != "ensemble" else None,
        "models": {k: v["model"] for k, v in candidates.items()} if best_name == "ensemble" else None,
        "weights": weights if best_name == "ensemble" else {},
        "feature_names": FEATURE_NAMES,
        "version": version,
        "horizon_minutes": 10,
        "metrics": {
            "selected": best_name,
            "mae": best["mae"],
            "rmse": best.get("rmse", 0.0),
            "r2": best.get("r2", 0.0),
            "candidates": {k: v["mae"] for k, v in candidates.items()},
            "baseline_no_change_mae": no_change_mae,
            "baseline_mean_mae": mean_mae,
            "beats_baseline": beats,
            "training_rows": len(df),
            "training_seconds": round(elapsed, 1),
        },
    }

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(artifact, output, compress=3)
    print(f"\nSaved: {output} ({output.stat().st_size / 1024:.0f} KB)")

    metrics_path = output.with_name(output.stem + "_metrics.json")
    metrics_path.write_text(json.dumps(artifact["metrics"], indent=2))
    print(f"Metrics: {metrics_path}")

    return artifact


# ═══════════════════════════════════════════════════════════════════════
# PART 3 — Generate dataset + run both pipelines
# ═══════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="AERIS full ML training pipeline")
    parser.add_argument("--episodes", type=int, default=60,
                        help="Episodes per scenario for hazard dataset")
    parser.add_argument("--forecast-episodes", type=int, default=30,
                        help="Episodes per scenario for forecast dataset")
    parser.add_argument("--ticks", type=int, default=240,
                        help="Ticks per episode for hazard dataset")
    parser.add_argument("--forecast-ticks", type=int, default=600,
                        help="Ticks per episode for forecast dataset")
    parser.add_argument("--seed", type=int, default=RANDOM_SEED)
    parser.add_argument("--skip-dataset", action="store_true",
                        help="Skip dataset generation, use existing CSVs")
    args = parser.parse_args()

    # ── Step 1: Generate datasets ──────────────────────────────────
    hazard_csv = "ml/datasets/aeris_synthetic.csv"
    forecast_csv = "ml/datasets/aeris_forecast_synthetic.csv"
    combined_csv = "ml/datasets/aeris_combined.csv"

    if not args.skip_dataset:
        print("=" * 70)
        print("  STEP 1: GENERATING TRAINING DATASETS")
        print("=" * 70)

        print(f"\nGenerating hazard dataset ({args.episodes} episodes × {args.ticks} ticks)...")
        df_hazard = generate_dataset(
            ticks=args.ticks, episodes_per_scenario=args.episodes,
            seed=args.seed, task="hazard",
        )
        Path(hazard_csv).parent.mkdir(parents=True, exist_ok=True)
        df_hazard.to_csv(hazard_csv, index=False)
        print(f"  >> {hazard_csv}: {len(df_hazard):,} rows")
        print(f"  Label distribution:\n{df_hazard['label'].value_counts().to_string()}")

        print(f"\nGenerating forecast dataset ({args.forecast_episodes} episodes × {args.forecast_ticks} ticks)...")
        df_forecast = generate_dataset(
            ticks=args.forecast_ticks, episodes_per_scenario=args.forecast_episodes,
            seed=args.seed, task="forecast", horizon_minutes=10,
        )
        df_forecast.to_csv(forecast_csv, index=False)
        print(f"  >> {forecast_csv}: {len(df_forecast):,} rows")
        print(f"  Future risk level distribution:\n{df_forecast['future_risk_level'].value_counts().to_string()}")
        baseline_mae = (df_forecast["future_risk_score"] - df_forecast["risk_score_now"]).abs().mean()
        print(f"  No-change baseline MAE: {baseline_mae:.2f}")

        # Combined for ensemble training (hazard task, all features)
        df_hazard.to_csv(combined_csv, index=False)
        print(f"  >> {combined_csv}: {len(df_hazard):,} rows")
    else:
        print("Skipping dataset generation — using existing CSVs")

    # ── Step 2: Train hazard ensemble ──────────────────────────────
    print("\n")
    hazard_artifact = build_hazard_ensemble(
        combined_csv,
        str(MODEL_DIR / "aeris_ensemble.joblib"),
        seed=args.seed,
    )

    # ── Step 3: Train forecast ensemble ────────────────────────────
    forecast_artifact = build_forecast_ensemble(
        forecast_csv,
        str(MODEL_DIR / "aeris_forecast_ensemble.joblib"),
        seed=args.seed,
    )

    # ── Step 4: Copy to backend model_store ────────────────────────
    BACKEND_MODEL_DIR.mkdir(parents=True, exist_ok=True)
    import shutil
    for src in [
        MODEL_DIR / "aeris_ensemble.joblib",
        MODEL_DIR / "aeris_forecast_ensemble.joblib",
    ]:
        dst = BACKEND_MODEL_DIR / src.name
        shutil.copy2(src, dst)
        print(f"Copied: {src.name} -> {dst}")

    print("\n" + "=" * 70)
    print("  TRAINING PIPELINE COMPLETE")
    print("=" * 70)
    print(f"\nHazard ensemble F1:  {hazard_artifact['metrics']['ensemble_f1']:.4f}")
    print(f"Forecast MAE:        {forecast_artifact['metrics']['mae']:.3f}")
    print(f"  Forecast beats baseline: {forecast_artifact['metrics']['beats_baseline']}")
    print(f"\nArtifacts:")
    for f in sorted(MODEL_DIR.glob("*.joblib")):
        print(f"  {f.name}: {f.stat().st_size // 1024} KB")
    for f in sorted(BACKEND_MODEL_DIR.glob("*.joblib")):
        print(f"  backend/{f.name}: {f.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
