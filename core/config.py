"""插件配置解析。

把 AstrBot 传进来的原始配置字典归一化成不可变的 :class:`Settings`，
所有取值都做容错：配置写错不会让插件在加载阶段崩掉。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .access import normalize_umo_list
from .constants import (
    DEFAULT_EVENT_TYPE,
    DEFAULT_INBOUND_PORT,
    MQTT_DEFAULT_PORT,
    MQTT_TOPIC_PREFIX,
)

_EVENT_TYPE_RE = re.compile(r"^[A-Za-z0-9_]{1,64}$")
_OUTBOUND_CHOICES = ("rest", "mqtt")
_INBOUND_CHOICES = ("http", "mqtt", "off")
_LISTEN_PORT_MIN = 1024
_PORT_MAX = 65535


def _as_bool(value: object, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "y"}
    return bool(value)


def _as_port(value: object, default: int, minimum: int) -> int:
    try:
        port = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    if port < minimum or port > _PORT_MAX:
        return default
    return port


def _as_event_type(value: object, default: str = DEFAULT_EVENT_TYPE) -> str:
    text = str(value or "").strip()
    if not text or not _EVENT_TYPE_RE.match(text):
        return default
    return text


def _as_choice(value: object, choices: tuple[str, ...], default: str) -> str:
    text = str(value or "").strip().lower()
    return text if text in choices else default


def normalize_topic_prefix(raw: object, default: str = MQTT_TOPIC_PREFIX) -> str:
    """把主题前缀归一化：去掉首尾斜杠和空白，空值回退默认。"""
    text = str(raw or "").strip().strip("/")
    return text or default


def normalize_ha_url(raw: object) -> str:
    """把用户填的地址补全成 ``<scheme>://<host>:<port>/api``。"""
    text = str(raw or "").strip()
    if not text:
        return ""
    if "://" not in text:
        text = "http://" + text
    text = text.rstrip("/")
    if not text.endswith("/api"):
        text += "/api"
    return text


def resolve_notify_targets(cfg: Any) -> tuple[str, ...]:
    """合并 ``notify_umos`` 与 1.x 的单个 ``notify_umo``，去重保序。"""
    targets = normalize_umo_list(cfg.get("notify_umos"))
    legacy = str(cfg.get("notify_umo") or "").strip()
    if legacy:
        targets.append(legacy)
    return tuple(dict.fromkeys(item for item in targets if item))


@dataclass(frozen=True)
class Settings:
    """一份解析完成的插件配置快照。"""

    outbound_transport: str = "rest"
    ha_url: str = ""
    ha_token: str = ""
    event_type: str = DEFAULT_EVENT_TYPE
    verify_ssl: bool = True
    reply_on_success: bool = True
    inbound_transport: str = "http"
    inbound_port: int = DEFAULT_INBOUND_PORT
    inbound_token: str = ""
    notify_umos: tuple[str, ...] = ()
    allowed_umos: tuple[str, ...] = ()
    blocked_umos: tuple[str, ...] = ()
    mqtt_host: str = ""
    mqtt_port: int = MQTT_DEFAULT_PORT
    mqtt_username: str = ""
    mqtt_password: str = ""
    mqtt_tls: bool = False
    mqtt_topic_prefix: str = MQTT_TOPIC_PREFIX

    @property
    def mqtt_ready(self) -> bool:
        return bool(self.mqtt_host)

    @property
    def uses_mqtt(self) -> bool:
        return "mqtt" in (self.outbound_transport, self.inbound_transport)

    @property
    def outbound_ready(self) -> bool:
        """是否具备向 HA 发指令的最低条件。"""
        if self.outbound_transport == "mqtt":
            return self.mqtt_ready
        return bool(self.ha_url and self.ha_token)

    @property
    def notify_enabled(self) -> bool:
        """是否配置了告警目标。留空即完全不推送。"""
        return bool(self.notify_umos)

    @property
    def mqtt_command_topic(self) -> str:
        return f"{self.mqtt_topic_prefix}/command"

    @property
    def mqtt_notify_topic(self) -> str:
        return f"{self.mqtt_topic_prefix}/notify"

    def event_url(self) -> str:
        return f"{self.ha_url}/events/{self.event_type}"

    @classmethod
    def from_config(cls, raw: object) -> "Settings":
        cfg: Any = raw if hasattr(raw, "get") else {}
        return cls(
            outbound_transport=_as_choice(
                cfg.get("outbound_transport"), _OUTBOUND_CHOICES, "rest"
            ),
            ha_url=normalize_ha_url(cfg.get("ha_url")),
            ha_token=str(cfg.get("ha_token") or "").strip(),
            event_type=_as_event_type(cfg.get("event_type")),
            verify_ssl=_as_bool(cfg.get("verify_ssl"), True),
            reply_on_success=_as_bool(cfg.get("reply_on_success"), True),
            inbound_transport=_as_choice(
                cfg.get("inbound_transport"), _INBOUND_CHOICES, "http"
            ),
            inbound_port=_as_port(
                cfg.get("inbound_port"), DEFAULT_INBOUND_PORT, _LISTEN_PORT_MIN
            ),
            inbound_token=str(cfg.get("inbound_token") or "").strip(),
            notify_umos=resolve_notify_targets(cfg),
            allowed_umos=tuple(normalize_umo_list(cfg.get("allowed_umos"))),
            blocked_umos=tuple(normalize_umo_list(cfg.get("blocked_umos"))),
            mqtt_host=str(cfg.get("mqtt_host") or "").strip(),
            mqtt_port=_as_port(cfg.get("mqtt_port"), MQTT_DEFAULT_PORT, 1),
            mqtt_username=str(cfg.get("mqtt_username") or "").strip(),
            mqtt_password=str(cfg.get("mqtt_password") or ""),
            mqtt_tls=_as_bool(cfg.get("mqtt_tls"), False),
            mqtt_topic_prefix=normalize_topic_prefix(cfg.get("mqtt_topic_prefix")),
        )
