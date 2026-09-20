"""HTTP gateway simulator.

A lightweight background task that keeps the demo alive: for every HTTP-registered
device it POSTs a telemetry frame to the backend every few seconds.  This gives
the Connections page something to show besides "silent / never" — an operator
can verify their HTTP gateway configuration by watching the register tick over
in real time.

The simulator lives in the same process as the backend.  It is started on
application startup and stopped on shutdown.  It is a *prototype* convenience,
not an architectural decision; production would run a real gateway.
"""
from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import httpx

from app.config.settings import settings

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = logging.getLogger("aeris.http_gateway")

# Normal-band ranges for a safe industrial environment.  The simulator stays
# inside these bounds so no alerts fire during a demo — unless the user has
# deliberately configured an anomaly in the simulator's cycle.
_SAFE = {
    "temperature": (22.0, 32.0),
    "humidity": (35.0, 65.0),
    "co": (5.0, 25.0),
    "methane": (100.0, 250.0),
    "smoke": (40.0, 120.0),
}

# Occasionally inject a spike so the dashboard isn't a flat line.
_SPIKE_CHANCE = 0.08


def _next_reading() -> dict:
    """Produce one telemetry frame with occasional normalised noise."""
    spike = random.random() < _SPIKE_CHANCE
    return {
        "temperature": round(random.uniform(*_SAFE["temperature"]) + (15 if spike else 0), 1),
        "humidity": round(random.uniform(*_SAFE["humidity"]), 1),
        "co": round(random.uniform(*_SAFE["co"]) + (80 if spike else 0), 1),
        "methane": round(random.uniform(*_SAFE["methane"]) + (400 if spike else 0), 1),
        "smoke": round(random.uniform(*_SAFE["smoke"]) + (200 if spike else 0), 1),
        "flame": spike and random.random() < 0.3,
    }


class HttpGatewaySimulator:
    """Periodically POSTs frames for every HTTP-registered device.

    The scheduler is deliberately naive: one asyncio task per device.  A real
    gateway would handle hundreds of nodes concurrently with a pool; for nine
    demo units this is fine and the per-device visibility makes the UI obvious.
    """

    def __init__(self) -> None:
        self._tasks: dict[int, asyncio.Task] = {}
        self._running = False

    @property
    def running(self) -> bool:
        return self._running

    def start(self) -> None:
        """Kick off the refresh loop.  Called from the app lifespan."""
        if self._running:
            return
        self._running = True
        asyncio.get_event_loop().create_task(self._refresh_loop())
        logger.info("HTTP gateway simulator started")

    def stop(self) -> None:
        for task in self._tasks.values():
            task.cancel()
        self._tasks.clear()
        self._running = False
        logger.info("HTTP gateway simulator stopped")

    async def _refresh_loop(self) -> None:
        """Every 15 seconds, reconcile running tasks with the live device list."""
        while self._running:
            try:
                await self._reconcile()
            except asyncio.CancelledError:
                return
            except Exception:  # noqa: BLE001
                logger.exception("HTTP gateway refresh failed")
            await asyncio.sleep(15)

    async def _reconcile(self) -> None:
        from sqlalchemy.orm import Session as SA  # noqa: PLC0415 — lazy to avoid cycle

        from app.database.session import SessionLocal  # noqa: PLC0415
        from app.models.models import Device, DeviceProtocol  # noqa: PLC0415

        with SessionLocal() as db:
            http_ids = {
                d.id
                for d in db.scalars(select(Device).where(Device.protocol == DeviceProtocol.HTTP.value)).all()
            }

        # Cancel tasks for devices that are no longer HTTP (disabled, deleted,
        # or switched to MQTT).
        for device_db_id in list(self._tasks):
            if device_db_id not in http_ids:
                self._tasks.pop(device_db_id).cancel()

        # Start a task for each new HTTP device.
        for device_db_id in http_ids - set(self._tasks):
            self._tasks[device_db_id] = asyncio.create_task(self._post_loop(device_db_id))

    async def _post_loop(self, device_db_id: int) -> None:
        """POST telemetry for one device until cancelled."""
        from app.database.session import SessionLocal  # noqa: PLC0415
        from app.models.models import Device, Zone  # noqa: PLC0415

        url = f"{settings.api_base_url.rstrip('/')}/api/telemetry"
        interval = 4.0 + random.random() * 3  # 4-7 s, jittered per device

        async with httpx.AsyncClient(timeout=8) as client:
            while True:
                try:
                    frame = self._build_frame(device_db_id)
                    resp = await client.post(url, json=frame)
                    if resp.status_code >= 400:
                        logger.warning(
                            "HTTP gateway POST %s -> %s: %s",
                            frame.get("device_id"), resp.status_code, resp.text[:200],
                        )
                except httpx.HTTPError as exc:
                    logger.debug("HTTP gateway POST failed: %s", exc)
                except asyncio.CancelledError:
                    return
                await asyncio.sleep(interval)

    def _build_frame(self, device_db_id: int) -> dict:
        """Build a JSON frame for one device, cached across calls."""
        from app.database.session import SessionLocal  # noqa: PLC0415
        from app.models.models import Device, Zone  # noqa: PLC0415

        with SessionLocal() as db:
            device = db.get(Device, device_db_id)
            zone = db.get(Zone, device.zone_id) if device else None
            factory_id = zone.factory.name if zone and zone.factory else "FACTORY_01"

        body = _next_reading()
        body["device_id"] = device.device_id if device else f"HTTP_DEV_{device_db_id}"
        body["factory_id"] = factory_id
        body["zone_id"] = zone.name.upper().replace(" ", "_") if zone else "UNKNOWN"
        body["timestamp"] = datetime.now(timezone.utc).isoformat()
        return body


# Module-level singleton — started/stopped by the app lifespan.
gateway_simulator = HttpGatewaySimulator()


def _build_frame(device_db_id: int) -> dict:
    """Standalone frame builder for tests (avoids asyncio)."""
    from app.database.session import SessionLocal  # noqa: PLC0415
    from app.models.models import Device, Zone  # noqa: PLC0415

    with SessionLocal() as db:
        device = db.get(Device, device_db_id)
        zone = db.get(Zone, device.zone_id) if device else None
        factory_id = zone.factory.name if zone and zone.factory else "FACTORY_01"

    body = _next_reading()
    body["device_id"] = device.device_id if device else f"HTTP_DEV_{device_db_id}"
    body["factory_id"] = factory_id
    body["zone_id"] = zone.name.upper().replace(" ", "_") if zone else "UNKNOWN"
    body["timestamp"] = datetime.now(timezone.utc).isoformat()
    return body


# Lazy import helper — avoids a circular import at module level.
from sqlalchemy import select  # noqa: E402
