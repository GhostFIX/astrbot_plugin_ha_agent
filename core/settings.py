"""WebUI 设置页的读取、校验与合并。

这里不依赖 AstrBot，纯函数进出，方便单测。真正的落盘与热生效在
``main.HaAgentPlugin.save_settings`` 里完成。

约定：
- 密钥字段（``ha_token`` / ``mqtt_password`` / ``inbound_token``）回给前端时
  一律置空并附带 ``<字段>_set`` 布尔；保存时该字段为空表示"保持原值"。
"""

from __future__ import annotations

import re
from typing import Any, Mapping

from .access import normalize_umo_list
from .config import normalize_ha_url, normalize_topic_prefix, resolve_notify_targets
from .constants import DEFAULT_EVENT_TYPE

SECRET_FIELDS = ("ha_token", "mqtt_password", "inbound_token")

_OUTBOUND_CHOICES = ("rest", "mqtt")
_INBOUND_CHOICES = ("http", "mqtt", "off")
_EVENT_TYPE_RE = re.compile(r"^[A-Za-z0-9_]{1,64}$")

_MAX_TEXT = 512
_MAX_SECRET = 4096
_MIN_LISTEN_PORT = 1024
_MAX_PORT = 65535


class SettingsError(ValueError):
    """设置校验失败，文案可直接展示给用户。"""


def _clean(value: Any, limit: int, label: str) -> str:
    text = str(value if value is not None else "").strip()
    if len(text) > limit:
        raise SettingsError(f"{label} 过长（上限 {limit} 字符）")
    return text


def _choice(value: Any, options: tuple[str, ...], label: str) -> str:
    text = str(value if value is not None else "").strip().lower()
    if text not in options:
        raise SettingsError(f"{label} 只能是 {' / '.join(options)}")
    return text


