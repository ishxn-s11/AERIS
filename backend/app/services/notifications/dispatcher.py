"""Notification dispatcher — fans an alert event out to out-of-process channels.

Runs as a background task consuming the alert bus. Two entry points:

* `run()` — the async consumer (Redis mode).
* `dispatch_sync()` — used by the bus fallback when Redis is absent. Delivery is
  handed to a small worker pool so the caller (the MQTT ingestion thread) is
  never blocked by SMTP or an HTTP gateway.

Failure isolation is per channel and per recipient: one broken channel must not
stop the others, and the operator can see what failed in `/api/alerts/providers`.
"""
from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from sqlalchemy.exc import SQLAlchemyError

from app.config.settings import settings
from app.services.alert_service import AlertPayload, AlertProvider

logger = logging.getLogger("aeris.notify.dispatcher")

MAX_WORKERS = 2


class NotificationDispatcher:
    def __init__(self, providers: list[AlertProvider]):
        self.providers = providers
        self.received = 0
        self._executor = ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="aeris-notify")
        self._counters: dict[str, dict] = {
            provider.name: {"sent": 0, "skipped": 0, "failed": 0, "last_error": None, "last_sent_at": None}
            for provider in providers
        }

    # -- dispatch ----------------------------------------------------------
    def should_send(self, provider: AlertProvider, payload: AlertPayload) -> bool:
        return provider.wants(payload)

    def dispatch_sync(self, payload: AlertPayload) -> None:
        """Queue delivery on the worker pool (safe from the MQTT thread)."""
        if not self.providers:
            return
        self._executor.submit(self._deliver_all, payload)

    async def dispatch(self, payload: AlertPayload) -> None:
        """Deliver on the event loop without blocking it."""
        await asyncio.to_thread(self._deliver_all, payload)

    def _deliver_all(self, payload: AlertPayload) -> None:
        for provider in self.providers:
            counter = self._counters.setdefault(
                provider.name, {"sent": 0, "skipped": 0, "failed": 0, "last_error": None, "last_sent_at": None}
            )
            if not self.should_send(provider, payload):
                counter["skipped"] += 1
                continue
            try:
                provider.send(None, payload)
                counter["sent"] += 1
                counter["last_sent_at"] = datetime.now(timezone.utc).isoformat()
                counter["last_error"] = None
            except Exception as exc:  # noqa: BLE001 — delivery failures are operational, not fatal
                counter["failed"] += 1
                counter["last_error"] = f"{type(exc).__name__}: {exc}"
                logger.exception("Notification channel '%s' failed", provider.name)

    # -- consumer ----------------------------------------------------------
    async def run(self, bus) -> None:
        """Consume the bus until cancelled; no-op when the bus is in-process."""
        try:
            async for payload in bus.messages():
                self.received += 1
                await self.dispatch(payload)
        except asyncio.CancelledError:
            raise
        except SQLAlchemyError:  # pragma: no cover — defensive
            logger.exception("Notification worker stopped on a database error")
        except Exception:  # noqa: BLE001 — the worker must survive transport errors
            logger.exception("Notification worker stopped")

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    # -- introspection -----------------------------------------------------
    def status(self) -> dict:
        return {
            "workers": MAX_WORKERS,
            "events_received": self.received,
            "channels": [
                {
                    "name": provider.name,
                    "min_severity": provider.min_severity,
                    "counters": self._counters.get(provider.name, {}),
                }
                for provider in self.providers
            ],
        }


def build_providers() -> list[AlertProvider]:
    """Instantiate the channels that are both enabled and fully configured.

    A channel that is enabled but missing recipients/credentials is reported by
    `/api/alerts/providers` as misconfigured instead of failing at alert time.
    """
    from app.services.notifications.email_provider import EmailProvider
    from app.services.notifications.sms_provider import SmsProvider

    providers: list[AlertProvider] = []
    if settings.notify_email_enabled and settings.email_recipient_list:
        providers.append(
            EmailProvider(
                settings.email_recipient_list,
                min_severity=settings.notify_email_min_severity,
            )
        )
    sms = SmsProvider(settings.sms_recipient_list, min_severity=settings.notify_sms_min_severity)
    if settings.notify_sms_enabled and sms.configured:
        providers.append(sms)
    return providers


def channel_configuration_report() -> list[dict]:
    """Configured vs. enabled state for every known channel (never secrets)."""
    from app.services.notifications.email_provider import EmailProvider
    from app.services.notifications.sms_provider import SmsProvider

    email_ok = bool(settings.email_recipient_list)
    sms_ok = SmsProvider(settings.sms_recipient_list).configured
    return [
        {
            "name": "email",
            "enabled": settings.notify_email_enabled,
            "configured": email_ok,
            "min_severity": settings.notify_email_min_severity,
            "recipients": len(settings.email_recipient_list),
            "transport": f"smtp://{settings.smtp_host}:{settings.smtp_port}",
            "detail": None if email_ok else "no recipients configured",
            "provider_class": EmailProvider.__name__,
        },
        {
            "name": "sms",
            "enabled": settings.notify_sms_enabled,
            "configured": sms_ok,
            "min_severity": settings.notify_sms_min_severity,
            "recipients": len(settings.sms_recipient_list),
            "transport": "http-webhook" if settings.sms_webhook_url else "unset",
            "detail": None if sms_ok else "webhook url or recipients missing",
            "provider_class": SmsProvider.__name__,
        },
    ]
