"""AERIS backend entrypoint.

Lifespan sequence (mirrors the production startup order):
1. init DB (tables + demo seed)
2. load ML models (hazard classifier + risk projection regressor)
3. connect the alert bus (Redis if configured, else in-process)
4. start MQTT subscriber (IoT ingestion)
5. start background loops: device health, notification worker
"""
import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.auth import router as auth_router
from app.api.domains import factories_router, zones_router
from app.api.forecasts import forecasts_router
from app.api.monitoring import (
    alerts_router,
    dashboard_router,
    devices_router,
    predictions_router,
    telemetry_router,
    ws_router,
)
from app.api.connections import connections_router
from app.api.dispersion import dispersion_router
from app.api.security import security_router
from app.config.logging_config import configure_logging, logger
from app.config.settings import settings
from app.database.seed import init_db
from app.ml.inference import preload_model
from app.mqtt.subscriber import AerisMqttSubscriber, set_active_subscriber
from app.services.notifications import bus as alert_bus
from app.services.notifications import dispatcher as notification_dispatcher
from app.services.telemetry_service import check_device_health
from app.websocket.manager import manager

configure_logging()


async def device_health_loop() -> None:
    """Periodically flag devices whose telemetry has stopped."""
    while True:
        await asyncio.sleep(settings.device_health_check_interval_seconds)
        try:
            await asyncio.to_thread(_health_check_once)
        except Exception:  # noqa: BLE001 — monitor must never crash the app
            logger.exception("Device health check failed")


def _health_check_once() -> None:
    from app.database.session import SessionLocal

    with SessionLocal() as db:
        changed = check_device_health(db)
        for device in changed:
            logger.warning("Device %s marked OFFLINE (no telemetry)", device.device_id)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    preload_model()  # rules-only mode if artifact missing — never fatal
    # Capture the main event loop so MQTT-thread ingestion can schedule
    # WebSocket broadcasts onto it (see manager.broadcast_threadsafe).
    manager.set_main_loop(asyncio.get_running_loop())
    # Notification fan-out: Redis when configured, in-process otherwise. Both
    # modes are fine for the prototype; the mode is reported by the API.
    alert_bus.connect()
    subscriber = AerisMqttSubscriber()
    # Publish the handle so status endpoints report this client's real state
    # rather than opening a competing connection (same client id → the broker
    # would kick one of them off).
    set_active_subscriber(subscriber)
    subscriber.start()
    subscriber.wait_for_connection(timeout=15)
    from app.services.http_gateway import gateway_simulator  # noqa: PLC0415
    gateway_simulator.start()
    health_task = asyncio.create_task(device_health_loop())
    notify_task = asyncio.create_task(notification_dispatcher.run(alert_bus))
    logger.info(
        "AERIS backend ready (env=%s, alert bus=%s, notification channels=%s)",
        settings.environment,
        alert_bus.mode,
        [p.name for p in notification_dispatcher.providers] or "none",
    )
    try:
        yield
    finally:
        health_task.cancel()
        notify_task.cancel()
        for task in (health_task, notify_task):
            with contextlib.suppress(asyncio.CancelledError):
                await task
        from app.services.http_gateway import gateway_simulator  # noqa: PLC0415
        gateway_simulator.stop()
        notification_dispatcher.shutdown()
        alert_bus.close()
        subscriber.stop()


app = FastAPI(
    title="AERIS API",
    description=(
        "AERIS — Aerial & Industrial Environmental Risk Intelligence System. "
        "IoT + AI hazardous-atmosphere monitoring and early-warning platform. "
        "PROTOTYPE: decision-support/demonstration system — not a substitute for "
        "certified fire & gas detection equipment."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router, prefix="/api")
app.include_router(factories_router, prefix="/api")
app.include_router(zones_router, prefix="/api")
app.include_router(devices_router, prefix="/api")
app.include_router(telemetry_router, prefix="/api")
app.include_router(alerts_router, prefix="/api")
app.include_router(dashboard_router, prefix="/api")
app.include_router(predictions_router, prefix="/api")
app.include_router(forecasts_router, prefix="/api")
app.include_router(security_router, prefix="/api")
app.include_router(dispersion_router, prefix="/api")
app.include_router(connections_router, prefix="/api")
app.include_router(ws_router)


@app.get("/api/health", tags=["health"])
def health() -> dict:
    return {"status": "ok", "service": "aeris-backend"}
