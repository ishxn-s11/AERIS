"""Live connectivity probes for IoT transports.

An onboarding page that only renders a saved host and port is useless: the
first question an engineer asks is "does the broker actually answer, and will
it accept this credential?". This module answers that from the backend, in two
stages, so a failure says *where* it failed:

  1. TCP (or TLS) reachability — routing, firewall, wrong port.
  2. MQTT CONNECT/CONNACK — broker alive, credential accepted, ACL usable.

The probe never subscribes or publishes: it connects, reads the return code and
disconnects, so it is safe to run against a production broker.
"""
from __future__ import annotations

import contextlib
import logging
import socket
import ssl
import threading
import time
from dataclasses import dataclass, field

import paho.mqtt.client as mqtt

logger = logging.getLogger("aeris.connections")

DEFAULT_TIMEOUT = 4.0

# paho CONNACK return codes worth naming — a bare number is not actionable.
CONNACK_REASONS: dict[int, str] = {
    1: "unacceptable protocol version",
    2: "client identifier rejected",
    3: "broker unavailable",
    4: "bad username or password",
    5: "not authorised (check the ACL)",
}


@dataclass
class ProbeResult:
    """Outcome of a broker probe, shaped for direct JSON serialisation."""

    ok: bool
    host: str
    port: int
    use_tls: bool = False
    stage: str = "tcp"
    latency_ms: float | None = None
    detail: str = ""
    connack_code: int | None = None
    steps: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "host": self.host,
            "port": self.port,
            "use_tls": self.use_tls,
            "stage": self.stage,
            "latency_ms": round(self.latency_ms, 1) if self.latency_ms is not None else None,
            "detail": self.detail,
            "connack_code": self.connack_code,
            "steps": self.steps,
        }


def _tcp_step(host: str, port: int, timeout: float, use_tls: bool) -> tuple[bool, float, str]:
    """Open a socket to the broker. Returns (ok, elapsed_ms, detail)."""
    started = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            if use_tls:
                context = ssl.create_default_context()
                with context.wrap_socket(sock, server_hostname=host) as tls:
                    tls.getpeercert()  # forces the handshake to complete
        return True, (time.perf_counter() - started) * 1000.0, ""
    except ssl.SSLError as exc:
        return False, (time.perf_counter() - started) * 1000.0, f"TLS handshake failed: {exc}"
    except socket.timeout:
        return False, (time.perf_counter() - started) * 1000.0, (
            f"Timed out after {timeout:.1f}s — host unreachable or port filtered"
        )
    except OSError as exc:
        return False, (time.perf_counter() - started) * 1000.0, str(exc)


def _mqtt_step(
    host: str,
    port: int,
    timeout: float,
    username: str | None,
    password: str | None,
    use_tls: bool,
    client_id: str,
    outcome: dict,
) -> None:
    """Run the MQTT handshake in a worker thread and record the outcome.

    Uses `connect_async` + `loop_start` and waits on an event, NOT the blocking
    `connect()`. That distinction is the whole probe: paho's synchronous
    `connect()` returns as soon as the CONNECT packet is written, and only the
    network loop reads the CONNACK back. Without a loop the broker answers
    happily (a raw CONNECT gets `20 02 00 00`) while `is_connected()` stays
    False — so the probe reported a perfectly healthy broker as refusing the
    connection. This is the same pattern the real subscriber uses.

    Runs off-thread so a broker that accepts the socket but never replies
    cannot hang the request.
    """
    started = time.perf_counter()
    settled = threading.Event()
    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=client_id,
        protocol=mqtt.MQTTv311,
    )
    if username:
        client.username_pw_set(username, password)
    if use_tls:
        client.tls_set()

    def _on_connect(_client, _userdata, _flags, reason_code, _properties=None):
        outcome["connack"] = int(getattr(reason_code, "value", reason_code) or 0)
        outcome["connack_name"] = str(reason_code.getName()) if hasattr(reason_code, "getName") else "unknown"
        outcome["elapsed_ms"] = (time.perf_counter() - started) * 1000.0
        settled.set()

    client.on_connect = _on_connect
    try:
        client.connect_async(host, port, keepalive=15)
        client.loop_start()
        if not settled.wait(timeout):
            outcome["timed_out"] = True
            outcome["elapsed_ms"] = (time.perf_counter() - started) * 1000.0
    except Exception as exc:  # noqa: BLE001 — probe must never raise upward
        outcome["error"] = f"{type(exc).__name__}: {exc}"
        outcome["elapsed_ms"] = (time.perf_counter() - started) * 1000.0
    finally:
        with contextlib.suppress(Exception):
            client.loop_stop()
        with contextlib.suppress(Exception):
            client.disconnect()


