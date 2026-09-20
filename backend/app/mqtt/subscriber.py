"""MQTT subscriber — the bridge between the IoT layer and the backend.

Design:
- paho-mqtt runs its network loop in a daemon thread (production-like: ingestion
  is decoupled from HTTP request handling).
- Payloads are validated with the TelemetryIn schema; invalid frames are logged
  and dropped (fail-closed behaviour for a safety system).
- Processing (DB write) happens in a short-lived session per message.
- The broker client id is made unique per process. MQTT brokers allow only one
  live connection per client id and silently kick the previous one ("session
  taken over"), so a shared id turns two backends into a fight that drops
  telemetry. Uniqueness is correctness here, not tidiness.
"""
import json
import logging
import secrets
import socket
import threading

import paho.mqtt.client as mqtt
from sqlalchemy.exc import SQLAlchemyError

from app.config.settings import settings
from app.database.session import SessionLocal
from app.schemas.schemas import TelemetryIn
from app.services.device_auth_service import DisabledDeviceError, UnregisteredDeviceError
from app.services.telemetry_service import UnknownFactoryError, ingest_telemetry

logger = logging.getLogger("aeris.mqtt")

# Operational counters (also useful as a future Prometheus metric): frames
# dropped because the device is not pre-registered or has been disabled.
telemetry_rejections = 0

# Process-wide handle on the running subscriber. Status/onboarding endpoints
# need to report *live* broker state (connected? which filter?) without
# constructing a second client — the broker kicks duplicate client ids, so a
# throwaway probe client here would disconnect the real ingestion loop.
_active_subscriber: "AerisMqttSubscriber | None" = None


def set_active_subscriber(subscriber: "AerisMqttSubscriber") -> None:
    global _active_subscriber
    _active_subscriber = subscriber


def get_active_subscriber() -> "AerisMqttSubscriber | None":
    return _active_subscriber


def topic_for_device(prefix: str, factory_id: str, zone_id: str) -> str:
    return f"{prefix}/{factory_id}/{zone_id}/telemetry"


# MQTT v3.1 caps client ids at 23 bytes; v3.1.1 brokers enforce it too.
MAX_CLIENT_ID_BYTES = 23


def build_client_id(base: str | None = None, *, token: str | None = None) -> str:
    """Broker-unique client id: `<configured base>-<host>-<token>`.

    Keeps the operator-visible prefix from `.env` while guaranteeing that a
    second instance (a dev backend next to a container, a rolling restart
    overlapping the old pod) cannot evict the first one's session — the broker
    permits one live connection per client id and silently kicks the older one.

    The uniqueness suffix is reserved first and the readable prefix is clipped
    to fit: trimming a finished id from the right would discard exactly the part
    that guarantees uniqueness. `token` is injectable so tests stay deterministic.
    """
    host = (socket.gethostname().split(".")[0] or "host")[:4]
    tail = f"-{host}-{token or secrets.token_hex(2)}"
    prefix = (base or settings.mqtt_client_id)[: max(4, MAX_CLIENT_ID_BYTES - len(tail))]
    return f"{prefix}{tail}"


class AerisMqttSubscriber:
    def __init__(self) -> None:
        self.prefix = settings.mqtt_topic_prefix
        # Subscribe to every zone: aeris/+/+/telemetry
        self.telemetry_filter = f"{self.prefix}/+/+/telemetry"
        self.client_id = build_client_id()
        self.client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=self.client_id,
            protocol=mqtt.MQTTv311,
        )
        if settings.mqtt_username:
            self.client.username_pw_set(settings.mqtt_username, settings.mqtt_password)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.on_disconnect = self._on_disconnect
        self._connected = threading.Event()

    # -- paho callbacks -----------------------------------------------------
    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        if reason_code == 0:
            client.subscribe(self.telemetry_filter, qos=1)
            self._connected.set()
            logger.info(
                "MQTT connected as '%s'; subscribed to '%s'", self.client_id, self.telemetry_filter
            )
        else:
            logger.error("MQTT connection failed: %s", reason_code)

    def _on_disconnect(self, client, userdata, flags, reason_code, properties=None):
        self._connected.clear()
        logger.warning("MQTT disconnected (reason=%s); client will auto-reconnect", reason_code)

    def _on_message(self, client, userdata, msg: mqtt.MQTTMessage) -> None:
        global telemetry_rejections
        try:
            frame = json.loads(msg.payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            logger.warning("Dropping malformed JSON on %s", msg.topic)
            return
        try:
            telemetry = TelemetryIn.model_validate(frame)
        except Exception as exc:  # noqa: BLE001 — boundary must never crash the loop
            logger.warning("Telemetry validation failed on %s: %s", msg.topic, exc)
            return

        parts = msg.topic.split("/")
        factory_id = parts[1] if len(parts) >= 4 else telemetry.factory_id

        try:
            with SessionLocal() as db:
                telemetry.factory_id = factory_id  # topic is authoritative
                # The observed topic is recorded on the device so broker ACLs can
                # be regenerated from what the node actually publishes on.
                ingest_telemetry(db, telemetry, observed_topic=msg.topic)
        except UnknownFactoryError as exc:
            logger.warning("Rejected telemetry: %s", exc)
        except (UnregisteredDeviceError, DisabledDeviceError) as exc:
            # Registration policy rejections are expected events in strict mode,
            # not faults: log at warning level with the reason.
            logger.warning("Rejected unregistered/disabled device: %s", exc)
            telemetry_rejections += 1
        except SQLAlchemyError:
            logger.exception("Database error while ingesting telemetry from %s", telemetry.device_id)
        except Exception:  # noqa: BLE001
            logger.exception("Unexpected error ingesting telemetry from %s", telemetry.device_id)

    # -- lifecycle -----------------------------------------------------------
    def start(self) -> None:
        self.client.connect_async(settings.mqtt_host, settings.mqtt_port, keepalive=60)
        self.client.loop_start()  # background daemon thread with auto-reconnect
        logger.info("MQTT subscriber starting -> %s:%d", settings.mqtt_host, settings.mqtt_port)

    def stop(self) -> None:
        self.client.loop_stop()
        self.client.disconnect()
        logger.info("MQTT subscriber stopped")

    def wait_for_connection(self, timeout: float = 10.0) -> bool:
        return self._connected.wait(timeout)
