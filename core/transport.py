"""把指令送到 Home Assistant 的传输层。

当前只有一种实现 :class:`RestTransport`：调用 HA 的 REST API
``POST /api/events/<event_type>``，让命令落到 HA 的事件总线上。

之所以用事件而不是直接调 service：HA 的事件总线按"发生了一次"设计，
同一条命令重复发送每次都会送达；而实体/状态是按"当前值"设计的，相同值
不会产生状态变化。命令是事件，不是状态。

``Transport`` 协议留给以后可能的 MQTT 实现，业务代码不感知具体传输方式。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Protocol, runtime_checkable

import aiohttp

from .constants import HA_PING_TIMEOUT, HA_REQUEST_TIMEOUT


class TransportError(RuntimeError):
    """传输层失败，消息可以直接展示给用户。"""

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        detail: str = "",
    ) -> None:
        super().__init__(message)
        self.status = status
        self.detail = detail


_STATUS_HINTS = {
    400: "HA 拒绝了请求（400），请检查事件类型是否合法。",
    401: "令牌无效或已被撤销（401），请重新生成长期访问令牌。",
    403: "令牌权限不足（403）。",
    404: "地址不对（404），请确认 ha_url 指向 HA 的 8123 端口。",
    405: "方法不被允许（405），ha_url 可能指向了别的服务。",
    500: "HA 内部错误（500），请查看 HA 日志。",
    502: "HA 前面的反向代理出错（502）。",
    503: "HA 不可用（503），可能正在重启。",
}


def describe_status(status: int) -> str:
    """把 HTTP 状态码翻译成一句可执行的提示。"""
    return _STATUS_HINTS.get(status, f"Home Assistant 返回 HTTP {status}。")


def describe_exception(exc: BaseException) -> str:
    """把 aiohttp 的异常翻译成一句人话。"""
    if isinstance(exc, TimeoutError) or isinstance(exc, asyncio.TimeoutError):
        return "连接 Home Assistant 超时，请检查网络、地址和防火墙。"
    if isinstance(exc, aiohttp.ClientConnectorError):
        return "无法连接 Home Assistant，请检查 ha_url、端口以及 AstrBot 所在网络。"
    if isinstance(exc, aiohttp.ClientSSLError):
        return "HTTPS 证书校验失败；自签名证书请在插件配置里关闭 verify_ssl。"
    if isinstance(exc, aiohttp.ClientError):
        return f"与 Home Assistant 通信失败：{exc}"
    return f"发送失败：{exc}"


@runtime_checkable
class Transport(Protocol):
    """传输层接口：发事件、探活、释放资源。"""

    async def fire_event(self, event_type: str, data: dict[str, Any]) -> None:
        """把 ``data`` 作为事件负载发给 HA，失败抛 :class:`TransportError`。"""

    async def ping(self) -> tuple[bool, str]:
        """探测 HA 是否可达，返回 (是否可达, 说明)。"""

    async def close(self) -> None:
        """释放底层连接。"""


class RestTransport:
    """基于 HA REST API 的实现。"""

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        verify_ssl: bool = True,
        timeout: float = HA_REQUEST_TIMEOUT,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._token = token
        self._verify_ssl = verify_ssl
        self._timeout = timeout
        self._session: aiohttp.ClientSession | None = None

    @property
    def base_url(self) -> str:
        return self._base

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }

    def _connector(self) -> aiohttp.TCPConnector | None:
        if self._verify_ssl:
            return None
        return aiohttp.TCPConnector(ssl=False)

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is not None and not self._session.closed:
            return self._session
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=self._timeout),
            connector=self._connector(),
        )
        return self._session

    async def fire_event(self, event_type: str, data: dict[str, Any]) -> None:
        url = f"{self._base}/events/{event_type}"
        # 先序列化成字节，避免不同 aiohttp 版本对 json= 的编码差异。
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        session = await self._ensure_session()
        try:
            async with session.post(url, data=body, headers=self._headers()) as resp:
                if resp.status >= 400:
                    text = await resp.text()
                    raise TransportError(
                        describe_status(resp.status),
                        status=resp.status,
                        detail=text[:200],
                    )
        except TransportError:
            raise
        except (TimeoutError, asyncio.TimeoutError) as exc:
            raise TransportError(describe_exception(exc)) from exc
        except aiohttp.ClientError as exc:
            raise TransportError(describe_exception(exc)) from exc

    async def ping(self, timeout: float = HA_PING_TIMEOUT) -> tuple[bool, str]:
        """``GET /api/`` 是 HA 官方的探活端点，返回 ``API running.``。"""
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=timeout),
                connector=self._connector(),
            ) as session:
                async with session.get(
                    self._base + "/", headers=self._headers()
                ) as resp:
                    if resp.status == 401:
                        return False, "令牌被拒绝"
                    if resp.status >= 400:
                        return False, f"HTTP {resp.status}"
                    return True, "可达"
        except Exception as exc:  # noqa: BLE001 - 探活不该把异常抛给调用方
            return False, describe_exception(exc)

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None
