"""Synthetic training dataset generator for AERIS.

⚠ SYNTHETIC DATA — clearly labeled as such. These scenarios mimic qualitative
patterns reported in industrial hazard literature (gradual gas accumulation,
fast fire development, machinery overheating, sensor noise). They are NOT
measurements from a real facility and must not be presented as such. Before any
production use, retrain on calibrated, labeled data from the target site.

Two tasks share one episode engine:

* `--task hazard`   — target: the scenario's ground-truth class
  (SAFE/WARNING/FIRE_RISK/GAS_LEAK/CRITICAL) for the current instant.
* `--task forecast` — target: the WORST rule-engine risk score inside the next
  `--horizon-minutes` (default 10). Rows without a fully observed horizon are
  dropped, so the label is never truncated into looking calm.

Both tasks carry `risk_score_now` (the production rule engine applied to the
row's own features) which doubles as the "no change" baseline for forecasting.
The scoring import is the SAME module the backend runs — the target is not a
second, drifting definition of risk.
"""
from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

import pandas as pd

# Make repo-root imports work regardless of cwd (script may run from anywhere).
_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT))
# The backend package is imported so the dataset target uses the production
# scoring function (single source of truth for "risk").
sys.path.insert(0, str(_REPO_ROOT / "backend"))

from iot.simulator.sensor_models import BASELINE, ScenarioState  # noqa: E402
from app.ml.features import FEATURE_NAMES as _PRODUCTION_FEATURES  # noqa: E402
from app.ml.risk_engine import evaluate  # noqa: E402

# One dataset tick == one simulator publish == 2 seconds. This MUST match the
# live publish interval, otherwise engineered rates (per-minute) will not
# transfer from training data to real-time inference.
TICK_SECONDS = 2

CLASSES = ("SAFE", "WARNING", "FIRE_RISK", "GAS_LEAK", "CRITICAL")

# Feature contract — imported from the backend so training data and online
# inference can never drift apart. The literal list is kept as a fallback for
# environments where the backend package is not importable.
FEATURE_NAMES = list(_PRODUCTION_FEATURES)

# Risk band cut points (mirror settings.risk_bands) used to label the projected
# score for reporting; the numeric target stays continuous.
BAND_SAFE_MAX = 30
BAND_WARNING_MAX = 60
BAND_HIGH_MAX = 80


def band_of(score: float) -> str:
    if score <= BAND_SAFE_MAX:
        return "SAFE"
    if score <= BAND_WARNING_MAX:
        return "WARNING"
    if score <= BAND_HIGH_MAX:
        return "HIGH"
    return "CRITICAL"


# Feature window in ticks: 120 s at the 2 s publish cadence, matching
# settings.feature_window_seconds on the backend.
WINDOW_TICKS = 60
# Minimum context before a row is emitted: a rate needs a real span, otherwise
# the value is noise amplified by a tiny denominator.
MIN_CONTEXT_TICKS = 30


