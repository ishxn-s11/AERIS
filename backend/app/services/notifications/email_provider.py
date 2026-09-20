"""Email alert channel (SMTP).

Disabled unless `NOTIFY_EMAIL_ENABLED=true` and at least one recipient is
configured — a prototype must never send mail by accident. Delivery runs on a
worker thread (`NotificationDispatcher`), so a slow SMTP server cannot delay
telemetry ingestion.
"""
from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

from app.config.settings import settings
from app.services.alert_service import AlertPayload, AlertProvider, AlertSeverity

logger = logging.getLogger("aeris.notify.email")

_SEVERITY_SUBJECT = {
    AlertSeverity.WARNING.value: "WARNING",
    AlertSeverity.HIGH.value: "HIGH",
    AlertSeverity.CRITICAL.value: "CRITICAL",
}


class EmailProvider(AlertProvider):
    """Sends an incident-style plain-text email per alert."""

    name = "email"

    def __init__(
        self,
        recipients: list[str],
        *,
        sender: str | None = None,
        min_severity: str | None = None,
        host: str | None = None,
        port: int | None = None,
        username: str | None = None,
        password: str | None = None,
        use_tls: bool | None = None,
        timeout: float | None = None,
    ):
        self.recipients = recipients
        self.sender = sender or settings.notify_email_from
        self.min_severity = min_severity or settings.notify_email_min_severity
        self.host = host or settings.smtp_host
        self.port = port or settings.smtp_port
        self.username = username if username is not None else settings.smtp_username
        self.password = password if password is not None else settings.smtp_password
        self.use_tls = settings.smtp_use_tls if use_tls is None else use_tls
        self.timeout = timeout or settings.smtp_timeout_seconds

    # -- rendering (pure, unit-tested without a mail server) --------------
    @staticmethod
    def render_subject(payload: AlertPayload) -> str:
        severity = payload.severity.value if isinstance(payload.severity, AlertSeverity) else str(payload.severity)
        return f"[AERIS {_SEVERITY_SUBJECT.get(severity, severity.upper())}] {payload.zone_name or 'unknown zone'}"

    @staticmethod
    def render_body(payload: AlertPayload) -> str:
        severity = payload.severity.value if isinstance(payload.severity, AlertSeverity) else str(payload.severity)
        hazard = (
            payload.hazard_type.value
            if hasattr(payload.hazard_type, "value")
            else str(payload.hazard_type)
        )
        lines = [
            "AERIS — industrial atmosphere early warning",
            "",
            f"Severity      : {severity.upper()}",
            f"Hazard        : {hazard}",
            f"Zone          : {payload.zone_name or 'unknown'}",
            f"Device        : {payload.device_id or 'n/a'}",
            f"Risk score    : {payload.risk_score if payload.risk_score is not None else 'n/a'}",
            f"Detected at   : {payload.occurred_at.isoformat() if payload.occurred_at else 'n/a'}",
            "",
            payload.message,
        ]
        if payload.sensor_values:
            lines.append("")
            lines.append("Sensor snapshot (raw ADC units — calibration required for ppm):")
            for key, value in payload.sensor_values.items():
                lines.append(f"  {key:>16}: {value}")
        if payload.explanation:
            lines.append("")
            lines.append("Contributing factors:")
            lines.extend(f"  - {reason}" for reason in payload.explanation)
        lines.extend(
            [
                "",
                "Acknowledge this alert in the AERIS console.",
                "",
                "PROTOTYPE notification — decision support only, not certified safety equipment.",
            ]
        )
        return "\n".join(lines)

    # -- delivery ----------------------------------------------------------
    def send(self, db=None, payload: AlertPayload | None = None) -> None:
        if payload is None or not self.recipients:
            return
        message = EmailMessage()
        message["Subject"] = self.render_subject(payload)
        message["From"] = self.sender
        message["To"] = ", ".join(self.recipients)
        message.set_content(self.render_body(payload))

        with smtplib.SMTP(self.host, self.port, timeout=self.timeout) as server:
            if self.use_tls:
                server.starttls()
            if self.username and self.password:
                server.login(self.username, self.password)
            server.send_message(message)
        logger.info("Email alert sent to %d recipient(s)", len(self.recipients))
