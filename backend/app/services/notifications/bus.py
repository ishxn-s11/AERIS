"""Alert fan-out bus.

Publishes alert events so notification channels can be consumed out of process
(one Redis channel, `aeris:alerts` by default). Deliberate degradation:

* `REDIS_URL` unset, or Redis unreachable -> `mode="in_process"`: events are
  handed straight to the in-process dispatcher. The prototype demo therefore
  works with or without Redis, and the mode is always reported to the operator
  (never silently pretended to be "distributed").
* Publishing happens on the MQTT thread, so the sync client is used; consuming
  happens on the event loop via `redis.asyncio`.

Redis is a fan-out transport, not a queue: alerts are already durably stored in
PostgreSQL by the time they are published, so a dropped message costs a
notification, never the record of truth.
"""
from __future__ import annotations

import json
import logging
import threading

from app.config.settings import settings
from app.services.alert_service import AlertPayload

logger = logging.getLogger("aeris.notify.bus")


class AlertBus:
    def __init__(self, dispatcher=None, url: str | None = None, channel: str | None = None):
        self.channel = channel or settings.alert_fanout_channel
        self.url = url if url is not None else settings.redis_url
        self.dispatcher = dispatcher
        self._client = None
        self._lock = threading.Lock()
        self._connected = False
        self.published = 0
        self.failed = 0

    # -- lifecycle ---------------------------------------------------------
    def connect(self) -> bool:
        """Try to establish the Redis connection. Never raises."""
        if not self.url:
            logger.info("REDIS_URL not set — alert bus running in in-process mode")
            return False
        try:
            import redis  # imported lazily so the dependency stays optional

            client = redis.Redis.from_url(self.url, decode_responses=True)
            client.ping()
            self._client = client
            self._connected = True
            logger.info("Alert bus connected to Redis at %s (channel=%s)", self.url, self.channel)
            return True
        except Exception as exc:  # noqa: BLE001 — redis is optional by design
            self._connected = False
            self._client = None
            logger.warning("Alert bus unavailable (%s) — falling back to in-process dispatch", exc)
            return False

    def close(self) -> None:
        client = self._client
        self._client = None
        self._connected = False
        if client is not None:
            try:
                client.close()
            except Exception:  # noqa: BLE001
                pass

    @property
    def mode(self) -> str:
        return "redis" if self._connected else "in_process"

    # -- publish -----------------------------------------------------------
    def publish(self, payload: AlertPayload) -> None:
        message = json.dumps(payload.to_dict())
        client = self._client
        if self._connected and client is not None:
            try:
                client.publish(self.channel, message)
                self.published += 1
                return
            except Exception as exc:  # noqa: BLE001 — degrade rather than lose the alert
                logger.warning("Redis publish failed (%s) — dispatching in-process", exc)
                self._connected = False
                self.failed += 1
        self._dispatch_local(payload)

    def _dispatch_local(self, payload: AlertPayload) -> None:
        if self.dispatcher is None:
            return
        self.published += 1
        self.dispatcher.dispatch_sync(payload)

    # -- consume -----------------------------------------------------------
    async def messages(self):
        """Async generator of AlertPayload received from Redis.

        Ends immediately when the bus is not connected, so the caller's task
        simply completes instead of spinning.
        """
        if not self._connected or not self.url:
            return
        import redis.asyncio as aioredis

        client = aioredis.Redis.from_url(self.url, decode_responses=True)
        pubsub = client.pubsub()
        await pubsub.subscribe(self.channel)
        logger.info("Notification worker listening on %s", self.channel)
        try:
            async for message in pubsub.listen():
                if message.get("type") != "message":
                    continue
                try:
                    yield AlertPayload.from_dict(json.loads(message["data"]))
                except Exception:  # noqa: BLE001 — a bad frame must not kill the worker
                    logger.exception("Discarding malformed bus message")
        finally:
            await pubsub.unsubscribe(self.channel)
            await pubsub.close()
            await client.close()

    def status(self) -> dict:
        with self._lock:
            return {
                "mode": self.mode,
                "channel": self.channel,
                "redis_url_configured": bool(self.url),
                "published": self.published,
                "publish_failures": self.failed,
            }
