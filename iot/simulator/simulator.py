"""AERIS sensor simulator CLI.

Publishes telemetry to MQTT exactly like an ESP32 node, so hardware and
simulation are interchangeable for everything downstream.

Usage:
    python simulator.py                          # all nodes, normal conditions
    python simulator.py --device NODE_02 --scenario gas_leak
    python simulator.py --device NODE_03 --scenario fire --interval 1
    python simulator.py --list-nodes
"""
from __future__ import annotations

import argparse
import json
import logging
import socket
import sys
import time
from datetime import datetime, timezone

import paho.mqtt.client as mqtt

from sensor_models import BASELINE, SensorFrame, ScenarioState, SCENARIOS

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-7s | %(message)s")
logger = logging.getLogger("aeris.simulator")

# Mirrors backend/app/database/seed.py SEED_FACTORIES (both demo factories, so
# the multi-factory console has live data for whichever site is selected).
NODES: dict[str, dict] = {
    "NODE_01": {"factory_id": "FACTORY_01", "zone_id": "ZONE_01", "zone_name": "Boiler Room"},
    "NODE_02": {"factory_id": "FACTORY_01", "zone_id": "ZONE_02", "zone_name": "Chemical Storage"},
    "NODE_03": {"factory_id": "FACTORY_01", "zone_id": "ZONE_03", "zone_name": "Generator Room"},
    "NODE_04": {"factory_id": "FACTORY_01", "zone_id": "ZONE_04", "zone_name": "Pipeline Section"},
    "NODE_05": {"factory_id": "FACTORY_01", "zone_id": "ZONE_05", "zone_name": "Production Area"},
    "NODE_06": {"factory_id": "FACTORY_01", "zone_id": "ZONE_06", "zone_name": "Warehouse"},
    "NODE_11": {"factory_id": "FACTORY_02", "zone_id": "ZONE_11", "zone_name": "Loading Bay"},
    "NODE_12": {"factory_id": "FACTORY_02", "zone_id": "ZONE_12", "zone_name": "Tank Farm"},
    "NODE_13": {"factory_id": "FACTORY_02", "zone_id": "ZONE_13", "zone_name": "Control Room"},
}

# Default factory for callers that do not pick a node (kept for compatibility
# with the Phase 1 docs and scripts that import FACTORY_ID).
FACTORY_ID = "FACTORY_01"


def build_payload(device_id: str, frame: SensorFrame) -> dict:
    """Telemetry envelope — identical contract to the ESP32 firmware JSON."""
    node = NODES[device_id]
    return {
        "device_id": device_id,
        "factory_id": node["factory_id"],
        "zone_id": node["zone_id"],
        "zone_name": node["zone_name"],
        "temperature": frame.temperature,
        "humidity": frame.humidity,
        "co": frame.co,
        "methane": frame.methane,
        "smoke": frame.smoke,
        "flame": frame.flame,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def topic_for(device_id: str) -> str:
    node = NODES[device_id]
    return f"aeris/{node['factory_id']}/{node['zone_id']}/telemetry"


class SimulatedNode:
    """One virtual ESP32 publishing a scenario at a fixed interval."""

    def __init__(self, device_id: str, scenario: str, broker: str, port: int,
                 interval: float, seed: int | None = None, publish: bool = True,
                 username: str | None = None, password: str | None = None):
        self.device_id = device_id
        self.scenario = scenario
        self.interval = interval
        self.state = ScenarioState(scenario, seed=seed)
        self.publish_enabled = publish
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"aeris-sim-{device_id}")
        # Per-device broker credentials when the broker enforces auth
        # (see backend/scripts/sync_mqtt_credentials.py).
        if username:
            self.client.username_pw_set(username, password)
        if broker:
            self.client.connect_async(broker, port, keepalive=60)
        self.client.loop_start()

    def tick(self) -> dict:
        frame = self.state.step()
        payload = build_payload(self.device_id, frame)
        if self.publish_enabled:
            info = self.client.publish(topic_for(self.device_id), json.dumps(payload), qos=1)
            if info.rc != mqtt.MQTT_ERR_SUCCESS:
                logger.warning("%s publish failed (rc=%s)", self.device_id, info.rc)
        return payload

    def stop(self) -> None:
        self.client.loop_stop()
        self.client.disconnect()


