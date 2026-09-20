"""Notification channels (email, SMS, future push/WhatsApp).

Public surface:
- `dispatcher` — fans alert events out to the configured channels.
- `bus` — the Redis fan-out transport (with in-process fallback).
- `notification_status()` — non-sensitive channel/bus state for the API.

Ingestion publishes; workers deliver. The two live in different threads on
purpose so a slow gateway can never delay telemetry.
"""
from app.services.notifications.bus import AlertBus
from app.services.notifications.dispatcher import (
    NotificationDispatcher,
    build_providers,
    channel_configuration_report,
)

dispatcher = NotificationDispatcher(build_providers())
bus = AlertBus(dispatcher=dispatcher)


def notification_status() -> dict:
    """Combined bus + channel status for `GET /api/alerts/providers`."""
    return {
        "bus": bus.status(),
        "dispatch": dispatcher.status(),
        "channels": channel_configuration_report(),
    }


__all__ = ["bus", "dispatcher", "notification_status", "build_providers"]
