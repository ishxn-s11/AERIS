"""Sensor models and scenario generators for the AERIS simulator.

Each scenario models the *physical progression* of an incident (gas plume
building up, fire ramping) rather than jumping straight to threshold values, so
the trend-detection pipeline has realistic gradients to work with.

All values are in the same ADC-count units as the real MQ sensors (0-1023) and
°C / %RH for DHT22 — hardware and simulator are interchangeable downstream.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field

# Ambient baseline for a healthy industrial zone.
BASELINE = {
    "temperature": 28.0,
    "humidity": 45.0,
    "co": 12.0,
    "methane": 60.0,
    "smoke": 40.0,
}


@dataclass
class SensorFrame:
    temperature: float
    humidity: float
    co: float
    methane: float
    smoke: float
    flame: bool
    # Optional wall-clock stamp attached by the dataset generator / simulator;
    # the live publisher stamps payloads at MQTT time.
    timestamp: object | None = None


class ScenarioState:
    """Mutable state machine driving one scenario on one device."""

    def __init__(self, scenario: str, seed: int | None = None):
        self.scenario = scenario
        self.tick = 0
        self.rng = random.Random(seed)
        self._state: dict = {}
        self._init_state()

    # -- helpers ------------------------------------------------------------
    def _noise(self, scale: float) -> float:
        return self.rng.gauss(0, scale)

    def _clamp(self, value: float, low: float, high: float) -> float:
        return max(low, min(high, value))

    def _frame(self, temp: float, hum: float, co: float, ch4: float, smoke: float, flame: bool = False) -> SensorFrame:
        """Apply sensor noise + physical clamps to a target state."""
        return SensorFrame(
            temperature=round(self._clamp(temp + self._noise(0.4), -20, 140), 1),
            humidity=round(self._clamp(hum + self._noise(1.5), 5, 100), 1),
            co=round(self._clamp(co + self._noise(4), 0, 1023), 0),
            methane=round(self._clamp(ch4 + self._noise(8), 0, 1023), 0),
            smoke=round(self._clamp(smoke + self._noise(6), 0, 1023), 0),
            flame=flame,
        )

    # -- scenario setup -------------------------------------------------------
    def _init_state(self) -> None:
        if self.scenario == "normal":
            self._state = {"temp": BASELINE["temperature"], "methane": BASELINE["methane"]}
        elif self.scenario == "gas_leak":
            # Leak starts at tick 15, ramps ~7 counts/s to simulate a plume.
            self._state = {"temp": BASELINE["temperature"], "methane": BASELINE["methane"]}
        elif self.scenario == "fire":
            # Fire ignites at tick 10; temp + smoke ramp, flame trips at ~tick 25.
            self._state = {"temp": BASELINE["temperature"], "smoke": BASELINE["smoke"]}
        elif self.scenario == "overheat":
            # Rapid temperature rise, gases stay near baseline (bearing failure).
            self._state = {"temp": BASELINE["temperature"]}
        elif self.scenario == "smoke_increase":
            self._state = {"smoke": BASELINE["smoke"]}
        elif self.scenario == "sensor_failure":
            # A wedged ADC/stale firmware buffer: ALL channels frozen at their
            # first readings with near-zero jitter (the all-channel flatline is
            # the malfunction signature the backend detects).
            self._state = {
                "frozen": {
                    "temperature": 77.7,
                    "humidity": BASELINE["humidity"],
                    "co": BASELINE["co"],
                    "methane": BASELINE["methane"],
                    "smoke": BASELINE["smoke"],
                }
            }
        else:
            raise ValueError(f"Unknown scenario '{self.scenario}'")

    # -- per-tick evolution ----------------------------------------------------
    def step(self) -> SensorFrame:
        self.tick += 1
        s = self._state
        sc = self.scenario

        if sc == "normal":
            return self._frame(
                temp=BASELINE["temperature"] + self._noise(1.0),
                hum=BASELINE["humidity"] + self._noise(2.0),
                co=BASELINE["co"] + self._noise(3),
                ch4=BASELINE["methane"] + self._noise(6),
                smoke=BASELINE["smoke"] + self._noise(5),
            )

        if sc == "gas_leak":
            ramp = max(0, self.tick - 15) * 7.0
            ch4 = min(BASELINE["methane"] + ramp, 900)
            # Late-stage: rising temperature as combustion risk grows.
            temp = BASELINE["temperature"] + max(0, self.tick - 60) * 0.3
            return self._frame(
                temp=temp, hum=BASELINE["humidity"], co=BASELINE["co"] + ramp * 0.05,
                ch4=ch4, smoke=BASELINE["smoke"] + ramp * 0.1,
            )

        if sc == "fire":
            ramp = max(0, self.tick - 10)
            temp = BASELINE["temperature"] + ramp * 1.8
            smoke = BASELINE["smoke"] + ramp * 14
            flame = self.tick >= 25
            return self._frame(
                temp=temp, hum=max(20, BASELINE["humidity"] - ramp * 0.3),
                co=BASELINE["co"] + ramp * 0.9, ch4=BASELINE["methane"],
                smoke=smoke, flame=flame,
            )

        if sc == "overheat":
            ramp = max(0, self.tick - 8) * 1.4
            return self._frame(
                temp=BASELINE["temperature"] + ramp, hum=BASELINE["humidity"],
                co=BASELINE["co"] + ramp * 0.15, ch4=BASELINE["methane"] + self._noise(6),
                smoke=BASELINE["smoke"] + ramp * 0.4,
            )

        if sc == "smoke_increase":
            ramp = max(0, self.tick - 12) * 10
            return self._frame(
                temp=BASELINE["temperature"], hum=BASELINE["humidity"],
                co=BASELINE["co"], ch4=BASELINE["methane"],
                smoke=min(BASELINE["smoke"] + ramp, 800),
            )

        # sensor_failure: every channel pinned to the frozen buffer values.
        f = s["frozen"]
        return SensorFrame(
            temperature=round(f["temperature"] + self._noise(0.05), 1),
            humidity=round(f["humidity"] + self._noise(0.05), 1),
            co=round(f["co"] + self._noise(0.05), 0),
            methane=round(f["methane"] + self._noise(0.05), 0),
            smoke=round(f["smoke"] + self._noise(0.05), 0),
            flame=False,
        )


SCENARIOS = ("normal", "gas_leak", "fire", "overheat", "smoke_increase", "sensor_failure")
