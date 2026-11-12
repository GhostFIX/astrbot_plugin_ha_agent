"""出站传输层：对着真正的本地 HTTP 服务往返，不打桩。"""

from __future__ import annotations

import asyncio
import socket

from aiohttp import web

from core.transport import RestTransport, TransportError


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = int(sock.getsockname()[1])
    sock.close()
    return port


async def _start_fake_ha(handler) -> tuple[web.AppRunner, int]:
    app = web.Application()
    app.router.add_post("/api/events/{event_type}", handler)
    app.router.add_get("/api/", handler)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = int(runner.addresses[0][1])
    return runner, port


def test_send_posts_payload_to_event_endpoint():
    received: dict = {}

    async def handler(request: web.Request) -> web.Response:
        received["path"] = request.path
        received["auth"] = request.headers.get("Authorization")
        received["body"] = await request.json()
        return web.json_response({"message": "Event fired."})

    async def scenario():
        runner, port = await _start_fake_ha(handler)
        transport = RestTransport(f"http://127.0.0.1:{port}/api", "secret-token")
        try:
            await transport.send({"command": "开电脑"})
        finally:
            await transport.close()
            await runner.cleanup()

    asyncio.run(scenario())

    assert received["path"] == "/api/events/astrbot_command"
    assert received["auth"] == "Bearer secret-token"
    assert received["body"] == {"command": "开电脑"}


def test_401_is_reported_with_actionable_message():
    async def handler(request: web.Request) -> web.Response:
        return web.Response(status=401, text="unauthorized")

    async def scenario():
        runner, port = await _start_fake_ha(handler)
        transport = RestTransport(f"http://127.0.0.1:{port}/api", "bad")
        try:
            await transport.send({"command": "x"})
        except TransportError as exc:
            return exc
        finally:
            await transport.close()
            await runner.cleanup()
        return None

    error = asyncio.run(scenario())
    assert error is not None
    assert error.status == 401
    assert "令牌" in str(error)


def test_connection_refused_is_reported():
    async def scenario():
        transport = RestTransport(f"http://127.0.0.1:{_free_port()}/api", "t")
        try:
            await transport.send({"command": "x"})
        except TransportError as exc:
            return exc
        finally:
            await transport.close()
        return None

    error = asyncio.run(scenario())
    assert error is not None
    assert "无法连接" in str(error) or "通信失败" in str(error)


def test_ping_reports_reachable_and_rejected():
    async def ok_handler(request: web.Request) -> web.Response:
        return web.Response(text="API running.")

    async def bad_handler(request: web.Request) -> web.Response:
        return web.Response(status=401, text="unauthorized")

    async def scenario(handler):
        runner, port = await _start_fake_ha(handler)
        transport = RestTransport(f"http://127.0.0.1:{port}/api", "t")
        try:
            return await transport.ping()
        finally:
            await transport.close()
            await runner.cleanup()

    assert asyncio.run(scenario(ok_handler)) == (True, "可达")
    reachable, detail = asyncio.run(scenario(bad_handler))
    assert reachable is False
    assert "令牌" in detail