def probe_mqtt_broker(
    host: str,
    port: int,
    *,
    username: str | None = None,
    password: str | None = None,
    use_tls: bool = False,
    timeout: float = DEFAULT_TIMEOUT,
    client_id: str = "aeris-conncheck",
) -> ProbeResult:
    """Probe a broker end to end and report the first failing stage."""
    result = ProbeResult(ok=False, host=host, port=port, use_tls=use_tls)

    tcp_ok, tcp_ms, tcp_detail = _tcp_step(host, port, timeout, use_tls)
    result.steps.append({
        "name": "TCP reachability" if not use_tls else "TLS reachability",
        "ok": tcp_ok,
        "ms": round(tcp_ms, 1),
        "detail": tcp_detail,
    })
    if not tcp_ok:
        result.stage = "tcp"
        result.latency_ms = tcp_ms
        result.detail = tcp_detail
        return result

    outcome: dict = {}
    worker = threading.Thread(
        target=_mqtt_step,
        args=(host, port, timeout, username, password, use_tls, client_id, outcome),
        daemon=True,
    )
    worker.start()
    worker.join(timeout)

    handshake_ms = outcome.get("elapsed_ms")
    result.stage = "mqtt"
    result.latency_ms = tcp_ms

    def _fail(detail: str) -> ProbeResult:
        result.ok = False
        result.detail = detail
        result.steps.append({
            "name": "MQTT handshake",
            "ok": False,
            "ms": round(handshake_ms, 1) if handshake_ms is not None else None,
            "detail": detail,
        })
        return result

    if outcome.get("error"):
        return _fail(outcome["error"])

    if outcome.get("timed_out") or worker.is_alive():
        return _fail(
            f"Socket opened but the broker sent no CONNACK within {timeout:.1f}s"
        )

    code = outcome.get("connack")
    result.connack_code = code
    if code != 0:
        name = outcome.get("connack_name", "unknown reason")
        return _fail(f"CONNACK: {name}")

    result.ok = True
    result.detail = (
        f"Broker accepted the connection in {(handshake_ms or tcp_ms):.0f} ms"
        + (f" as '{username}'" if username else " (anonymous)")
    )
    result.steps.append({
        "name": "MQTT handshake",
        "ok": True,
        "ms": round(handshake_ms or 0, 1),
        "detail": "",
    })
    return result


def active_broker_report() -> dict:
    """Describe the broker this backend is configured against, and its live state.

    Imported lazily so the API module does not pull the MQTT loop at import
    time (tests and tooling import the app without a broker).
    """
    from app.config.settings import settings
    from app.mqtt.subscriber import get_active_subscriber, telemetry_rejections

    subscriber = get_active_subscriber()
    connected = subscriber.wait_for_connection(timeout=0) if subscriber else False
    return {
        "host": settings.mqtt_host,
        "port": settings.mqtt_port,
        "use_tls": settings.mqtt_tls,
        "auth_mode": "username/password" if settings.mqtt_username else "anonymous",
        "username": settings.mqtt_username,
        "topic_prefix": settings.mqtt_topic_prefix,
        "client_id": subscriber.client_id if subscriber else settings.mqtt_client_id,
        "subscribe_filter": (
            subscriber.telemetry_filter if subscriber else f"{settings.mqtt_topic_prefix}/+/+/telemetry"
        ),
        "qos": 1,
        "connected": connected,
        "transport": "tcp",
        "rejected_frames": telemetry_rejections,
        "note": (
            "Broker configuration is environment-driven (MQTT_HOST/MQTT_PORT/…); "
            "this page verifies it rather than editing it."
        ),
    }
