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

    async def get_settings(self):
        plugin = self.plugin
        settings = plugin.settings
        return json_response(
            {
                "success": True,
                "version": PLUGIN_VERSION,
                "plugin_name": PLUGIN_NAME,
                "settings": public_snapshot(plugin.config),
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
