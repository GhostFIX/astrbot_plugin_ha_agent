"""WebUI 设置页的后端接口。

Dashboard 的插件视图通过 bridge 调到这里，鉴权由 Dashboard 负责；
本模块只做参数解析、调用插件方法、包装响应。
"""

from __future__ import annotations

import logging
from typing import Any

try:  # AstrBot 新版接口
    from astrbot.api.web import error_response, json_response, request  # type: ignore
except ImportError:  # pragma: no cover - 旧版本走 Quart 兼容层
    from quart import jsonify, request  # type: ignore

    def json_response(data: Any, status_code: int = 200):
        response = jsonify(data)
        response.status_code = status_code
        return response

    def error_response(message: str, status_code: int = 400):
        response = jsonify({"success": False, "error": message})
        response.status_code = status_code
        return response


from .constants import PLUGIN_NAME, PLUGIN_VERSION
from .settings import SettingsError, public_snapshot

logger = logging.getLogger(PLUGIN_NAME)


async def _body() -> dict[str, Any]:
    """兼容新版 request 代理与旧版 Quart。"""
    try:
        data = await request.json(default={})
    except TypeError:
        data = await request.json()
    except AttributeError:  # pragma: no cover
        data = await request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _query(name: str, default: str = "") -> str:
    try:
        value = request.query.get(name, default)
    except AttributeError:  # pragma: no cover - Quart 旧接口
        value = request.args.get(name, default)
    if value is None:
        return default
    return str(value)


def _query_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(_query(name, str(default)) or default)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, value))


class HaAgentWebAPI:
    """设置页的三个接口。"""

    def __init__(self, plugin: Any) -> None:
        self.plugin = plugin

    def register(self) -> None:
        registrar = self.plugin.context.register_web_api
        registrar(
            f"/{PLUGIN_NAME}/settings",
            self.get_settings,
            ["GET"],
            "读取 HA 指令助手设置",
        )
        registrar(
            f"/{PLUGIN_NAME}/settings",
            self.save_settings,
            ["POST"],
            "保存 HA 指令助手设置",
        )
        registrar(
            f"/{PLUGIN_NAME}/settings/test",
            self.test_connection,
            ["POST"],
            "测试出站与入站通道",
        )
        registrar(
            f"/{PLUGIN_NAME}/settings/umos",
            self.list_umos,
            ["GET"],
            "列出已知会话（UMO）候选",
        )
        registrar(
            f"/{PLUGIN_NAME}/logs",
            self.get_logs,
            ["GET"],
            "读取插件日志",
        )
        registrar(
            f"/{PLUGIN_NAME}/logs/clear",
            self.clear_logs,
            ["POST"],
            "清空插件日志缓冲",
        )
        registrar(
            f"/{PLUGIN_NAME}/settings/token",
            self.rotate_token,
            ["POST"],
            "生成新的告警回传令牌",
        )

    async def get_settings(self):
        plugin = self.plugin
        settings = plugin.settings
        return json_response(
            {
                "success": True,
                "version": PLUGIN_VERSION,
                "plugin_name": PLUGIN_NAME,
                "settings": public_snapshot(plugin.config),
                "token": plugin.token_status(),
                "state": {
                    "outbound_mode": settings.outbound_transport,
                    "outbound_ready": plugin.transport is not None,
                    "inbound_mode": settings.inbound_transport,
                    "inbound_running": plugin.inbound is not None
                    and plugin.inbound.running
                    or (plugin.bridge is not None and plugin.bridge.is_connected()),
                    "notify_enabled": settings.notify_enabled,
                    "saved_at": plugin.last_saved_at,
                },
            }
        )

    async def save_settings(self):
        try:
            payload = await _body()
            snapshot = await self.plugin.save_settings(payload)
        except SettingsError as exc:
            return error_response(str(exc), status_code=400)
        except Exception:  # noqa: BLE001
            logger.exception("ha-agent: 保存设置失败")
            return error_response("保存失败，请查看 AstrBot 日志", status_code=500)

        return json_response(
            {
                "success": True,
                "message": "已保存并热生效",
                "settings": snapshot,
                "token": self.plugin.token_status(),
                "state": {
                    "outbound_ready": self.plugin.transport is not None,
                    "inbound_mode": self.plugin.settings.inbound_transport,
                },
            }
        )

    async def test_connection(self):
        try:
            report = await self.plugin.check_channels()
        except Exception:  # noqa: BLE001
            logger.exception("ha-agent: 通道自检失败")
            return error_response("自检失败，请查看 AstrBot 日志", status_code=500)
        return json_response({"success": True, **report})

    async def list_umos(self):
        """已知会话候选：见过的 + 配置里已经填过的。"""
        plugin = self.plugin
        entries = [dict(item) for item in plugin.umo_history.entries()]
        known = {item["umo"] for item in entries}

        settings = plugin.settings
        configured = (
            list(settings.notify_umos)
            + list(settings.allowed_umos)
            + list(settings.blocked_umos)
        )
        for umo in configured:
            if umo and umo not in known:
                entries.append({"umo": umo, "label": "", "last_seen": ""})
                known.add(umo)

        return json_response({"success": True, "umos": entries[:500]})

    async def get_logs(self):
        plugin = self.plugin
        limit = _query_int("limit", 200, 1, 800)
        level = _query("level", "DEBUG").upper()
        return json_response(
            {
                "success": True,
                "entries": plugin.logs.snapshot(limit=limit, min_level=level),
                **plugin.logs.stats(),
            }
        )

    async def clear_logs(self):
        self.plugin.logs.clear()
        return json_response({"success": True, "cleared": True})

    async def rotate_token(self):
        try:
            token = await self.plugin.rotate_inbound_token()
        except Exception:  # noqa: BLE001
            logger.exception("ha-agent: 生成令牌失败")
            return error_response("生成令牌失败，请查看 AstrBot 日志", status_code=500)

        return json_response(
            {
                "success": True,
                "token": token,
                "notice": "令牌只显示这一次，关闭或刷新页面后不再显示。",
            }
        )