def _median(values: list[float]) -> float:
    return sorted(values)[len(values) // 2]


def _window_features(frames: list, window: int | None = None) -> dict:
    """Compute the online-style features over the trailing history window.

    Mirrors backend/app/ml/features.py: median-anchored rates, window means.

    Train/serve consistency: the backend derives each robust rate from
    median(first 3) vs median(last 3) across its 120 s feature window — a
    ~2-minute slope. An earlier version of this generator used a 24 s span,
    which made noisy ambient channels look like trends ("temperature rising
    rapidly" out of sensor noise). The window and MIN_CONTEXT_TICKS guard below
    keep the training path comparable to live inference.
    """
    w = min(len(frames), window or WINDOW_TICKS)
    recent = frames[-w:]
    temps = [f.temperature for f in recent]
    gases = [f.methane for f in recent]
    smokes = [f.smoke for f in recent]
    cos = [f.co for f in recent]
    latest = recent[-1]

    def rate(values: list[float]) -> float:
        """Per-minute slope, median-anchored at both ends (spike-resistant)."""
        if len(values) < MIN_CONTEXT_TICKS:
            return 0.0
        minutes = ((len(values) - 1) * TICK_SECONDS) / 60.0
        if minutes <= 0:
            return 0.0
        return (_median(values[-3:]) - _median(values[:3])) / minutes

    import statistics as st
    temps_w, gases_w, smokes_w, cos_w = temps, gases, smokes, cos

    return {
        "temperature": latest.temperature,
        "humidity": latest.humidity,
        "co": latest.co,
        "methane": latest.methane,
        "smoke": latest.smoke,
        "flame": 1.0 if latest.flame else 0.0,
        "temperature_ma": st.fmean(temps_w),
        "gas_ma": st.fmean(gases_w),
        "smoke_ma": st.fmean(smokes_w),
        "co_ma": st.fmean(cos_w),
        "temperature_rate": rate(temps),
        "gas_rate": rate(gases),
        "smoke_rate": rate(smokes),
        "temperature_std": st.pstdev(temps_w),
    }


def _classify_tick(scenario: str, tick: int, frames: list) -> str:
    """Assign a ground-truth label from the scenario's physical ground truth.

    Labels come from the scenario mechanics (what is physically happening),
    not from threshold arithmetic — the model must learn patterns, not
    re-derive the rule engine.
    """
    latest = frames[-1]
    if scenario == "normal":
        return "SAFE"
    if scenario == "sensor_failure":
        return "SAFE"  # device health is handled by rules, not this classifier
    if scenario == "gas_leak":
        if latest.methane > BASELINE["methane"] + 350 or tick > 90:
            return "CRITICAL"
        if tick > 40:
            return "GAS_LEAK"
        if tick > 18:
            return "WARNING"
        return "SAFE"
    if scenario == "fire":
        if latest.flame or tick > 60:
            return "CRITICAL"
        if tick > 16:
            return "FIRE_RISK"
        if tick > 11:
            return "WARNING"
        return "SAFE"
    if scenario == "overheat":
        if latest.temperature > 75:
            return "CRITICAL"
        if tick > 45:
            return "WARNING"  # overheating without flame = elevated, not fire
        return "SAFE"
    if scenario == "smoke_increase":
        if latest.smoke > 500:
            return "CRITICAL"
        return "FIRE_RISK" if tick > 20 else "WARNING"
    return "SAFE"


def generate_dataset(
    ticks: int = 120,
    episodes_per_scenario: int = 40,
    seed: int = 7,
    task: str = "hazard",
    horizon_minutes: int = 10,
) -> pd.DataFrame:
    """Generate labelled episodes: each row is one tick of one episode.

    `task="forecast"` additionally emits `future_risk_score` (the worst score
    inside the horizon) and drops rows whose horizon is not fully observed.
    """
    rows: list[dict] = []
    rng = random.Random(seed)
    scenarios = ("normal", "gas_leak", "fire", "overheat", "smoke_increase")
    horizon_ticks = int(horizon_minutes * 60 / TICK_SECONDS) if task == "forecast" else 0

    for scenario in scenarios:
        for episode_index in range(episodes_per_scenario):
            state = ScenarioState(scenario, seed=rng.randrange(1_000_000))
            frames = []
            base_ts = pd.Timestamp("2026-01-01T00:00:00Z")
            episode: list[dict] = []
            scores: list[float] = []

            for t in range(1, ticks + 1):
                frame = state.step()
                # Synthetic stamps spaced at the live publish cadence.
                frame.timestamp = base_ts + pd.Timedelta(seconds=t * TICK_SECONDS)
                frames.append(frame)
                feats = _window_features(frames)
                # The production scoring function — identical to live inference.
                score = float(evaluate(feats)["risk_score"])
                scores.append(score)
                feats["label"] = _classify_tick(scenario, t, frames)
                feats["risk_score_now"] = score
                feats["risk_level_now"] = band_of(score)
                feats["scenario"] = scenario
                feats["episode"] = f"{scenario}-{episode_index:03d}"
                feats["tick"] = t
                episode.append(feats)

            # Rows before MIN_CONTEXT_TICKS carry zero rates (no window yet) and
            # would teach the model that a 0-rate always means SAFE.
            first_usable = MIN_CONTEXT_TICKS - 1
            if task == "forecast":
                for i in range(first_usable, len(episode)):
                    end = i + 1 + horizon_ticks
                    if end > len(scores):
                        break  # horizon not fully observed — no truncated labels
                    feats = episode[i]
                    future = max(scores[i + 1 : end])
                    feats["future_risk_score"] = round(future, 1)
                    feats["future_risk_level"] = band_of(future)
                    feats["horizon_minutes"] = horizon_minutes
                    rows.append(feats)
            else:
                rows.extend(episode[first_usable:])
    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate SYNTHETIC AERIS training data")
    parser.add_argument("--ticks", type=int, default=240)
    parser.add_argument("--episodes", type=int, default=40)
    parser.add_argument("--task", choices=("hazard", "forecast"), default="hazard")
    parser.add_argument("--horizon-minutes", type=int, default=10,
                        help="forecast task: projection horizon in minutes")
    parser.add_argument("--out", default=None)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()

    # A 10-minute horizon at the live 2 s cadence spans 300 ticks, so forecast
    # episodes must observe the horizon after MIN_CONTEXT_TICKS, and still keep
    # a useful number of emittable rows.
    ticks = args.ticks
    if args.task == "forecast":
        ticks = max(
            ticks,
            int(args.horizon_minutes * 60 / TICK_SECONDS) + MIN_CONTEXT_TICKS + 240,
        )
    out_path = args.out or (
        "ml/datasets/aeris_synthetic.csv"
        if args.task == "hazard"
        else "ml/datasets/aeris_forecast_synthetic.csv"
    )

    df = generate_dataset(
        ticks=ticks,
        episodes_per_scenario=args.episodes,
        seed=args.seed,
        task=args.task,
        horizon_minutes=args.horizon_minutes,
    )
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)
    print(f"Wrote {len(df)} rows x {len(df.columns)} cols to {out} (task={args.task}, ticks/episode={ticks})")
    if args.task == "forecast":
        baseline_mae = (df["future_risk_score"] - df["risk_score_now"]).abs().mean()
        print(f"Future level distribution:\n{df['future_risk_level'].value_counts()}")
        print(f"'No change' baseline MAE: {baseline_mae:.2f} risk points")
    else:
        print(df["label"].value_counts())


if __name__ == "__main__":
    main()
