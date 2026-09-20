"""Notification channel tests — rendering, severity gating, bus, dispatch."""
import json

import pytest

from app.models.models import AlertSeverity, HazardType
from app.services.alert_service import AlertPayload, AlertProvider, meets_severity
from app.services.notifications import notification_status
from app.services.notifications.bus import AlertBus
from app.services.notifications.dispatcher import NotificationDispatcher
from app.services.notifications.email_provider import EmailProvider
from app.services.notifications.sms_provider import SmsProvider


def _payload(severity: AlertSeverity = AlertSeverity.CRITICAL) -> AlertPayload:
    return AlertPayload(
        severity=severity,
        hazard_type=HazardType.GAS_LEAK,
        message="Possible combustible gas leak detected in Chemical Storage",
        device_id="NODE_02",
        zone_name="Chemical Storage",
        sensor_values={"methane": 604, "temperature": 37.8},
        risk_score=91.4,
        explanation=["Gas rising rapidly (+210/min)"],
    )


# ---------------------------------------------------------------------------
# Severity gating
# ---------------------------------------------------------------------------
def test_severity_threshold_ordering():
    assert meets_severity("critical", "high") is True
    assert meets_severity("warning", "high") is False
    assert meets_severity("warning", None) is True


def test_email_channel_respects_min_severity():
    provider = EmailProvider(["ops@example.com"], min_severity="high")
    assert provider.wants(_payload(AlertSeverity.HIGH)) is True
    assert provider.wants(_payload(AlertSeverity.WARNING)) is False


def test_sms_channel_only_wants_critical():
    provider = SmsProvider(["+" + "1" * 11], webhook_url="http://gw.invalid/send", min_severity="critical")
    assert provider.wants(_payload(AlertSeverity.CRITICAL)) is True
    assert provider.wants(_payload(AlertSeverity.HIGH)) is False


# ---------------------------------------------------------------------------
# Rendering (pure functions — no transport involved)
# ---------------------------------------------------------------------------
def test_email_rendering_contains_actionable_fields():
    subject = EmailProvider.render_subject(_payload())
    body = EmailProvider.render_body(_payload())
    assert "CRITICAL" in subject and "Chemical Storage" in subject
    assert "gas_leak" in body
    assert "NODE_02" in body
    assert "91.4" in body
    assert "methane" in body and "604" in body
    assert "Gas rising rapidly" in body
    assert "calibration" in body.lower()  # honest about raw ADC units
    assert "not certified" in body.lower()


def test_sms_body_stays_within_one_segment():
    message = SmsProvider.render_message(_payload(AlertSeverity.WARNING))
    assert len(message) <= 160
    assert "WARNING" in message and "Chemical Storage" in message


def test_sms_payload_shape_is_gateway_compatible():
    provider = SmsProvider(["+15550001111"], webhook_url="http://gw.invalid/send", sender="AERIS")
    body = json.loads(provider.build_payload("+15550001111", _payload()).decode())
    assert body["To"] == "+15550001111"
    assert body["From"] == "AERIS"
    assert "CRITICAL" in body["Body"]


def test_sms_requires_webhook_and_recipients():
    assert SmsProvider([]).configured is False
    assert SmsProvider(["+1555"], webhook_url=None).configured is False
    assert SmsProvider(["+1555"], webhook_url="http://gw.invalid").configured is True


# ---------------------------------------------------------------------------
# Email transport (stubbed SMTP)
# ---------------------------------------------------------------------------
def test_email_provider_sends_via_smtp(monkeypatch):
    sent: list = []

    class FakeSMTP:
        def __init__(self, host, port, timeout=None):
            sent.append(("connect", host, port))

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def starttls(self):
            sent.append(("starttls",))

        def login(self, user, password):
            sent.append(("login", user))

        def send_message(self, message):
            sent.append(("message", message["Subject"], message["To"]))

    monkeypatch.setattr("smtplib.SMTP", FakeSMTP)
    provider = EmailProvider(["ops@example.com"], host="mail.invalid", port=2525,
                             username="u", password="p", min_severity="warning")
    provider.send(None, _payload())

    assert ("starttls",) in sent
    assert ("login", "u") in sent
    assert any(item[0] == "message" and item[2] == "ops@example.com" for item in sent)


def test_email_provider_skips_when_no_recipients(monkeypatch):
    monkeypatch.setattr("smtplib.SMTP", lambda *a, **k: pytest.fail("must not connect"))
    EmailProvider([], host="mail.invalid").send(None, _payload())


