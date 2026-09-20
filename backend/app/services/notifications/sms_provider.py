"""SMS alert channel.

Talks to a provider-agnostic HTTP endpoint that accepts a Twilio-compatible
body (`{"To", "From", "Body"}`). Point `SMS_WEBHOOK_URL` at Twilio, Vonage, an
MSG91 route or an internal gateway — the alert logic never changes.

Uses `urllib` from the standard library on purpose: notification delivery must
not add a heavyweight client dependency to a safety-critical ingestion path.
Delivery runs on a worker thread, so a hanging gateway cannot stall MQTT
ingestion (timeouts are enforced per request).
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

from app.config.settings import settings
from app.services.alert_service import AlertPayload, AlertProvider, AlertSeverity

logger = logging.getLogger("aeris.notify.sms")

# SMS gateways bill per segment; keep the body inside one GSM-7 segment.
MAX_BODY_CHARS = 160


class SmsProvider(AlertProvider):
    name = "sms"

    def __init__(
        self,
        recipients: list[str],
        *,
        webhook_url: str | None = None,
        auth_token: str | None = None,
        sender: str | None = None,
        min_severity: str | None = None,
        timeout: float | None = None,
    ):
        self.recipients = recipients
        self.webhook_url = webhook_url if webhook_url is not None else settings.sms_webhook_url
        self.auth_token = auth_token if auth_token is not None else settings.sms_auth_token
        self.sender = sender or settings.notify_sms_from
        self.min_severity = min_severity or settings.notify_sms_min_severity
        self.timeout = timeout or settings.sms_timeout_seconds

    @property
    def configured(self) -> bool:
        return bool(self.webhook_url and self.recipients)

    # -- rendering (pure) --------------------------------------------------
    @staticmethod
    def render_message(payload: AlertPayload) -> str:
        severity = payload.severity.value if isinstance(payload.severity, AlertSeverity) else str(payload.severity)
        score = f" score {payload.risk_score:.0f}" if payload.risk_score is not None else ""
        body = f"AERIS {severity.upper()}{score} — {payload.zone_name or 'zone'}: {payload.message}"
        return body[: MAX_BODY_CHARS - 1] + "…" if len(body) > MAX_BODY_CHARS else body

    def build_payload(self, recipient: str, payload: AlertPayload) -> bytes:
        return json.dumps(
            {
                "To": recipient,
                "From": self.sender,
                "Body": self.render_message(payload),
            }
        ).encode("utf-8")

    # -- delivery ----------------------------------------------------------
    def send(self, db=None, payload: AlertPayload | None = None) -> None:
        if payload is None or not self.configured:
            return
        for recipient in self.recipients:
            request = urllib.request.Request(
                self.webhook_url,
                data=self.build_payload(recipient, payload),
                headers={
                    "Content-Type": "application/json",
                    **({"Authorization": f"Bearer {self.auth_token}"} if self.auth_token else {}),
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:  # noqa: S310
                    if response.status >= 400:
                        logger.error("SMS gateway rejected message for %s (HTTP %s)", recipient, response.status)
                    else:
                        logger.info("SMS alert sent to %s", recipient)
            except (urllib.error.URLError, TimeoutError) as exc:
                logger.error("SMS delivery failed for %s: %s", recipient, exc)
