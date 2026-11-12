"""HA 告警回传的 HTTP 监听。

一个只做 POST 的小 HTTP 服务：HA 用 ``rest_command`` 把文字推过来，插件
校验令牌后交给业务层转发到指定会话。

不使用 AstrBot 的 ``register_web_api``：那条路由挂在 WebUI 的鉴权链路后面，
不同版本行为不一致；独立监听端口 + 自己的令牌，行为完全可预测。
"""

from __future__ import annotations

import hmac
import json
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable

from aiohttp import web

from .constants import INBOUND_PATH, MAX_NOTIFY_CHARS
from .text import normalize_newlines


@dataclass(frozen=True)
class DeliveryReport:
    """一次告警投递的结果，可能包含多个目标。"""

    ok: bool
    error: str | None = None
    delivered: int = 0
    total: int = 0


Deliver = Callable[[str, str], Awaitable[DeliveryReport]]

_ERROR_STATUS = {
    "notify_umo_not_configured": 409,
    "platform_not_found": 502,
    "empty_message": 400,
    "deliver_failed": 500,
}


def _coerce_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        # 保留正文里的换行，只把 CRLF/CR 归一化成 LF。
        return normalize_newlines(value).strip()
    if isinstance(value, (int, float)):
        return str(value)
    return ""


class InboundServer:
    """带令牌校验的告警接收端。"""

    def __init__(
        self,
        *,
        deliver: Deliver,
        token: str,
        host: str = "0.0.0.0",
        port: int = 0,
        path: str = INBOUND_PATH,
        logger: logging.Logger | None = None,
    ) -> None:
        self._deliver = deliver
        self._token = token
        self._host = host
        self._port = port
        self._path = path
        self._logger = logger or logging.getLogger(__name__)
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None

    @property
    def port(self) -> int:
        return self._port

    @property
    def path(self) -> str:
        return self._path

    @property
    def running(self) -> bool:
        return self._runner is not None and self._site is not None

    def set_token(self, token: str) -> None:
        """原地更新校验令牌，避免为换令牌重启端口。"""
        self._token = str(token or "")

    def _authorised(self, request: web.Request) -> bool:
        if not self._token:
            return False
        supplied = request.headers.get("X-Auth-Token", "")
        if not supplied:
            auth = request.headers.get("Authorization", "")
            if auth[:7].lower() == "bearer ":
                supplied = auth[7:]
        # 统一按字节比较：配置里手填了非 ASCII 令牌也不会抛 TypeError。
        return hmac.compare_digest(
            supplied.strip().encode("utf-8"), self._token.encode("utf-8")
        )

    @staticmethod
    def _json(payload: dict, status: int) -> web.Response:
        return web.json_response(payload, status=status)

    async def _handle(self, request: web.Request) -> web.Response:
        if request.method != "POST":
            return self._json(
                {"ok": False, "error": "method_not_allowed"}, status=405
            )
        if not self._authorised(request):
            return self._json({"ok": False, "error": "invalid_token"}, status=401)

        # 自己解析 body 而不是用 request.json()：不同 aiohttp 版本对
        # ``content_type`` 参数、以及 body 取值方法的支持不一致，
        # 直接 read() + json.loads 最稳。
        try:
            raw = await request.read()
            payload = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return self._json({"ok": False, "error": "invalid_json"}, status=400)
        if not isinstance(payload, dict):
            return self._json({"ok": False, "error": "invalid_json"}, status=400)

        message = _coerce_text(payload.get("message"))
        if not message:
            return self._json({"ok": False, "error": "empty_message"}, status=400)
        if len(message) > MAX_NOTIFY_CHARS:
            message = message[:MAX_NOTIFY_CHARS] + "…"

        title = _coerce_text(payload.get("title"))
        try:
            report = await self._deliver(title, message)
        except Exception:  # noqa: BLE001 - 业务层异常也要回一个明确状态码
            self._logger.exception("inbound delivery raised")
            report = DeliveryReport(False, "deliver_failed")

        if report.ok:
            return self._json(
                {
                    "ok": True,
                    "delivered": True,
                    "sent": report.delivered,
                    "targets": report.total,
                },
                status=200,
            )
        code = report.error or "deliver_failed"
        return self._json(
            {
                "ok": False,
                "error": code,
                "sent": report.delivered,
                "targets": report.total,
            },
            status=_ERROR_STATUS.get(code, 500),
        )

    async def start(self) -> int:
        """启动监听，返回实际端口（传 0 时由系统分配）。"""
        app = web.Application()
        app.router.add_route("*", self._path, self._handle)
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        site = web.TCPSite(runner, self._host, self._port)
        try:
            await site.start()
        except OSError:
            await runner.cleanup()
            raise

        addresses = getattr(runner, "addresses", None) or []
        if addresses:
            self._port = int(addresses[0][1])
        self._runner = runner
        self._site = site
        return self._port

    async def stop(self) -> None:
        runner = self._runner
        self._runner = None
        self._site = None
        if runner is not None:
            await runner.cleanup()