# ---------------------------------------------------------------------------
# Dispatcher: fan-out, isolation, counters
# ---------------------------------------------------------------------------
class _Recorder(AlertProvider):
    """Test double implementing the real provider interface (no transport)."""

    def __init__(self, name: str = "recorder", *, fail: bool = False, min_severity: str | None = None):
        self.name = name
        self.fail = fail
        self.min_severity = min_severity
        self.seen: list[AlertPayload] = []

    def send(self, db, payload) -> None:
        if self.fail:
            raise RuntimeError("gateway down")
        self.seen.append(payload)


def test_dispatcher_isolates_failures_between_channels():
    ok = _Recorder("ok")
    broken = _Recorder("broken", fail=True)
    dispatcher = NotificationDispatcher([broken, ok])
    dispatcher._deliver_all(_payload())
    dispatcher.shutdown()

    assert len(ok.seen) == 1  # a failing channel must not block delivery
    by_name = {c["name"]: c["counters"] for c in dispatcher.status()["channels"]}
    assert by_name["ok"]["sent"] == 1
    assert by_name["broken"]["failed"] == 1
    assert "gateway down" in by_name["broken"]["last_error"]


def test_dispatcher_counts_skipped_below_severity_floor():
    channel = _Recorder("quiet", min_severity="critical")
    dispatcher = NotificationDispatcher([channel])
    dispatcher._deliver_all(_payload(AlertSeverity.WARNING))
    dispatcher.shutdown()

    counter = dispatcher.status()["channels"][0]["counters"]
    assert counter["skipped"] == 1
    assert channel.seen == []


def test_dispatcher_sends_when_above_severity_floor():
    channel = _Recorder("loud", min_severity="critical")
    dispatcher = NotificationDispatcher([channel])
    dispatcher._deliver_all(_payload(AlertSeverity.CRITICAL))
    dispatcher.shutdown()

    status = dispatcher.status()["channels"][0]
    assert status["counters"]["sent"] == 1
    assert status["min_severity"] == "critical"
    assert len(channel.seen) == 1


# ---------------------------------------------------------------------------
# Bus
# ---------------------------------------------------------------------------
class _FakeDispatcher:
    def __init__(self):
        self.sync_calls = 0

    def dispatch_sync(self, payload):
        self.sync_calls += 1


def test_bus_without_redis_falls_back_to_in_process_dispatch():
    dispatcher = _FakeDispatcher()
    bus = AlertBus(dispatcher=dispatcher, url="")
    assert bus.connect() is False
    bus.publish(_payload())
    assert bus.mode == "in_process"
    assert dispatcher.sync_calls == 1
    assert bus.status()["published"] == 1


def test_bus_reports_unconfigured_redis_url():
    bus = AlertBus(dispatcher=None, url="")
    bus.connect()
    status = bus.status()
    assert status["mode"] == "in_process"
    assert status["redis_url_configured"] is False
    assert status["channel"] == "aeris:alerts"


def test_bus_publish_without_dispatcher_is_lossy_but_safe():
    bus = AlertBus(dispatcher=None, url="")
    bus.connect()
    bus.publish(_payload())  # must not raise when Redis and dispatcher are absent
    assert bus.status()["published"] == 0


def test_payload_survives_bus_serialisation_roundtrip():
    original = _payload()
    restored = AlertPayload.from_dict(json.loads(json.dumps(original.to_dict())))
    assert restored.severity == original.severity
    assert restored.hazard_type == original.hazard_type
    assert restored.zone_name == original.zone_name
    assert restored.sensor_values == original.sensor_values
    assert restored.risk_score == original.risk_score
    assert restored.occurred_at is not None  # stamped on the way out


# ---------------------------------------------------------------------------
# Status surface used by /api/alerts/providers
# ---------------------------------------------------------------------------
def test_notification_status_lists_both_channels_and_bus(client, auth_headers):
    res = client.get("/api/security/notifications", headers=auth_headers)
    assert res.status_code == 200
    body = res.json()
    assert body["bus"]["channel"]
    names = {c["name"] for c in body["channels"]}
    assert {"email", "sms"} <= names
    for channel in body["channels"]:
        assert "enabled" in channel and "configured" in channel and "min_severity" in channel
        # Never leak transport secrets through the status endpoint.
        assert "password" not in json.dumps(channel).lower()
        assert "auth_token" not in json.dumps(channel).lower()


def test_notifications_status_requires_auth(client):
    assert client.get("/api/security/notifications").status_code == 401


def test_status_helper_shape():
    status = notification_status()
    assert set(status) == {"bus", "dispatch", "channels"}
