"""WebSocket connection manager — fan-out of telemetry and alerts to dashboards.

Kept dependency-free of FastAPI internals so services can broadcast without a
request context (MQTT thread → asyncio bridge uses the manager's loop handle).
"""
import asyncio
import logging
from collections import defaultdict
from datetime import datetime, timezone

from fastapi import WebSocket

logger = logging.getLogger("aeris.ws")


class ConnectionManager:
    def __init__(self) -> None:
        self.active_connections: list[WebSocket] = []
        # Per-subscriber topic filter: subscriber id -> {"zones": {zone names}}
        self.subscriptions: dict[int, dict] = {}
        self._sub_seq = 0
        # Main asyncio loop handle — set during app startup. MQTT threads have
        # no running loop of their own, so broadcasts from ingestion must be
        # scheduled onto THIS loop explicitly.
        self._main_loop: asyncio.AbstractEventLoop | None = None

    def set_main_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._main_loop = loop

    async def connect(self, websocket: WebSocket) -> int:
        await websocket.accept()
        self.active_connections.append(websocket)
        self._sub_seq += 1
        self.subscriptions[self._sub_seq] = {"zones": set()}
        websocket.state.sub_id = self._sub_seq
        logger.info("Dashboard connected (%d total)", len(self.active_connections))
        return self._sub_seq

    def disconnect(self, websocket: WebSocket) -> None:
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        sub_id = getattr(websocket.state, "sub_id", None)
        self.subscriptions.pop(sub_id, None)
        logger.info("Dashboard disconnected (%d total)", len(self.active_connections))

    def set_zone_filter(self, websocket: WebSocket, zones: set[str]) -> None:
        sub_id = getattr(websocket.state, "sub_id", None)
        if sub_id in self.subscriptions:
            self.subscriptions[sub_id]["zones"] = zones

    async def broadcast(self, message: dict) -> None:
        """Send a JSON message to every connected dashboard."""
        if not self.active_connections:
            return
        payload = {**message, "server_time": datetime.now(timezone.utc).isoformat()}
        dead: list[WebSocket] = []
        for ws in list(self.active_connections):
            try:
                await ws.send_json(payload)
            except Exception:  # noqa: BLE001 — any send failure drops the client
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

    def broadcast_threadsafe(self, message: dict) -> None:
        """Safe to call from the MQTT (non-async) thread.

        Schedules the broadcast coroutine onto the captured main loop; without
        the captured handle, get_running_loop() on the MQTT thread always fails
        and every message would be silently dropped."""
        loop = self._main_loop
        if loop is None or loop.is_closed():
            logger.warning("No main loop captured; dropping broadcast: %s", message.get("type"))
            return
        asyncio.run_coroutine_threadsafe(self.broadcast(message), loop)

    def broadcast_alert(self, payload) -> None:
        """Called by the WebSocketProvider on the MQTT thread."""
        self.broadcast_threadsafe({
            "type": "alert",
            "severity": payload.severity.value,
            "hazard_type": payload.hazard_type.value,
            "message": payload.message,
            "zone_name": payload.zone_name,
            "device_id": payload.device_id,
            "risk_score": payload.risk_score,
            "sensor_values": payload.sensor_values,
            "explanation": payload.explanation,
        })


manager = ConnectionManager()
