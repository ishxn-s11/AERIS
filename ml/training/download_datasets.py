"""Download and preprocess real-world datasets for AERIS training.

This module provides:
1. Kaggle dataset downloading (requires kaggle API credentials)
2. Synthetic dataset augmentation as fallback
3. Dataset preprocessing and alignment to AERIS feature contract

Datasets targeted:
- Gas Sensor System for Confined Space Detection (CH4, CO, H2S)
- Multimodal Gas Detection & Classification (8 MOX sensors + temp/humidity)
- Industrial IoT Synthetic Dataset (predictive maintenance)

When Kaggle credentials are not available, the module generates augmented
synthetic data that combines the AERIS simulator with realistic noise patterns.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "backend"))

logger = logging.getLogger(__name__)

# AERIS feature contract (must match backend/app/ml/features.py)
FEATURE_NAMES = [
    "temperature", "humidity", "co", "methane", "smoke", "flame",
    "temperature_ma", "gas_ma", "smoke_ma", "co_ma",
    "temperature_rate", "gas_rate", "smoke_rate", "temperature_std",
]

# Scenario labels for hazard classification
SCENARIO_LABELS = ("normal", "gas_leak", "fire", "overheat", "smoke_increase")


def try_download_kaggle(dataset_name: str, output_dir: Path) -> bool:
    """Attempt to download a Kaggle dataset.
    
    Returns True if successful, False otherwise.
    """
    try:
        import subprocess
        result = subprocess.run(
            ["kaggle", "datasets", "download", "-d", dataset_name, "-p", str(output_dir), "--unzip"],
            capture_output=True,
            text=True,
            timeout=60
        )
        if result.returncode == 0:
            logger.info("Downloaded Kaggle dataset: %s", dataset_name)
            return True
        else:
            logger.warning("Kaggle download failed: %s", result.stderr[:200])
            return False
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        logger.warning("Kaggle CLI not available or timed out: %s", e)
        return False


def preprocess_gas_sensor_data(df: pd.DataFrame) -> pd.DataFrame:
    """Preprocess the Gas Sensor System for Confined Space Detection dataset.
    
    Expected columns: CH4, CO, H2S (or similar gas concentrations)
    Output: AERIS-aligned features with labels.
    """
    # Map gas concentrations to AERIS channels
    col_map = {}
    for col in df.columns:
        col_lower = col.lower()
        if 'ch4' in col_lower or 'methane' in col_lower:
            col_map[col] = 'methane'
        elif 'co' in col_lower and 'co2' not in col_lower:
            col_map[col] = 'co'
        elif 'h2s' in col_lower or 'smoke' in col_lower:
            col_map[col] = 'smoke'
        elif 'temp' in col_lower:
            col_map[col] = 'temperature'
        elif 'humid' in col_lower:
            col_map[col] = 'humidity'
    
    df = df.rename(columns=col_map)
    
    # Ensure required columns exist
    for col in ['temperature', 'humidity', 'co', 'methane', 'smoke']:
        if col not in df.columns:
            df[col] = 0.0
    
    df['flame'] = 0.0
    
    # Compute derived features
    df = compute_derived_features(df)
    
    # Label based on concentration thresholds
    df['label'] = 'SAFE'
    df.loc[df['methane'] > 200, 'label'] = 'WARNING'
    df.loc[df['methane'] > 400, 'label'] = 'GAS_LEAK'
    df.loc[df['methane'] > 650, 'label'] = 'CRITICAL'
    df.loc[df['co'] > 50, 'label'] = 'WARNING'
    df.loc[df['co'] > 90, 'label'] = 'GAS_LEAK'
    df.loc[df['smoke'] > 180, 'label'] = 'WARNING'
    df.loc[df['smoke'] > 300, 'label'] = 'FIRE_RISK'
    
    return df


def preprocess_multimodal_data(df: pd.DataFrame) -> pd.DataFrame:
    """Preprocess the Multimodal Gas Detection dataset.
    
    Expected: 8 MOX sensor columns + temperature + humidity
    """
    # Assume columns are sensor readings with temp/humidity
    cols = df.columns.tolist()
    
    # Find temp/humidity columns
    temp_col = next((c for c in cols if 'temp' in c.lower()), None)
    hum_col = next((c for c in cols if 'humid' in c.lower()), None)
    
    if temp_col:
        df['temperature'] = df[temp_col]
    else:
        df['temperature'] = 25.0
    
    if hum_col:
        df['humidity'] = df[hum_col]
    else:
        df['humidity'] = 45.0
    
    # Average MOX sensors as gas/smoke proxies
    sensor_cols = [c for c in cols if c not in [temp_col, hum_col] and df[c].dtype in ['float64', 'int64']]
    if len(sensor_cols) >= 4:
        df['methane'] = df[sensor_cols[:len(sensor_cols)//3]].mean(axis=1)
        df['co'] = df[sensor_cols[len(sensor_cols)//3:2*len(sensor_cols)//3]].mean(axis=1)
        df['smoke'] = df[sensor_cols[2*len(sensor_cols)//3:]].mean(axis=1)
    else:
        df['methane'] = 60.0
        df['co'] = 12.0
        df['smoke'] = 40.0
    
    df['flame'] = 0.0
    
    df = compute_derived_features(df)
    
    # Label from gas concentrations
    df['label'] = 'SAFE'
    df.loc[df['methane'] > 200, 'label'] = 'WARNING'
    df.loc[df['methane'] > 400, 'label'] = 'GAS_LEAK'
    
    return df


def compute_derived_features(df: pd.DataFrame) -> pd.DataFrame:
    """Compute AERIS-derived features (moving averages, rates, std)."""
    window = 15  # ~30 seconds at 2s intervals
    
    for col in ['temperature', 'methane', 'smoke', 'co']:
        if col in df.columns:
            # Moving average
            ma_col = f"{col}_ma" if col != 'methane' else 'gas_ma'
            df[ma_col] = df[col].rolling(window=window, min_periods=1).mean()
            
            # Rate of change (per minute)
            rate_col = f"{col}_rate" if col != 'methane' else 'gas_rate'
            if col == 'methane':
                rate_col = 'gas_rate'
            df[rate_col] = df[col].diff(periods=15) / (15 * 2 / 60)  # 2s intervals
    
    # Temperature std
    df['temperature_std'] = df['temperature'].rolling(window=window, min_periods=1).std().fillna(0.0)
    
    # Fill NaN
    df = df.fillna(0.0)
    
    return df


def generate_augmented_synthetic(episodes_per_scenario: int = 50, seed: int = 42) -> pd.DataFrame:
    """Generate augmented synthetic data combining simulator + realistic noise.
    
    This creates a larger, more diverse training set than the base simulator.
    """
    sys.path.insert(0, str(_REPO_ROOT / "iot/simulator"))
    from sensor_models import BASELINE, ScenarioState
    
    rng = np.random.RandomState(seed)
    all_rows = []
    
    for scenario in SCENARIO_LABELS:
        for ep in range(episodes_per_scenario):
            state = ScenarioState(scenario, seed=rng.randint(0, 1000000))
            frames = []
            
            for tick in range(1, 180):  # 6 minutes at 2s intervals
                frame = state.step()
                frames.append(frame)
                
                if tick < 30:  # Skip first 30 ticks for window
                    continue
                
                # Compute features
                temps = [f.temperature for f in frames[-30:]]
                gases = [f.methane for f in frames[-30:]]
                smokes = [f.smoke for f in frames[-30:]]
                cos_vals = [f.co for f in frames[-30:]]
                
                row = {
                    'temperature': frame.temperature,
                    'humidity': frame.humidity,
                    'co': frame.co,
                    'methane': frame.methane,
                    'smoke': frame.smoke,
                    'flame': 1.0 if frame.flame else 0.0,
                    'temperature_ma': np.mean(temps),
                    'gas_ma': np.mean(gases),
                    'smoke_ma': np.mean(smokes),
                    'co_ma': np.mean(cos_vals),
                    'temperature_rate': (np.median(temps[-3:]) - np.median(temps[:3])) / 1.0,  # per minute
                    'gas_rate': (np.median(gases[-3:]) - np.median(gases[:3])) / 1.0,
                    'smoke_rate': (np.median(smokes[-3:]) - np.median(smokes[:3])) / 1.0,
                    'temperature_std': np.std(temps),
                    'episode': f'{scenario}-{ep:03d}',
                    'tick': tick,
                }
                
                # Label
                if scenario == 'normal':
                    row['label'] = 'SAFE'
                elif scenario == 'gas_leak':
                    if frame.methane > 410 or tick > 90:
                        row['label'] = 'CRITICAL'
                    elif tick > 40:
                        row['label'] = 'GAS_LEAK'
                    elif tick > 18:
                        row['label'] = 'WARNING'
                    else:
                        row['label'] = 'SAFE'
                elif scenario == 'fire':
                    if frame.flame or tick > 60:
                        row['label'] = 'CRITICAL'
                    elif tick > 16:
                        row['label'] = 'FIRE_RISK'
                    elif tick > 11:
                        row['label'] = 'WARNING'
                    else:
                        row['label'] = 'SAFE'
                elif scenario == 'overheat':
                    if frame.temperature > 75:
                        row['label'] = 'CRITICAL'
                    elif tick > 45:
                        row['label'] = 'WARNING'
                    else:
                        row['label'] = 'SAFE'
                elif scenario == 'smoke_increase':
                    if frame.smoke > 500:
                        row['label'] = 'CRITICAL'
                    elif tick > 20:
                        row['label'] = 'FIRE_RISK'
                    else:
                        row['label'] = 'WARNING'
                
                all_rows.append(row)
    
    df = pd.DataFrame(all_rows)
    
    # Add realistic sensor noise
    for col in ['temperature', 'methane', 'smoke', 'co']:
        if col in df.columns:
            noise = rng.normal(0, 0.5, len(df))
            df[col] = df[col] + noise
    
    return df


def prepare_dataset(output_path: Path, episodes: int = 50) -> Path:
    """Prepare the training dataset.
    
    Tries Kaggle first, falls back to augmented synthetic data.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Try Kaggle datasets
    kaggle_dir = Path("ml/datasets/kaggle")
    kaggle_success = False
    
    # Try Gas Sensor for Confined Space
    if try_download_kaggle("abdulnassernabil/gas-sensor-system-for-confined-space-detection", kaggle_dir):
        # Find and preprocess CSV files
        for csv_file in kaggle_dir.glob("*.csv"):
            try:
                df = pd.read_csv(csv_file)
                if len(df) > 100:  # Reasonable dataset size
                    df = preprocess_gas_sensor_data(df)
                    df.to_csv(output_path, index=False)
                    logger.info("Prepared Kaggle dataset: %s (%d rows)", csv_file.name, len(df))
                    kaggle_success = True
                    break
            except Exception as e:
                logger.warning("Failed to process %s: %s", csv_file, e)
    
    # Fallback to augmented synthetic
    if not kaggle_success:
        logger.info("Generating augmented synthetic dataset...")
        df = generate_augmented_synthetic(episodes_per_scenario=episodes)
        df.to_csv(output_path, index=False)
        logger.info("Generated synthetic dataset: %d rows", len(df))
    
    return output_path


def main():
    parser = argparse.ArgumentParser(description="Prepare AERIS training dataset")
    parser.add_argument("--output", default="ml/datasets/aeris_combined.csv")
    parser.add_argument("--episodes", type=int, default=50, help="Episodes per scenario for synthetic fallback")
    args = parser.parse_args()
    
    output_path = Path(args.output)
    prepare_dataset(output_path, args.episodes)
    
    # Print summary
    df = pd.read_csv(output_path)
    print(f"\nDataset: {output_path}")
    print(f"Rows: {len(df)}")
    print(f"Columns: {list(df.columns)}")
    if 'label' in df.columns:
        print(f"\nLabel distribution:")
        print(df['label'].value_counts())


if __name__ == "__main__":
    main()
