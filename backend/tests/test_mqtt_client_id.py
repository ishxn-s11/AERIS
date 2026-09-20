"""Two backends must be able to share one broker without evicting each other.

MQTT brokers allow a single live connection per client id and silently take the
session over otherwise, so a hard-coded client id turns a dev backend next to a
container (or an overlapping rolling restart) into dropped telemetry.
"""
import re

from app.mqtt.subscriber import MAX_CLIENT_ID_BYTES, AerisMqttSubscriber, build_client_id


def test_client_id_keeps_the_configured_prefix_and_stays_within_the_byte_cap():
    generated = build_client_id("aeris-backend", token="abcd")
    assert generated.startswith("aeris-backend-")
    assert len(generated) <= MAX_CLIENT_ID_BYTES
    assert re.match(r"^[A-Za-z0-9\-]+$", generated)


def test_two_processes_get_different_client_ids():
    assert build_client_id("aeris-backend") != build_client_id("aeris-backend")


def test_long_prefix_is_clipped_but_keeps_the_unique_tail():
    generated = build_client_id("a-very-long-deployment-name-for-aeris", token="abcd")
    assert len(generated) <= MAX_CLIENT_ID_BYTES
    assert generated.endswith("-abcd")


def test_subscriber_uses_a_generated_id_not_the_raw_configuration():
    subscriber = AerisMqttSubscriber()
    try:
        assert subscriber.client_id != "aeris-backend"
        assert subscriber.client_id.startswith("aeris-back")
        assert len(subscriber.client_id) <= MAX_CLIENT_ID_BYTES
        assert subscriber.telemetry_filter.endswith("/+/+/telemetry")
    finally:
        subscriber.stop()
