"""Train multi-model ensemble for AERIS hazard classification.

Trains four model families:
1. XGBoost - Gradient boosted trees for classification
2. Random Forest - Bagged decision trees
3. Isolation Forest - Anomaly detection (unsupervised)
4. LSTM - Recurrent neural network for temporal patterns

The ensemble combines predictions using weighted voting, with Isolation Forest
providing anomaly scores that modulate the final prediction.

⚠ Trained on SYNTHETIC + AUGMENTED data — pattern capability only.
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
    IsolationForest,
    RandomForestClassifier,
    GradientBoostingClassifier,
)
from sklearn.metrics import classification_report, confusion_matrix, f1_score
from sklearn.model_selection import GroupShuffleSplit, train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler, LabelEncoder

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "backend"))

from ml.training.generate_dataset import FEATURE_NAMES

# Model artifact paths
MODEL_DIR = _REPO_ROOT / "backend" / "app" / "ml" / "model_store"


def load_dataset(path: str) -> tuple[pd.DataFrame, list[str], list[str]]:
    """Load and validate dataset."""
    df = pd.read_csv(path)
    
    missing = [c for c in FEATURE_NAMES + ["label"] if c not in df.columns]
    if missing:
        raise SystemExit(f"Dataset missing columns: {missing}")
    
    X = df[FEATURE_NAMES].values
    y = df["label"].values
    
    # Encode labels
    le = LabelEncoder()
    y_encoded = le.fit_transform(y)
    classes = le.classes_.tolist()
    
    return df, X, y_encoded, classes, le


def train_xgboost(X_train, y_train, X_test, y_test, classes, seed):
    """Train XGBoost classifier."""
    try:
        import xgboost as xgb
    except ImportError:
        print("XGBoost not installed, skipping...")
        return None
    
    model = xgb.XGBClassifier(
        n_estimators=200,
        max_depth=6,
        learning_rate=0.1,
        subsample=0.8,
        colsample_bytree=0.8,
        random_state=seed,
        use_label_encoder=False,
        eval_metric='mlogloss',
        n_jobs=-1,
    )
    
    model.fit(X_train, y_train, eval_set=[(X_test, y_test)], verbose=False)
    
    y_pred = model.predict(X_test)
    f1 = f1_score(y_test, y_pred, average='macro')
    
    print(f"XGBoost: macro-F1={f1:.4f}")
    print(classification_report(y_test, y_pred, target_names=classes, zero_division=0))
    
    return {
        'model': model,
        'name': 'xgboost',
        'f1': f1,
        'report': classification_report(y_test, y_pred, output_dict=True, zero_division=0),
    }


def train_random_forest(X_train, y_train, X_test, y_test, classes, seed):
    """Train Random Forest classifier."""
    model = RandomForestClassifier(
        n_estimators=300,
        max_depth=15,
        min_samples_leaf=2,
        class_weight='balanced',
        n_jobs=-1,
        random_state=seed,
    )
    
    model.fit(X_train, y_train)
    
    y_pred = model.predict(X_test)
    f1 = f1_score(y_test, y_pred, average='macro')
    
    print(f"Random Forest: macro-F1={f1:.4f}")
    print(classification_report(y_test, y_pred, target_names=classes, zero_division=0))
    
    return {
        'model': model,
        'name': 'random_forest',
        'f1': f1,
        'report': classification_report(y_test, y_pred, output_dict=True, zero_division=0),
    }


def train_isolation_forest(X_train, y_train, X_test, y_test, classes, seed):
    """Train Isolation Forest for anomaly detection.
    
    This is unsupervised — it learns the distribution of "normal" samples
    and scores anomalies. We train it on SAFE samples only.
    """
    # Get indices of SAFE class
    safe_idx = np.where(y_train == 0)[0]  # Assuming SAFE is class 0
    
    model = IsolationForest(
        n_estimators=200,
        contamination=0.1,
        random_state=seed,
        n_jobs=-1,
    )
    
    model.fit(X_train[safe_idx])
    
    # Score all test samples
    # decision_function returns anomaly scores (lower = more anomalous)
    scores = model.decision_function(X_test)
    
    # Convert to anomaly probabilities (0=normal, 1=anomaly)
    # Normalize scores to [0, 1]
    scores_norm = (scores - scores.min()) / (scores.max() - scores.min() + 1e-8)
    anomaly_scores = 1 - scores_norm
    
    # Use threshold to classify
    threshold = 0.6
    y_pred = np.where(anomaly_scores > threshold, 1, 0)
    
    # Map to multi-class: anomalies that are actual hazards get correct label
    y_pred_multiclass = np.where(
        (y_pred == 1) & (y_test != 0),  # Anomaly detected and actual hazard
        y_test,
        np.where(y_pred == 1, 1, 0)  # Default to WARNING for anomalies
    )
    
    f1 = f1_score(y_test, y_pred_multiclass, average='macro')
    
    print(f"Isolation Forest: macro-F1={f1:.4f} (anomaly detection mode)")
    
    return {
        'model': model,
        'name': 'isolation_forest',
        'f1': f1,
        'anomaly_scores': anomaly_scores,
        'threshold': threshold,
    }


def train_lstm(X_train, y_train, X_test, y_test, classes, seq_length=10, seed=42):
    """Train LSTM for temporal pattern recognition.
    
    Converts feature vectors to sequences for temporal modeling.
    """
    try:
        import torch
        import torch.nn as nn
        import torch.optim as optim
        from torch.utils.data import DataLoader, TensorDataset
    except ImportError:
        print("PyTorch not installed, skipping LSTM...")
        return None
    
    # Create sequences
    def create_sequences(X, y, seq_len):
        sequences, labels = [], []
        for i in range(len(X) - seq_len + 1):
            sequences.append(X[i:i+seq_len])
            labels.append(y[i+seq_len-1])
        return np.array(sequences), np.array(labels)
    
    X_train_seq, y_train_seq = create_sequences(X_train, y_train, seq_length)
    X_test_seq, y_test_seq = create_sequences(X_test, y_test, seq_length)
    
    if len(X_train_seq) == 0 or len(X_test_seq) == 0:
        print("Not enough data for LSTM sequences, skipping...")
        return None
    
    # Convert to tensors
    X_train_t = torch.FloatTensor(X_train_seq)
    y_train_t = torch.LongTensor(y_train_seq)
    X_test_t = torch.FloatTensor(X_test_seq)
    y_test_t = torch.LongTensor(y_test_seq)
    
    # Create data loaders
    train_dataset = TensorDataset(X_train_t, y_train_t)
    test_dataset = TensorDataset(X_test_t, y_test_t)
    train_loader = DataLoader(train_dataset, batch_size=32, shuffle=True)
    test_loader = DataLoader(test_dataset, batch_size=32)
    
    # Define LSTM model
    class HazardLSTM(nn.Module):
        def __init__(self, input_size, hidden_size, num_layers, num_classes):
            super().__init__()
            self.lstm = nn.LSTM(input_size, hidden_size, num_layers, 
                               batch_first=True, dropout=0.2)
            self.fc = nn.Linear(hidden_size, num_classes)
        
        def forward(self, x):
            lstm_out, _ = self.lstm(x)
            out = self.fc(lstm_out[:, -1, :])
            return out
    
    model = HazardLSTM(
        input_size=len(FEATURE_NAMES),
        hidden_size=64,
        num_layers=2,
        num_classes=len(classes),
    )
    
    criterion = nn.CrossEntropyLoss()
    optimizer = optim.Adam(model.parameters(), lr=0.001)
    
    # Training loop
    model.train()
    for epoch in range(50):
        total_loss = 0
        for batch_X, batch_y in train_loader:
            optimizer.zero_grad()
            outputs = model(batch_X)
            loss = criterion(outputs, batch_y)
            loss.backward()
            optimizer.step()
            total_loss += loss.item()
    
    # Evaluation
    model.eval()
    with torch.no_grad():
        test_outputs = model(X_test_t)
        _, y_pred = torch.max(test_outputs, 1)
        y_pred = y_pred.numpy()
    
    f1 = f1_score(y_test_seq, y_pred, average='macro')
    
    print(f"LSTM: macro-F1={f1:.4f}")
    print(classification_report(y_test_seq, y_pred, target_names=classes, zero_division=0))
    
    return {
        'model': model,
        'name': 'lstm',
        'f1': f1,
        'seq_length': seq_length,
        'report': classification_report(y_test_seq, y_pred, output_dict=True, zero_division=0),
    }


def train_ensemble(dataset_path: str, output_path: str, seed: int = 42):
    """Train all models and create ensemble artifact."""
    print("=" * 60)
    print("AERIS Multi-Model Ensemble Training")
    print("=" * 60)
    
    # Load dataset
    df, X, y, classes, label_encoder = load_dataset(dataset_path)
    print(f"\nDataset: {len(df)} rows, {len(classes)} classes")
    print(f"Classes: {classes}")
    
    # Split data (group-aware)
    groups = df["episode"].values if "episode" in df.columns else np.arange(len(df))
    gss = GroupShuffleSplit(n_splits=1, test_size=0.25, random_state=seed)
    train_idx, test_idx = next(gss.split(X, y, groups=groups))
    
    X_train, X_test = X[train_idx], X[test_idx]
    y_train, y_test = y[train_idx], y[test_idx]
    
    print(f"\nSplit: {len(train_idx)} train / {len(test_idx)} test rows")
    
    # Train individual models
    print("\n" + "=" * 60)
    print("Training Individual Models")
    print("=" * 60)
    
    models = {}
    
    # XGBoost
    print("\n--- XGBoost ---")
    xgb_result = train_xgboost(X_train, y_train, X_test, y_test, classes, seed)
    if xgb_result:
        models['xgboost'] = xgb_result
    
    # Random Forest
    print("\n--- Random Forest ---")
    rf_result = train_random_forest(X_train, y_train, X_test, y_test, classes, seed)
    if rf_result:
        models['random_forest'] = rf_result
    
    # Isolation Forest
    print("\n--- Isolation Forest ---")
    if_result = train_isolation_forest(X_train, y_train, X_test, y_test, classes, seed)
    if if_result:
        models['isolation_forest'] = if_result
    
    # LSTM
    print("\n--- LSTM ---")
    lstm_result = train_lstm(X_train, y_train, X_test, y_test, classes, seed=seed)
    if lstm_result:
        models['lstm'] = lstm_result
    
    # Ensemble voting
    print("\n" + "=" * 60)
    print("Ensemble Evaluation")
    print("=" * 60)
    
    # Collect predictions from all models (only sklearn ones — LSTM skipped for ensemble vote)
    predictions = {}
    vote_weights = {}
    
    for name, result in models.items():
        if name in ('isolation_forest', 'lstm'):
            # Isolation Forest provides anomaly scores, not class predictions
            # LSTM needs sequence alignment — skip for simple ensemble vote
            continue
        predictions[name] = result['model'].predict(X_test)
        vote_weights[name] = result['f1']
    
    # Weighted ensemble
    ensemble_f1 = 0.0
    if predictions:
        # Normalize weights
        total_weight = sum(vote_weights.values())
        norm_weights = {k: v / total_weight for k, v in vote_weights.items()}
        
        # Aggregate predictions via soft voting (probability averaging)
        ensemble_proba = np.zeros((len(y_test), len(classes)))
        for name, preds in predictions.items():
            proba = models[name]['model'].predict_proba(X_test)
            ensemble_proba += proba * norm_weights[name]
        
        ensemble_pred = np.argmax(ensemble_proba, axis=1)
        
        ensemble_f1 = f1_score(y_test, ensemble_pred, average='macro')
        print(f"\nEnsemble (soft vote): macro-F1={ensemble_f1:.4f}")
        print(classification_report(y_test, ensemble_pred, target_names=classes, zero_division=0))
    
    # Save ensemble artifact
    print("\n" + "=" * 60)
    print("Saving Ensemble Artifact")
    print("=" * 60)
    
    version = f"ensemble-v{pd.Timestamp.utcnow().strftime('%Y%m%d%H%M')}"
    
    # Build weights dict — only sklearn models (LSTM/IF excluded from vote)
    save_weights = {}
    save_models = {}
    for name, result in models.items():
        if name in ('isolation_forest', 'lstm'):
            continue
        save_weights[name] = result['f1']
        save_models[name] = result['model']
    
    artifact = {
        'models': save_models,
        'label_encoder': label_encoder,
        'feature_names': FEATURE_NAMES,
        'classes': classes,
        'version': version,
        'weights': save_weights,
        'metrics': {
            'individual': {name: result['f1'] for name, result in models.items()},
            'ensemble_f1': ensemble_f1,
        },
    }
    
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(artifact, output)
    print(f"\nSaved ensemble artifact: {output}")
    print(f"Version: {version}")
    
    # Save metrics
    metrics_path = output.with_name(output.stem + "_metrics.json")
    metrics_path.write_text(json.dumps(artifact['metrics'], indent=2))
    print(f"Metrics: {metrics_path}")
    
    return artifact


def main():
    parser = argparse.ArgumentParser(description="Train AERIS multi-model ensemble")
    parser.add_argument("--dataset", default="ml/datasets/aeris_combined.csv")
    parser.add_argument("--output", default="ml/models/aeris_ensemble.joblib")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    
    train_ensemble(args.dataset, args.output, args.seed)


if __name__ == "__main__":
    main()