def _bool(value: Any, label: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        low = value.strip().lower()
        if low in {"true", "1", "on", "yes", "y"}:
            return True
        if low in {"false", "0", "off", "no", "n", ""}:
            return False
    if isinstance(value, (int, float)):
        return bool(value)
    raise SettingsError(f"{label} 需要是布尔值")


def _port(value: Any, label: str, minimum: int) -> int:
    try:
        port = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise SettingsError(f"{label} 需要是数字") from exc
    if port < minimum or port > _MAX_PORT:
        raise SettingsError(f"{label} 需在 {minimum}-{_MAX_PORT} 之间")
    return port


def _event_type(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return DEFAULT_EVENT_TYPE
    if not _EVENT_TYPE_RE.match(text):
        raise SettingsError("事件类型只能包含字母、数字和下划线")
    return text


def public_snapshot(config: Mapping[str, Any] | None) -> dict[str, Any]:
    """给前端的完整可编辑快照，密钥一律置空。"""
    cfg: Mapping[str, Any] = config if isinstance(config, Mapping) else {}

    def text(key: str, default: str = "") -> str:
        value = cfg.get(key, default)
        return str(value if value is not None else default)

    def flag(key: str, default: bool) -> bool:
        value = cfg.get(key, default)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on", "y"}
        return bool(value) if value is not None else default

    snapshot: dict[str, Any] = {
        "outbound_transport": text("outbound_transport", "rest").lower() or "rest",
        "inbound_transport": text("inbound_transport", "http").lower() or "http",
        "ha_url": text("ha_url"),
        "event_type": text("event_type", DEFAULT_EVENT_TYPE) or DEFAULT_EVENT_TYPE,
        "verify_ssl": flag("verify_ssl", True),
        "inbound_port": _safe_int(cfg.get("inbound_port"), 6199),
        "notify_umos": list(resolve_notify_targets(cfg)),
        "allowed_umos": list(normalize_umo_list(cfg.get("allowed_umos"))),
        "blocked_umos": list(normalize_umo_list(cfg.get("blocked_umos"))),
        "mqtt_host": text("mqtt_host"),
        "mqtt_port": _safe_int(cfg.get("mqtt_port"), 1883),
        "mqtt_username": text("mqtt_username"),
        "mqtt_tls": flag("mqtt_tls", False),
        "mqtt_topic_prefix": text("mqtt_topic_prefix", "astrbot/ha") or "astrbot/ha",
    }
    for field in SECRET_FIELDS:
        existing = str(cfg.get(field) or "").strip()
        snapshot[field] = ""
        snapshot[f"{field}_set"] = bool(existing)
    return snapshot


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def merge_payload(
    current: Mapping[str, Any] | None,
    payload: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """把前端提交的字段合并进现有配置，返回完整的新配置字典。

    只认识白名单里的键，其余静默忽略；任何非法值抛 :class:`SettingsError`。
    """
    if not isinstance(payload, Mapping):
        raise SettingsError("请求体需要是 JSON 对象")

    base: Mapping[str, Any] = current if isinstance(current, Mapping) else {}
    merged = dict(base)

    if "outbound_transport" in payload:
        merged["outbound_transport"] = _choice(
            payload["outbound_transport"], _OUTBOUND_CHOICES, "出站方式"
        )
    if "inbound_transport" in payload:
        merged["inbound_transport"] = _choice(
            payload["inbound_transport"], _INBOUND_CHOICES, "入站方式"
        )

    if "ha_url" in payload:
        merged["ha_url"] = _clean(payload["ha_url"], _MAX_TEXT, "HA 地址")
    if "event_type" in payload:
        merged["event_type"] = _event_type(payload["event_type"])
    if "verify_ssl" in payload:
        merged["verify_ssl"] = _bool(payload["verify_ssl"], "校验 HTTPS 证书")

    if "inbound_port" in payload:
        merged["inbound_port"] = _port(
            payload["inbound_port"], "告警回传端口", _MIN_LISTEN_PORT
        )

    if "mqtt_host" in payload:
        merged["mqtt_host"] = _clean(payload["mqtt_host"], _MAX_TEXT, "Broker 地址")
    if "mqtt_port" in payload:
        merged["mqtt_port"] = _port(payload["mqtt_port"], "Broker 端口", 1)
    if "mqtt_username" in payload:
        merged["mqtt_username"] = _clean(
            payload["mqtt_username"], _MAX_TEXT, "MQTT 用户名"
        )
    if "mqtt_tls" in payload:
        merged["mqtt_tls"] = _bool(payload["mqtt_tls"], "MQTT TLS")
    if "mqtt_topic_prefix" in payload:
        prefix = normalize_topic_prefix(payload["mqtt_topic_prefix"])
        if not prefix:
            raise SettingsError("主题前缀不能为空")
        merged["mqtt_topic_prefix"] = prefix

    if "notify_umos" in payload:
        targets = normalize_umo_list(payload["notify_umos"])
        if len(targets) > 200:
            raise SettingsError("告警目标最多 200 个")
        merged["notify_umos"] = targets
        # 1.x 的单个 notify_umo 会被并进列表，这里顺手清掉避免复活。
        merged.pop("notify_umo", None)
    elif "notify_umo" in payload:
        legacy = str(payload["notify_umo"] or "").strip()
        merged["notify_umos"] = [legacy] if legacy else []
        merged.pop("notify_umo", None)

    for field, label in (("allowed_umos", "白名单"), ("blocked_umos", "黑名单")):
        if field in payload:
            merged[field] = normalize_umo_list(payload[field])
            if len(merged[field]) > 200:
                raise SettingsError(f"{label}最多 200 条")

    # 密钥：空字符串表示保持原值，避免"打开页面直接保存"把令牌清空。
    for field in SECRET_FIELDS:
        if field in payload:
            value = _clean(payload[field], _MAX_SECRET, field)
            if value:
                merged[field] = value

    # 归一化地址，避免存进 "192.168.1.5:8123" 这种半成品。
    if merged.get("ha_url"):
        merged["ha_url"] = normalize_ha_url(merged["ha_url"])

    return merged