def run_forever(nodes: list[SimulatedNode], once: bool = False, print_payloads: bool = True,
                duration: float | None = None) -> None:
    """Drive all nodes; `once` runs a single tick (used by tests).

    `duration` bounds the run in seconds (demo/CI convenience) — the process
    then exits cleanly instead of requiring an external kill.
    """
    deadline = time.monotonic() + duration if duration else None
    try:
        while True:
            for node in nodes:
                payload = node.tick()
                if print_payloads:
                    logger.info(
                        "%s [%s] T=%s CH4=%s Smoke=%s CO=%s Flame=%s",
                        node.device_id, node.scenario, payload["temperature"], payload["methane"],
                        payload["smoke"], payload["co"], payload["flame"],
                    )
            if once:
                break
            if deadline and time.monotonic() >= deadline:
                logger.info("Duration reached (%.0fs) — stopping", duration)
                break
            time.sleep(nodes[0].interval if nodes else 1)
    except KeyboardInterrupt:
        logger.info("Simulator stopped by user")
    finally:
        for node in nodes:
            node.stop()


def _load_credentials(path: str | None) -> dict[str, tuple[str | None, str | None]]:
    """Read a `device_id,username,password` CSV into a lookup map.

    Returns an empty map when no file is given or it cannot be read — the
    simulator then behaves exactly as before (anonymous broker).
    """
    if not path:
        return {}
    try:
        with open(path, encoding="utf-8") as handle:
            rows = [line.strip().split(",") for line in handle if line.strip() and not line.startswith("#")]
    except OSError as exc:
        logger.warning("Could not read credentials file %s (%s) — using anonymous MQTT", path, exc)
        return {}
    return {row[0]: (row[1], row[2]) for row in rows if len(row) >= 3}


def main() -> None:
    parser = argparse.ArgumentParser(description="AERIS IoT sensor simulator")
    parser.add_argument("--host", default=socket.gethostbyname(socket.gethostname()) if False else "localhost",
                        help="MQTT broker host (default: localhost)")
    parser.add_argument("--port", type=int, default=1883)
    parser.add_argument("--device", help="single device (e.g. NODE_02); default: all nodes")
    parser.add_argument("--scenario", choices=SCENARIOS, default="normal")
    parser.add_argument("--interval", type=float, default=2.0, help="seconds between frames")
    parser.add_argument("--duration", type=float, default=None,
                        help="auto-stop after N seconds (handy for demos/CI)")
    parser.add_argument("--list-nodes", action="store_true")
    parser.add_argument("--quiet", action="store_true", help="suppress per-frame console logging (publishing is unaffected)")
    parser.add_argument("--username", default=None,
                        help="broker username (per-device when the broker enforces auth)")
    parser.add_argument("--password", default=None, help="broker password")
    parser.add_argument("--credentials-file", default=None,
                        help="CSV (username,password) generated by sync_mqtt_credentials.py")
    args = parser.parse_args()

    if args.list_nodes:
        for node_id, meta in NODES.items():
            print(f"{node_id} -> {meta['factory_id']} {meta['zone_id']} {meta['zone_name']}")
        return

    device_ids = [args.device] if args.device else list(NODES)
    for device_id in device_ids:
        if device_id not in NODES:
            parser.error(f"Unknown device '{device_id}'. Use --list-nodes.")

    # Credentials: explicit flags win; otherwise look each node up in the
    # generated credentials file so an authenticated broker works out of the box.
    device_credentials = _load_credentials(args.credentials_file)

    nodes = [
        SimulatedNode(
            device_id, args.scenario, args.host, args.port, args.interval,
            publish=True,
            username=args.username or device_credentials.get(device_id, (None, None))[0],
            password=args.password or device_credentials.get(device_id, (None, None))[1],
        )
        for device_id in device_ids
    ]
    logger.info("Simulating %d node(s): %s (scenario=%s, interval=%.1fs)",
                len(nodes), ", ".join(device_ids), args.scenario, args.interval)
    run_forever(nodes, print_payloads=not args.quiet, duration=args.duration)


if __name__ == "__main__":
    main()
