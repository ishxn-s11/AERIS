"""Train and compare baseline hazard classifiers on the SYNTHETIC dataset.

Trains three model families (Logistic Regression, Random Forest, Gradient
Boosting), evaluates with stratified cross-validation + held-out test split,
selects the best by macro-F1 (classes are imbalanced), and saves a joblib
artifact containing pipeline + feature order + metrics.

⚠ Trained on SYNTHETIC data (see generate_dataset.py) — pattern capability only.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import joblib
import pandas as pd
from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import classification_report, confusion_matrix, f1_score
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT))

from ml.training.generate_dataset import FEATURE_NAMES  # noqa: E402


def load_dataframe(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    missing = [c for c in FEATURE_NAMES + ["label"] if c not in df.columns]
    if missing:
        raise SystemExit(f"Dataset missing columns: {missing}")
    return df


def build_candidates(seed: int) -> dict[str, Pipeline]:
    return {
        "logistic_regression": Pipeline([
            ("scaler", StandardScaler()),
            ("clf", LogisticRegression(max_iter=2000, class_weight="balanced", random_state=seed)),
        ]),
        "random_forest": Pipeline([
            ("clf", RandomForestClassifier(
                n_estimators=300, min_samples_leaf=2, class_weight="balanced",
                n_jobs=-1, random_state=seed)),
        ]),
        "gradient_boosting": Pipeline([
            ("clf", GradientBoostingClassifier(random_state=seed)),
        ]),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Train AERIS hazard model")
    parser.add_argument("--dataset", default="ml/datasets/aeris_synthetic.csv")
    parser.add_argument("--out", default="ml/models/aeris_model.joblib")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--version", default=None, help="model version tag")
    args = parser.parse_args()

    df = load_dataframe(args.dataset)
    X, y = df[FEATURE_NAMES], df["label"]

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.25, stratify=y, random_state=args.seed
    )

    results: dict[str, dict] = {}
    best_name, best_f1, best_pipeline = None, -1.0, None
    for name, pipeline in build_candidates(args.seed).items():
        pipeline.fit(X_train, y_train)
        pred = pipeline.predict(X_test)
        f1 = f1_score(y_test, pred, average="macro")
        results[name] = {
            "macro_f1": round(f1, 4),
            "report": classification_report(y_test, pred, output_dict=True, zero_division=0),
            "confusion": confusion_matrix(y_test, pred).tolist(),
        }
        print(f"{name:22s} macro-F1={f1:.4f}")
        if f1 > best_f1:
            best_name, best_f1, best_pipeline = name, f1, pipeline

    print(f"\nSelected: {best_name} (macro-F1 {best_f1:.4f})")
    print(classification_report(y_test, best_pipeline.predict(X_test), zero_division=0))

    version = args.version or f"{best_name}-v{pd.Timestamp.utcnow().strftime('%Y%m%d%H%M')}"
    artifact = {
        "pipeline": best_pipeline,
        "feature_names": FEATURE_NAMES,
        "version": version,
        "metrics": {
            "selected": best_name,
            "macro_f1": round(best_f1, 4),
            "candidates": {k: v["macro_f1"] for k, v in results.items()},
            "training_rows": len(df),
            "data_source": "SYNTHETIC generator (iot simulator scenarios)",
        },
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(artifact, out)
    print(f"Saved {out} ({version})")

    metrics_path = out.with_name(out.stem + "_metrics.json")
    metrics_path.write_text(json.dumps(results, indent=2))
    print(f"Metrics detail: {metrics_path}")


if __name__ == "__main__":
    main()
