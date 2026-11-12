"""WebUI 设置页的纯逻辑：快照脱敏与提交合并。"""

from __future__ import annotations

import pytest

from core.settings import SettingsError, merge_payload, public_snapshot

BASE = {
    "outbound_transport": "rest",
    "inbound_transport": "http",
    "ha_url": "http://192.168.1.5:8123/api",
    "ha_token": "secret-token",
    "event_type": "astrbot_command",
    "verify_ssl": True,
    "inbound_port": 6199,
    "notify_umos": ["aiocqhttp:GroupMessage:9"],
    "allowed_umos": ["a"],
    "blocked_umos": [],
    "mqtt_host": "",
    "mqtt_port": 1883,
    "mqtt_username": "",
    "mqtt_password": "",
    "mqtt_tls": False,
    "mqtt_topic_prefix": "astrbot/ha",
    "inbound_token": "",
}


def test_snapshot_masks_secrets_and_reports_presence():
    snapshot = public_snapshot(BASE)

    assert snapshot["ha_token"] == ""
    assert snapshot["ha_token_set"] is True
    assert snapshot["mqtt_password_set"] is False
    assert snapshot["ha_url"] == "http://192.168.1.5:8123/api"
    assert snapshot["inbound_port"] == 6199


def test_snapshot_handles_empty_config():
    snapshot = public_snapshot(None)
    assert snapshot["outbound_transport"] == "rest"
    assert snapshot["inbound_transport"] == "http"
    assert snapshot["mqtt_port"] == 1883
    assert snapshot["mqtt_topic_prefix"] == "astrbot/ha"
    assert snapshot["allowed_umos"] == []
    assert snapshot["ha_token_set"] is False


def test_blank_secret_keeps_existing_value():
    merged = merge_payload(BASE, {"ha_token": "", "mqtt_password": "  "})
    assert merged["ha_token"] == "secret-token"
    assert merged["mqtt_password"] == ""


def test_provided_secret_replaces_value():
    merged = merge_payload(BASE, {"mqtt_password": "new-pass"})
    assert merged["mqtt_password"] == "new-pass"


def test_transport_switch_is_applied():
    merged = merge_payload(
        BASE, {"outbound_transport": "mqtt", "inbound_transport": "off"}
    )
    assert merged["outbound_transport"] == "mqtt"
    assert merged["inbound_transport"] == "off"


@pytest.mark.parametrize(
    "payload",
    [
        {"outbound_transport": "carrier-pigeon"},
        {"inbound_transport": "smoke-signal"},
        {"event_type": "bad name"},
        {"inbound_port": 80},
        {"inbound_port": "abc"},
        {"mqtt_port": 0},
        {"mqtt_port": 70000},
    ],
)
def test_invalid_values_raise(payload):
    with pytest.raises(SettingsError):
        merge_payload(BASE, payload)


def test_unknown_keys_are_ignored():
    merged = merge_payload(BASE, {"ha_token": "", "evil": "x", "__proto__": 1})
    assert "evil" not in merged
    assert "__proto__" not in merged


def test_lists_accept_strings_and_lists():
    merged = merge_payload(BASE, {"allowed_umos": "a, b\nc", "blocked_umos": ["d", " d "]})
    assert merged["allowed_umos"] == ["a", "b", "c"]
    assert merged["blocked_umos"] == ["d"]


def test_ha_url_is_normalized_on_save():
    merged = merge_payload(BASE, {"ha_url": "192.168.1.9:8123"})
    assert merged["ha_url"] == "http://192.168.1.9:8123/api"


def test_topic_prefix_is_normalized():
    merged = merge_payload(BASE, {"mqtt_topic_prefix": "/home/astrbot/"})
    assert merged["mqtt_topic_prefix"] == "home/astrbot"

    blank = merge_payload(BASE, {"mqtt_topic_prefix": "   "})
    assert blank["mqtt_topic_prefix"] == "astrbot/ha"


def test_event_type_default_when_blank():
    merged = merge_payload(BASE, {"event_type": "  "})
    assert merged["event_type"] == "astrbot_command"


def test_payload_must_be_object():
    with pytest.raises(SettingsError):
        merge_payload(BASE, ["not", "a", "dict"])  # type: ignore[arg-type]
