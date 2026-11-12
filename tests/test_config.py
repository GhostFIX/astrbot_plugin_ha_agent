"""配置归一化：地址补全、事件名清洗、端口边界。"""

from __future__ import annotations

from core.config import Settings, normalize_ha_url


def test_url_without_scheme_gets_http_and_api():
    assert normalize_ha_url("192.168.1.5:8123") == "http://192.168.1.5:8123/api"


def test_url_trailing_slash_is_trimmed():
    assert normalize_ha_url("http://ha.local:8123/") == "http://ha.local:8123/api"


def test_url_already_has_api():
    assert normalize_ha_url("https://ha.example.com/api/") == (
        "https://ha.example.com/api"
    )


def test_empty_url_stays_empty():
    assert normalize_ha_url(None) == ""
    assert normalize_ha_url("   ") == ""


def test_defaults_when_config_is_empty():
    settings = Settings.from_config({})
    assert settings.event_type == "astrbot_command"
    assert settings.inbound_port == 6199
    assert settings.verify_ssl is True
    assert settings.outbound_transport == "rest"
    assert settings.inbound_transport == "http"
    assert settings.outbound_ready is False
    assert settings.notify_enabled is False
    assert settings.mqtt_port == 1883
    assert settings.mqtt_topic_prefix == "astrbot/ha"
    assert settings.uses_mqtt is False


def test_transport_choices_fall_back_to_defaults():
    assert Settings.from_config({"outbound_transport": "bogus"}).outbound_transport == (
        "rest"
    )
    assert Settings.from_config({"inbound_transport": "bogus"}).inbound_transport == (
        "http"
    )
    assert Settings.from_config({"outbound_transport": "MQTT"}).outbound_transport == (
        "mqtt"
    )
    assert Settings.from_config({"inbound_transport": "off"}).inbound_transport == (
        "off"
    )


def test_mqtt_topics_are_derived_from_prefix():
    settings = Settings.from_config({"mqtt_topic_prefix": "home/astrbot"})
    assert settings.mqtt_command_topic == "home/astrbot/command"
    assert settings.mqtt_notify_topic == "home/astrbot/notify"

    sloppy = Settings.from_config({"mqtt_topic_prefix": "/home/astrbot/"})
    assert sloppy.mqtt_topic_prefix == "home/astrbot"

    blank = Settings.from_config({"mqtt_topic_prefix": "   "})
    assert blank.mqtt_topic_prefix == "astrbot/ha"


def test_mqtt_port_validation():
    assert Settings.from_config({"mqtt_port": 0}).mqtt_port == 1883
    assert Settings.from_config({"mqtt_port": 70000}).mqtt_port == 1883
    assert Settings.from_config({"mqtt_port": "8883"}).mqtt_port == 8883


def test_mqtt_readiness_ignores_ha_rest_fields():
    settings = Settings.from_config(
        {"outbound_transport": "mqtt", "mqtt_host": "192.168.1.5"}
    )
    assert settings.mqtt_ready is True
    assert settings.outbound_ready is True
    assert settings.uses_mqtt is True

    rest = Settings.from_config({"outbound_transport": "mqtt"})
    assert rest.outbound_ready is False


def test_event_type_rejects_illegal_characters():
    assert Settings.from_config({"event_type": "bad name!"}).event_type == (
        "astrbot_command"
    )
    assert Settings.from_config({"event_type": "my_cmd2"}).event_type == "my_cmd2"


def test_port_out_of_range_falls_back():
    assert Settings.from_config({"inbound_port": 80}).inbound_port == 6199
    assert Settings.from_config({"inbound_port": 70000}).inbound_port == 6199
    assert Settings.from_config({"inbound_port": "abc"}).inbound_port == 6199
    assert Settings.from_config({"inbound_port": 8123}).inbound_port == 8123


def test_bool_strings_are_parsed():
    assert Settings.from_config({"verify_ssl": "false"}).verify_ssl is False
    assert Settings.from_config({"verify_ssl": "true"}).verify_ssl is True


def test_reply_on_success_defaults_true_and_can_be_disabled():
    assert Settings.from_config({}).reply_on_success is True
    assert Settings.from_config({"reply_on_success": "false"}).reply_on_success is False
    assert Settings.from_config({"reply_on_success": False}).reply_on_success is False


def test_lists_are_normalized_into_tuples():
    settings = Settings.from_config(
        {"allowed_umos": ["a", " a ", "b"], "blocked_umos": "c,d"}
    )
    assert settings.allowed_umos == ("a", "b")
    assert settings.blocked_umos == ("c", "d")


def test_ready_flags():
    settings = Settings.from_config(
        {"ha_url": "http://ha:8123", "ha_token": "t", "notify_umos": ["x:y:z"]}
    )
    assert settings.outbound_ready is True
    assert settings.notify_enabled is True
    assert settings.event_url() == "http://ha:8123/api/events/astrbot_command"


def test_legacy_single_notify_umo_is_merged():
    settings = Settings.from_config({"notify_umo": "a:b:1"})
    assert settings.notify_umos == ("a:b:1",)
    assert settings.notify_enabled is True


def test_notify_targets_are_deduped_and_ordered():
    settings = Settings.from_config(
        {"notify_umos": ["a:b:1", "a:b:2", "a:b:1"], "notify_umo": "a:b:2"}
    )
    assert settings.notify_umos == ("a:b:1", "a:b:2")
