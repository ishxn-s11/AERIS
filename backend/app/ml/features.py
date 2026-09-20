"""Online feature extraction for the AERIS processing pipeline.

Pipeline stages implemented here (per device, sliding time window):

1. Validation      — done upstream by the TelemetryIn schema (MQTT/HTTP gate).
2. Calibration     — hook point: `calibrate()` maps raw ADC counts to physical
                     units. PROTOTYPE: identity transform, because MQ-series
                     calibration is hardware-specific (documented in README).
3. Noise filtering — median-quantile filter: rates and moving averages are
                     computed from the median of the first/last few samples so
                     a single spiked frame cannot fake a trend.
4. Missing values  — samples are never interpolated; when the window holds too
                     little data the derived features degrade gracefully to 0
                     (rate) or the latest value (moving average) instead of
                     crashing or poisoning downstream logic.
5. Outlier detection — samples deviating >3 sigma (robust, via MAD) from the
                     window median are excluded from averages.
6. Moving averages — mean over the configured window for temp/gas/smoke/CO.
7. Rate of change  — robust units-per-minute slope of each key channel.
8. Feature extraction — one flat dict matching the ML feature contract.

Thread-safety: the MQTT network thread is the only writer; the health monitor
only reads counts. A simple lock is therefore sufficient (no asyncio needed).
"""
from __future__ import annotations

import statistics
import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.config.settings import settings


@dataclass
class ReadingSample:
    """One validated telemetry sample (already schema-checked upstream)."""
    timestamp: datetime
    temperature: float
    humidity: float
    co: float
    methane: float
    smoke: float
    flame: bool


# Feature contract shared with the ML training pipeline (order matters only
# for model I/O; lookups are by name).
FEATURE_NAMES = [
    "temperature", "humidity", "co", "methane", "smoke", "flame",
    "temperature_ma", "gas_ma", "smoke_ma", "co_ma",
    "temperature_rate", "gas_rate", "smoke_rate",
    "temperature_std",
]


def calibrate(raw: float, channel: str) -> float:
    """Calibration hook: raw ADC counts -> physical units.

    PROTOTYPE: identity transform. Real deployments must fit per-sensor curves
    (e.g. RS/R0 ratio for MQ-series) against a reference analyzer and store the
    coefficients in configuration. Keeping this as the single funnel means the
    change happens in exactly one place.
    """
    return raw


class DeviceWindow:
    """Time-bounded ring buffer of samples for one device."""

    def __init__(self, window_seconds: int | None = None, hard_cap: int = 600):
        self.window_seconds = window_seconds or settings.feature_window_seconds
        self._samples: deque[ReadingSample] = deque(maxlen=hard_cap)

    def append(self, sample: ReadingSample) -> None:
        # Normalize to UTC-aware so eviction math is always comparable
        # (client-supplied timestamps may be naive).
        if sample.timestamp.tzinfo is None:
            sample.timestamp = sample.timestamp.replace(tzinfo=timezone.utc)
        self._samples.append(sample)
        self._evict_expired()

    def as_list(self) -> list[ReadingSample]:
        self._evict_expired()
        return list(self._samples)

    def _evict_expired(self) -> None:
        if not self._samples:
            return
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=self.window_seconds)
        while self._samples and self._samples[0].timestamp < cutoff:
            self._samples.popleft()

    def __len__(self) -> int:
        return len(self._samples)


class FeatureStore:
    """Per-device windows with a lock for cross-thread access."""

    def __init__(self) -> None:
        self._windows: dict[str, DeviceWindow] = {}
        self._lock = threading.Lock()

    def append(self, device_id: str, sample: ReadingSample) -> list[ReadingSample]:
        with self._lock:
            window = self._windows.setdefault(device_id, DeviceWindow())
            window.append(sample)
            return window.as_list()

    def window(self, device_id: str) -> list[ReadingSample]:
        with self._lock:
            window = self._windows.get(device_id)
            return window.as_list() if window else []


# ---------------------------------------------------------------------------
# Filtering helpers
# ---------------------------------------------------------------------------
def _robust_outlier_mask(values: list[float], sigma: float = 3.0) -> list[bool]:
    """Keep values within `sigma` robust sigmas (MAD-based) of the median.

    MAD (median absolute deviation) is used instead of std because the spike
    itself would inflate std and hide the outlier it came from.
    """
    if len(values) < 8:
        return [True] * len(values)
    med = statistics.median(values)
    mad = statistics.median([abs(v - med) for v in values]) or 1e-9
    return [abs(v - med) <= sigma * 1.4826 * mad for v in values]


def _filtered_mean(values: list[float]) -> float:
    kept = [v for v, ok in zip(values, _robust_outlier_mask(values)) if ok]
    return statistics.fmean(kept) if kept else statistics.fmean(values)


def _robust_rate(samples: list[ReadingSample], getter) -> float:
    """Units-per-minute slope resistant to single-sample spikes.

    Uses median(last 3) - median(first 3) instead of last-first: one glitched
    frame in an otherwise flat series must not produce a huge fake rate.
    """
    if len(samples) < 4:
        return 0.0
    elapsed_min = (samples[-1].timestamp - samples[0].timestamp).total_seconds() / 60.0
    if elapsed_min < 0.25:  # <15s of coverage: slope meaningless
        return 0.0
    head = [getter(s) for s in samples[:3]]
    tail = [getter(s) for s in samples[-3:]]
    return (statistics.median(tail) - statistics.median(head)) / elapsed_min


def _std(values: list[float]) -> float:
    return statistics.pstdev(values) if len(values) >= 2 else 0.0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def compute_features(history: list[ReadingSample]) -> dict:
    """Turn a device's recent samples into the pipeline feature dict.

    Missing-value policy: with insufficient samples, rates are 0.0 and moving
    averages fall back to the latest raw value — downstream rules then see a
    conservative "no trend" picture rather than fabricated dynamics.
    """
    if not history:
        return {name: 0.0 for name in FEATURE_NAMES}

    latest = history[-1]
    temps = [calibrate(s.temperature, "temperature") for s in history]
    gases = [calibrate(s.methane, "methane") for s in history]
    cos = [calibrate(s.co, "co") for s in history]
    smokes = [calibrate(s.smoke, "smoke") for s in history]

    return {
        "temperature": latest.temperature,
        "humidity": latest.humidity,
        "co": latest.co,
        "methane": latest.methane,
        "smoke": latest.smoke,
        "flame": 1.0 if latest.flame else 0.0,
        "temperature_ma": _filtered_mean(temps),
        "gas_ma": _filtered_mean(gases),
        "smoke_ma": _filtered_mean(smokes),
        "co_ma": _filtered_mean(cos),
        "temperature_rate": _robust_rate(history, lambda s: s.temperature),
        "gas_rate": _robust_rate(history, lambda s: s.methane),
        "smoke_rate": _robust_rate(history, lambda s: s.smoke),
        "temperature_std": _std(temps),
    }


# Module-level store used by the ingestion pipeline (one process-wide instance).
feature_store = FeatureStore()
