"""Simulator unit tests — payload contract and scenario progression.

Run from backend/:  pytest ../iot/simulator -v
(or from iot/simulator: python -m pytest)
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from sensor_models import BASELINE, ScenarioState, SCENARIOS  # noqa: E402
from simulator import NODES, build_payload, topic_for  # noqa: E402


def test_all_devices_have_topology():
    assert set(NODES) == {f"NODE_0{i}" for i in range(1, 7)}


def test_topic_convention():
    assert topic_for("NODE_02") == "aeris/FACTORY_01/ZONE_02/telemetry"


def test_payload_matches_esp32_contract():
    payload = build_payload("NODE_01", type("F", (), {
        "temperature": 30.0, "humidity": 50.0, "co": 10, "methane": 60, "smoke": 40, "flame": False,
    })())
    assert payload["device_id"] == "NODE_01"
    assert payload["factory_id"] == "FACTORY_01"
    assert payload["zone_id"] == "ZONE_01"
    assert payload["zone_name"] == "Boiler Room"
    # All keys the backend schema requires:
    required = {"device_id", "factory_id", "zone_id", "zone_name", "temperature",
                "humidity", "co", "methane", "smoke", "flame", "timestamp"}
    assert required.issubset(payload.keys())
    json.dumps(payload)  # must be JSON-serializable


def test_unknown_scenario_rejected():
    try:
        ScenarioState("meteor_strike")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_gas_leak_progression_is_monotonic():
    """Methane must rise steadily once the leak starts — trend detection depends on it."""
    state = ScenarioState("gas_leak", seed=42)
    methane = [state.step().methane for _ in range(120)]
    early, late = sum(methane[15:25]) / 10, sum(methane[100:120]) / 20
    assert late > early + 300, f"leak not ramping (early={early}, late={late})"


def test_fire_scenario_trips_flame_and_smoke():
    state = ScenarioState("fire", seed=7)
    frames = [state.step() for _ in range(40)]
    assert any(f.flame for f in frames[25:])
    assert frames[-1].smoke > BASELINE["smoke"] + 200


def test_sensor_failure_is_constant_temperature():
    state = ScenarioState("sensor_failure", seed=1)
    temps = [state.step().temperature for _ in range(30)]
    # Stuck at 77.7 ± sensor noise (sigma=0.4); far from the 28C baseline.
    assert all(abs(t - 77.7) < 1.5 for t in temps)
    assert all(t > 70 for t in temps)
