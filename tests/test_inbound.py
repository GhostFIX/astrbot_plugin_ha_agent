"""入站监听：令牌、状态码、长度截断，全部走真实 HTTP。"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any

import aiohttp

from core.constants import MAX_NOTIFY_CHARS
from core.inbound import DeliveryReport, InboundServer


@asynccontextmanager
async def _running(deliver, token: str = "good-token"):
    server = InboundServer(deliver=deliver, token=token, host="127.0.0.1", port=0)
    port = await server.start()
    try:
        yield server, f"http://127.0.0.1:{port}{server.path}"
    finally:
        await server.stop()


def _request(
    deliver,
    *,
    payload: Any = None,
    headers: dict[str, str] | None = None,
    method: str = "post",
    raw_body: str | None = None,
) -> tuple[int, dict]:
    """在真实的监听器上打一次请求，返回 (状态码, JSON 体)。"""

    async def scenario():
        async with _running(deliver) as (_, base):
            async with aiohttp.ClientSession() as session:
                kwargs: dict[str, Any] = {"headers": headers or {}}
                if raw_body is not None:
                    kwargs["data"] = raw_body
                else:
                    kwargs["json"] = payload
                async with session.request(method, base, **kwargs) as resp:
                    return resp.status, await resp.json()

    return asyncio.run(scenario())


AUTH = {"X-Auth-Token": "good-token"}


def test_valid_token_delivers_message():
    received: list[tuple[str, str]] = []

    async def deliver(title: str, message: str):
        received.append((title, message))
        return DeliveryReport(True)

    status, body = _request(deliver, payload={"message": "客厅 31 度"}, headers=AUTH)

    assert status == 200
    assert body["ok"] is True
    assert body["delivered"] is True
    assert body["targets"] == 0
    assert received == [("", "客厅 31 度")]


def test_title_is_forwarded():
    received: list[tuple[str, str]] = []

    async def deliver(title: str, message: str):
        received.append((title, message))
        return DeliveryReport(True)

    status, _ = _request(
        deliver, payload={"message": "客厅 31 度", "title": "高温"}, headers=AUTH
    )

    assert status == 200
    assert received == [("高温", "客厅 31 度")]


def test_bearer_token_is_accepted():
    async def deliver(title: str, message: str):
        return DeliveryReport(True)

    status, _ = _request(
        deliver,
        payload={"message": "hi"},
        headers={"Authorization": "Bearer good-token"},
    )

    assert status == 200


def test_non_ascii_token_does_not_crash():
    """配置里手填中文令牌时，比较逻辑不能抛 TypeError。"""

    class _FakeRequest:
        def __init__(self, headers: dict[str, str]) -> None:
            self.headers = headers

    async def deliver(title: str, message: str):
        return DeliveryReport(True)

    server = InboundServer(deliver=deliver, token="中文令牌")
    assert server._authorised(_FakeRequest({"X-Auth-Token": "中文令牌"})) is True
    assert server._authorised(_FakeRequest({"X-Auth-Token": "中文"})) is False
    assert server._authorised(_FakeRequest({})) is False


def test_wrong_token_is_rejected():
    async def deliver(title: str, message: str):
        raise AssertionError("不应被调用")

    status, body = _request(
        deliver, payload={"message": "hi"}, headers={"X-Auth-Token": "nope"}
    )

    assert status == 401
    assert body["error"] == "invalid_token"


def test_missing_token_is_rejected():
    async def deliver(title: str, message: str):
        raise AssertionError("不应被调用")

    status, _ = _request(deliver, payload={"message": "hi"})

    assert status == 401


def test_unconfigured_notify_target_returns_409():
    async def deliver(title: str, message: str):
        return DeliveryReport(False, "notify_umo_not_configured")

    status, body = _request(deliver, payload={"message": "hi"}, headers=AUTH)

    assert status == 409
    assert body["error"] == "notify_umo_not_configured"


def test_partial_delivery_reports_counts():
    async def deliver(title: str, message: str):
        return DeliveryReport(True, None, delivered=2, total=3)

    status, body = _request(deliver, payload={"message": "hi"}, headers=AUTH)

    assert status == 200
    assert body["ok"] is True
    assert body["sent"] == 2
    assert body["targets"] == 3


def test_multiline_message_is_delivered_verbatim():
    received: list[str] = []

    async def deliver(title: str, message: str):
        received.append(message)
        return DeliveryReport(True)

    status, _ = _request(
        deliver,
        payload={"message": "第一行\n第二行\n\n第四行"},
        headers=AUTH,
    )

    assert status == 200
    assert received == ["第一行\n第二行\n\n第四行"]


def test_crlf_is_normalized_on_http():
    received: list[str] = []

    async def deliver(title: str, message: str):
        received.append(message)
        return DeliveryReport(True)

    _request(deliver, payload={"message": "A\r\nB\rC"}, headers=AUTH)

    assert received == ["A\nB\nC"]


def test_set_token_takes_effect_immediately():
    received: list[str] = []

    async def deliver(title: str, message: str):
        received.append(message)
        return DeliveryReport(True)

    async def scenario():
        async with _running(deliver) as (server, base):
            server.set_token("rotated-token")
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    base, json={"message": "old"}, headers={"X-Auth-Token": "good-token"}
                ) as old:
                    async with session.post(
                        base,
                        json={"message": "new"},
                        headers={"X-Auth-Token": "rotated-token"},
                    ) as new:
                        return old.status, new.status

    assert asyncio.run(scenario()) == (401, 200)
    assert received == ["new"]


def test_platform_not_found_returns_502():
    async def deliver(title: str, message: str):
        return DeliveryReport(False, "platform_not_found")

    status, _ = _request(deliver, payload={"message": "hi"}, headers=AUTH)

    assert status == 502


def test_empty_message_returns_400():
    async def deliver(title: str, message: str):
        raise AssertionError("不应被调用")

    status, body = _request(deliver, payload={"message": "   "}, headers=AUTH)

    assert status == 400
    assert body["error"] == "empty_message"


def test_invalid_json_returns_400():
    async def deliver(title: str, message: str):
        raise AssertionError("不应被调用")

    status, body = _request(
        deliver,
        headers={**AUTH, "Content-Type": "application/json"},
        raw_body="not json",
    )

    assert status == 400
    assert body["error"] == "invalid_json"


def test_get_is_rejected():
    async def deliver(title: str, message: str):
        raise AssertionError("不应被调用")

    status, body = _request(deliver, method="get", headers=AUTH)

    assert status == 405
    assert body["error"] == "method_not_allowed"


def test_overlong_message_is_truncated():
    received: list[str] = []

    async def deliver(title: str, message: str):
        received.append(message)
        return DeliveryReport(True)

    status, _ = _request(
        deliver, payload={"message": "x" * (MAX_NOTIFY_CHARS + 50)}, headers=AUTH
    )

    assert status == 200
    assert len(received[0]) == MAX_NOTIFY_CHARS + 1


def test_server_reports_bound_port():
    async def deliver(title: str, message: str):
        return DeliveryReport(True)

    async def scenario():
        async with _running(deliver) as (server, base):
            return server.port, server.running, base

    port, running, base = asyncio.run(scenario())
    assert port > 0
    assert running is True
    assert base.endswith("/ha/notify")
