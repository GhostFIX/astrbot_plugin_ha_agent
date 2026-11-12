"""插件配置解析。

把 AstrBot 传进来的原始配置字典归一化成不可变的 :class:`Settings`，
所有取值都做容错：配置写错不会让插件在加载阶段崩掉。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .access import normalize_umo_list
from .constants import DEFAULT_EVENT_TYPE, DEFAULT_INBOUND_PORT

_EVENT_TYPE_RE = re.compile(r"^[A-Za-z0-9_]{1,64}$")
_PORT_MIN = 1024
_PORT_MAX = 65535


def _as_bool(value: object, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "y"}
    return bool(value)


def _as_port(value: object, default: int = DEFAULT_INBOUND_PORT) -> int:
    try:
        port = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    if port < _PORT_MIN or port > _PORT_MAX:
        return default
    return port


def _as_event_type(value: object, default: str = DEFAULT_EVENT_TYPE) -> str:
    text = str(value or "").strip()
    if not text or not _EVENT_TYPE_RE.match(text):
        return default
    return text


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


@dataclass(frozen=True)
class Settings:
    """一份解析完成的插件配置快照。"""

    ha_url: str = ""
    ha_token: str = ""
    event_type: str = DEFAULT_EVENT_TYPE
    verify_ssl: bool = True
    inbound_enabled: bool = True
    inbound_port: int = DEFAULT_INBOUND_PORT
    inbound_token: str = ""
    notify_umo: str = ""
    allowed_umos: tuple[str, ...] = ()
    blocked_umos: tuple[str, ...] = ()

    @property
    def outbound_ready(self) -> bool:
        """是否具备向 HA 发事件的最低条件。"""
        return bool(self.ha_url and self.ha_token)

    @property
    def notify_enabled(self) -> bool:
        """是否配置了告警目标。留空即完全不推送。"""
        return bool(self.notify_umo)

    def event_url(self) -> str:
        return f"{self.ha_url}/events/{self.event_type}"

    @classmethod
    def from_config(cls, raw: object) -> "Settings":
        cfg: Any = raw if hasattr(raw, "get") else {}
        return cls(
            ha_url=normalize_ha_url(cfg.get("ha_url")),
            ha_token=str(cfg.get("ha_token") or "").strip(),
            event_type=_as_event_type(cfg.get("event_type")),
            verify_ssl=_as_bool(cfg.get("verify_ssl"), True),
            inbound_enabled=_as_bool(cfg.get("inbound_enabled"), True),
            inbound_port=_as_port(cfg.get("inbound_port")),
            inbound_token=str(cfg.get("inbound_token") or "").strip(),
            notify_umo=str(cfg.get("notify_umo") or "").strip(),
            allowed_umos=tuple(normalize_umo_list(cfg.get("allowed_umos"))),
            blocked_umos=tuple(normalize_umo_list(cfg.get("blocked_umos"))),
        )
