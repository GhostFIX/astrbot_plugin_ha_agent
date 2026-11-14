"""冒烟测试：用最小 AstrBot 桩子真实 import ``main.py`` 并跑一次指令。

这里不打桩业务代码，只有 ``astrbot.*`` 被替换，验证的是"插件能被 AstrBot
加载、handler 不炸、返回文案正确"。
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BRIDGE_ROOT = ROOT.parent
for _path in (BRIDGE_ROOT, ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

_DATA_DIR = {"path": Path(tempfile.mkdtemp(prefix="ha-agent-test-"))}


def _module(name: str) -> types.ModuleType:
    mod = types.ModuleType(name)
    mod.__path__ = []  # type: ignore[attr-defined]
    sys.modules[name] = mod
    return mod


def _install_stubs() -> None:
    astrbot = _module("astrbot")
    api = _module("astrbot.api")
    event_mod = _module("astrbot.api.event")
    star_mod = _module("astrbot.api.star")
    web_mod = _module("astrbot.api.web")
    core = _module("astrbot.core")
    utils = _module("astrbot.core.utils")
    path_mod = _module("astrbot.core.utils.astrbot_path")

    class _Request:
        query = type("Q", (), {"get": staticmethod(lambda *a, **k: "")})()

        async def json(self, default=None, **kwargs):
            return default if default is not None else {}

    def json_response(data, status_code=200):
        return {"status": status_code, "body": data}

    def error_response(message, status_code=400):
        return {"status": status_code, "body": {"success": False, "error": message}}

    web_mod.request = _Request()
    web_mod.json_response = json_response
    web_mod.error_response = error_response

    class AstrMessageEvent:  # noqa: D401 - 仅用于类型标注
        pass

    class MessageChain:
        def __init__(self) -> None:
            self.text = ""

        def message(self, text: str) -> "MessageChain":
            self.text = text
            return self

    class _Filter:
        def command(self, *args, **kwargs):
            def deco(fn):
                return fn

            return deco

    class Context:
        def __init__(self) -> None:
            self.sent: list[tuple[str, str]] = []
            self.routes: list[tuple] = []

        def register_web_api(self, *args) -> None:
            self.routes.append(args)

        async def send_message(self, session: str, chain: MessageChain) -> bool:
            self.sent.append((session, chain.text))
            return True

    class Star:
        def __init__(self, context: Context) -> None:
            self.context = context

    event_mod.AstrMessageEvent = AstrMessageEvent
    event_mod.MessageChain = MessageChain
    event_mod.filter = _Filter()
    star_mod.Context = Context
    star_mod.Star = Star
    path_mod.get_astrbot_data_path = lambda: str(_DATA_DIR["path"])

    astrbot.api = api
    astrbot.core = core
    api.event = event_mod
    api.star = star_mod
    api.web = web_mod
    core.utils = utils
    utils.astrbot_path = path_mod


_install_stubs()

from Github.main import HaAgentPlugin  # noqa: E402  必须在桩子装好之后


class FakeEvent:
    def __init__(
        self,
        text: str,
        umo: str = "aiocqhttp:GroupMessage:1",
        admin: bool = False,
    ) -> None:
        self.message_str = text
        self.unified_msg_origin = umo
        self.call_llm = True
        self._admin = admin

    def get_message_str(self) -> str:
        return self.message_str

    def get_sender_name(self) -> str:
        return "测试用户"

    def get_sender_id(self) -> str:
        return "42"

    def get_platform_name(self) -> str:
        return "aiocqhttp"

    def is_admin(self) -> bool:
        return self._admin

    def plain_result(self, text: str) -> tuple[str, str]:
        return ("plain", text)


class FakeTransport:
    def __init__(self) -> None:
        self.label = "HA · astrbot_command"
        self.events: list[dict] = []
        self.closed = False

    async def send(self, payload: dict) -> None:
        self.events.append(payload)

    async def ping(self) -> tuple[bool, str]:
        return True, "可达"

    async def close(self) -> None:
        self.closed = True


BASE_CONFIG = {
    "ha_url": "http://127.0.0.1:8123",
    "ha_token": "token",
    "inbound_transport": "off",
}


def _build(config: dict | None = None):
    ctx = sys.modules["astrbot.api.star"].Context()
    plugin = HaAgentPlugin(ctx, {**BASE_CONFIG, **(config or {})})
    # 测试里不落任何全局日志器的 handler，避免用例之间互相累积。
    plugin.logs.detach()
    transport = FakeTransport()
    plugin.transport = transport
    return plugin, ctx, transport


def _run(plugin, event) -> list[tuple[str, str]]:
    async def scenario():
        return [item async for item in plugin.ha_command(event)]

    return asyncio.run(scenario())


def test_command_is_forwarded_verbatim():
    plugin, _, transport = _build()
    results = _run(plugin, FakeEvent("/ha 开电脑"))

    assert len(results) == 1
    assert "已发送" in results[0][1]
    assert len(transport.events) == 1
    payload = transport.events[0]
    assert payload["command"] == "开电脑"
    assert payload["session"] == "aiocqhttp:GroupMessage:1"
    assert payload["sender"] == "测试用户"
    assert payload["timestamp"]


def test_inner_spaces_are_preserved():
    plugin, _, transport = _build()
    _run(plugin, FakeEvent("/ha 开 电脑"))
    assert transport.events[0]["command"] == "开 电脑"


def test_bare_prefix_shows_usage_only():
    plugin, _, transport = _build()
    plugin.inbound_token = "abcdefghijklmnop"

    text = _run(plugin, FakeEvent("/ha"))[0][1]

    assert transport.events == []
    assert "用法" in text
    assert "/ha check" in text
    # 用法卡片不带会话 ID、不带令牌、不带通道状态
    assert "会话 ID" not in text
    assert "令牌" not in text
    assert "abcdefghijklmnop" not in text


def test_bare_prefix_is_permission_gated():
    plugin, _, transport = _build({"blocked_umos": ["aiocqhttp:GroupMessage:1"]})
    text = _run(plugin, FakeEvent("/ha"))[0][1]

    assert transport.events == []
    assert "无权限" in text
    assert "用法" not in text


def test_usage_denied_outside_whitelist():
    plugin, _, _ = _build({"allowed_umos": ["other:umo:1"]})
    text = _run(plugin, FakeEvent("/ha"))[0][1]

    assert "无权限" in text


def test_success_reply_can_be_disabled():
    plugin, _, transport = _build({"reply_on_success": False})

    results = _run(plugin, FakeEvent("/ha 开电脑"))

    assert results == []
    assert len(transport.events) == 1
    assert transport.events[0]["command"] == "开电脑"


def test_failure_reply_ignores_the_switch():
    from Github.core.transport import TransportError

    class BrokenTransport(FakeTransport):
        async def send(self, payload: dict) -> None:
            raise TransportError("令牌无效或已被撤销（401）", status=401)

    plugin, _, _ = _build({"reply_on_success": False})
    plugin.transport = BrokenTransport()

    results = _run(plugin, FakeEvent("/ha 开电脑"))

    assert len(results) == 1
    assert "发送失败" in results[0][1]


def test_stripped_prefix_still_works():
    plugin, _, transport = _build()
    _run(plugin, FakeEvent("开电脑"))
    assert transport.events[0]["command"] == "开电脑"


def test_blacklisted_session_is_rejected():
    plugin, _, transport = _build({"blocked_umos": ["aiocqhttp:GroupMessage:1"]})
    results = _run(plugin, FakeEvent("/ha 开电脑"))

    assert transport.events == []
    assert "禁止" in results[0][1]


def test_session_outside_whitelist_is_rejected():
    plugin, _, transport = _build({"allowed_umos": ["other:umo:1"]})
    results = _run(plugin, FakeEvent("/ha 开电脑"))

    assert transport.events == []
    assert "白名单" in results[0][1]


def test_missing_ha_config_is_reported():
    ctx = sys.modules["astrbot.api.star"].Context()
    plugin = HaAgentPlugin(ctx, {"inbound_transport": "off"})
    results = _run(plugin, FakeEvent("/ha 开电脑"))

    assert "还没配置" in results[0][1]


def test_transport_failure_is_surfaced():
    # 注意：这里必须用 Github.core 里的同一个异常类。插件以 ``Github.main``
    # 被导入时，相对导入会生成 ``Github.core.transport`` 这份模块对象，
    # 与测试里直接 ``import core.transport`` 得到的不是同一个类。
    from Github.core.transport import TransportError

    class BrokenTransport(FakeTransport):
        async def send(self, payload: dict) -> None:
            raise TransportError("令牌无效或已被撤销（401）", status=401)

    plugin, _, _ = _build()
    plugin.transport = BrokenTransport()
    results = _run(plugin, FakeEvent("/ha 开电脑"))

    assert "发送失败" in results[0][1]
    assert "401" in results[0][1]


def test_overlong_command_is_rejected():
    from Github.core.constants import MAX_COMMAND_CHARS

    plugin, _, transport = _build()
    results = _run(plugin, FakeEvent("/ha " + "x" * (MAX_COMMAND_CHARS + 1)))

    assert transport.events == []
    assert "太长" in results[0][1]


def test_deliver_prefixes_title_and_uses_notify_targets():
    plugin, ctx, _ = _build({"notify_umos": ["aiocqhttp:GroupMessage:9"]})

    async def scenario():
        return await plugin._deliver("高温", "客厅 31 度")

    report = asyncio.run(scenario())
    assert report.ok is True
    assert report.delivered == 1
    assert report.total == 1
    assert ctx.sent == [("aiocqhttp:GroupMessage:9", "【高温】\n客厅 31 度")]


def test_deliver_without_target_is_refused():
    plugin, ctx, _ = _build()

    async def scenario():
        return await plugin._deliver("", "客厅 31 度")

    report = asyncio.run(scenario())
    assert report.ok is False
    assert report.error == "notify_umo_not_configured"
    assert ctx.sent == []


def test_deliver_reports_missing_platform():
    class NoPlatformContext:
        async def send_message(self, session, chain):
            return False

    plugin, _, _ = _build({"notify_umos": ["bad:umo:1"]})
    plugin.context = NoPlatformContext()

    async def scenario():
        return await plugin._deliver("", "hi")

    report = asyncio.run(scenario())
    assert report.ok is False
    assert report.error == "platform_not_found"


def test_check_card_shows_status_without_token():
    plugin, _, transport = _build({"notify_umos": ["aiocqhttp:GroupMessage:9"]})
    plugin.inbound_token = "abcdefghijklmnop"

    text = _run(plugin, FakeEvent("/ha check"))[0][1]

    assert transport.events == []
    assert "会话 ID（SID）" in text
    assert "aiocqhttp:GroupMessage:1" in text
    assert "出站：" in text
    assert "入站：" in text
    assert "告警目标" in text
    assert "已生成" in text
    assert "abcdefghijklmnop" not in text


def test_check_is_permission_gated():
    plugin, _, _ = _build({"blocked_umos": ["aiocqhttp:GroupMessage:1"]})
    text = _run(plugin, FakeEvent("/ha check"))[0][1]

    assert "无权限" in text
    assert "会话 ID" not in text


def _free_port() -> int:
    import socket

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = int(sock.getsockname()[1])
    sock.close()
    return port


def test_inbound_lifecycle_end_to_end():
    """initialize() 真的开端口，HA 真发 HTTP，消息真的进了会话。"""
    import aiohttp

    port = _free_port()
    plugin, ctx, _ = _build(
        {
            "inbound_transport": "http",
            "inbound_port": port,
            "notify_umos": ["aiocqhttp:GroupMessage:9"],
        }
    )

    async def scenario():
        await plugin.initialize()
        try:
            assert plugin.inbound is not None
            assert plugin.inbound.running is True
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"http://127.0.0.1:{port}/ha/notify",
                    json={"message": "客厅 31 度", "title": "高温"},
                    headers={"X-Auth-Token": plugin.inbound_token},
                ) as resp:
                    return resp.status, await resp.json()
        finally:
            await plugin.terminate()

    status, body = asyncio.run(scenario())

    assert status == 200
    assert body["ok"] is True
    assert body["delivered"] is True
    assert ctx.sent == [("aiocqhttp:GroupMessage:9", "【高温】\n客厅 31 度")]
    assert plugin.inbound is None


# ------------------------------------------------------------ MQTT 模式接线


class FakeBridge:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.started = False
        self.published: list[dict] = []

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.started = False

    def is_connected(self) -> bool:
        return self.started

    def publish_command(self, payload: dict) -> None:
        self.published.append(payload)


def _mqtt_config(**overrides) -> dict:
    return {
        "outbound_transport": "mqtt",
        "mqtt_host": "10.0.0.2",
        "mqtt_port": 1883,
        "inbound_transport": "off",
        **overrides,
    }


def test_mqtt_outbound_is_selected_and_used(monkeypatch):
    import Github.main as main_mod

    monkeypatch.setattr(main_mod, "MqttBridge", FakeBridge)
    plugin, _, _ = _build(_mqtt_config())

    asyncio.run(plugin.initialize())
    assert isinstance(plugin.transport, main_mod.MqttTransport)

    results = _run(plugin, FakeEvent("/ha 开电脑"))

    assert "已发送" in results[0][1]
    assert plugin.transport.label == "MQTT · astrbot/ha/command"
    assert plugin._bridge.published[0]["command"] == "开电脑"


def test_mqtt_inbound_registers_handler(monkeypatch):
    import Github.main as main_mod

    monkeypatch.setattr(main_mod, "MqttBridge", FakeBridge)
    plugin, ctx, _ = _build(
        _mqtt_config(
            outbound_transport="rest",
            inbound_transport="mqtt",
            notify_umos=["aiocqhttp:GroupMessage:9"],
        )
    )

    asyncio.run(plugin.initialize())
    assert plugin._bridge.kwargs["topic_notify"] == "astrbot/ha/notify"
    assert plugin._bridge.kwargs["on_notify"] is not None

    asyncio.run(plugin._handle_mqtt_notify("高温", "客厅 31 度"))

    assert ctx.sent == [("aiocqhttp:GroupMessage:9", "【高温】\n客厅 31 度")]


def test_help_card_reflects_mqtt_mode(monkeypatch):
    import Github.main as main_mod

    monkeypatch.setattr(main_mod, "MqttBridge", FakeBridge)
    plugin, _, _ = _build(_mqtt_config(inbound_transport="mqtt"))

    asyncio.run(plugin.initialize())
    text = _run(plugin, FakeEvent("/ha check"))[0][1]

    assert "出站：MQTT" in text
    assert "astrbot/ha/command" in text
    assert "入站：MQTT 订阅 astrbot/ha/notify" in text


def test_mqtt_without_host_reports_not_ready():
    plugin, _, _ = _build({"outbound_transport": "mqtt", "inbound_transport": "off"})
    plugin.transport = None

    asyncio.run(plugin.initialize())
    results = _run(plugin, FakeEvent("/ha 开电脑"))

    assert plugin.transport is None
    assert "MQTT 未就绪" in results[0][1]


# ------------------------------------------------------------ WebUI 设置接口


class FakeConfig(dict):
    """模拟 AstrBotConfig：dict + save_config。"""

    def __init__(self, data: dict) -> None:
        super().__init__(data)
        self.saved: dict | None = None

    def save_config(self, replace_config: dict | None = None) -> None:
        if replace_config:
            self.update(replace_config)
        self.saved = dict(self)


def test_web_api_registers_routes():
    plugin, ctx, _ = _build()
    routes = [entry[0] for entry in ctx.routes]

    assert routes.count("/astrbot_plugin_ha_agent/settings") == 2
    assert "/astrbot_plugin_ha_agent/settings/test" in routes


def test_get_settings_endpoint_returns_masked_snapshot():
    plugin, _, _ = _build()
    result = asyncio.run(plugin.web_api.get_settings())

    body = result["body"]
    assert body["success"] is True
    assert body["settings"]["outbound_transport"] == "rest"
    assert body["settings"]["ha_token"] == ""
    assert body["settings"]["ha_token_set"] is True
    assert body["state"]["outbound_mode"] == "rest"


def test_get_settings_exposes_only_token_fingerprint():
    plugin, _, _ = _build()
    plugin.inbound_token = "abcdefghijklmnop"

    body = asyncio.run(plugin.web_api.get_settings())["body"]

    assert body["token"] == {"set": True, "hint": "abcd…mnop"}
    assert "abcdefghijklmnop" not in str(body)


def test_rotate_token_returns_plaintext_once_and_persists():
    plugin, _, _ = _build()
    old = plugin.inbound_token

    body = asyncio.run(plugin.web_api.rotate_token())["body"]
    token = body["token"]

    assert body["success"] is True
    assert token and token != old
    assert plugin.inbound_token == token
    assert plugin.secret_store.load() == token
    assert "只显示" in body["notice"]


def test_save_settings_endpoint_rejects_bad_value(monkeypatch):
    import Github.core.web_api as web_api_mod

    async def fake_body():
        return {"outbound_transport": "carrier-pigeon"}

    monkeypatch.setattr(web_api_mod, "_body", fake_body)
    plugin, _, _ = _build()

    result = asyncio.run(plugin.web_api.save_settings())

    assert result["status"] == 400
    assert result["body"]["success"] is False
    assert "rest / mqtt" in result["body"]["error"]


def test_save_settings_persists_and_hot_swaps_transport(monkeypatch):
    import Github.main as main_mod

    monkeypatch.setattr(main_mod, "MqttBridge", FakeBridge)

    ctx = sys.modules["astrbot.api.star"].Context()
    config = FakeConfig(dict(BASE_CONFIG))
    plugin = HaAgentPlugin(ctx, config)
    asyncio.run(plugin.initialize())

    snapshot = asyncio.run(
        plugin.save_settings({"outbound_transport": "mqtt", "mqtt_host": "10.0.0.9"})
    )

    assert plugin.settings.outbound_transport == "mqtt"
    assert isinstance(plugin.transport, main_mod.MqttTransport)
    assert config.saved is not None
    assert config.saved["mqtt_host"] == "10.0.0.9"
    assert snapshot["mqtt_host"] == "10.0.0.9"
    assert snapshot["ha_token"] == ""
    assert plugin.last_saved_at


def test_save_settings_keeps_blank_secret():
    ctx = sys.modules["astrbot.api.star"].Context()
    config = FakeConfig({**BASE_CONFIG, "ha_token": "keep-me"})
    plugin = HaAgentPlugin(ctx, config)

    asyncio.run(plugin.save_settings({"ha_token": ""}))

    assert config["ha_token"] == "keep-me"


def test_check_channels_reports_both_directions():
    plugin, _, _ = _build()
    report = asyncio.run(plugin.check_channels())

    assert report["outbound"]["mode"] == "rest"
    assert report["outbound"]["ok"] is True
    assert report["inbound"]["mode"] == "off"
    assert report["inbound"]["detail"] == "已关闭"


# ---------------------------------------------------------------- 多目标投递


def test_deliver_reports_adapter_failure_with_reason():
    plugin, _, _ = _build({"notify_umos": ["weixin:friend:abc"]})

    class ExplodingContext:
        async def send_message(self, session, chain):
            raise RuntimeError("failed to send 1 message segment(s)")

    plugin.context = ExplodingContext()
    report = asyncio.run(plugin._deliver("", "今日耗电：2.45 kWh"))

    assert report.ok is False
    assert report.error == "send_failed"
    assert "RuntimeError" in report.failures[0]
    assert "failed to send" in report.failures[0]


def test_deliver_reports_invalid_umo():
    plugin, _, _ = _build({"notify_umos": ["bad"]})

    class BadSessionContext:
        async def send_message(self, session, chain):
            raise ValueError("不合法的 session 字符串")

    plugin.context = BadSessionContext()
    report = asyncio.run(plugin._deliver("", "hi"))

    assert report.ok is False
    assert report.error == "invalid_umo"
    assert "UMO 不合法" in report.failures[0]


def test_deliver_multi_target_partial_failure():
    plugin, _, _ = _build({"notify_umos": ["good:1", "bad:2"]})

    class MixedContext:
        def __init__(self) -> None:
            self.sent: list[tuple[str, str]] = []

        async def send_message(self, session, chain):
            if session == "bad:2":
                return False
            self.sent.append((session, chain.text))
            return True

    ctx = MixedContext()
    plugin.context = ctx
    report = asyncio.run(plugin._deliver("", "hi"))

    assert report.ok is True
    assert report.delivered == 1
    assert report.total == 2
    assert ctx.sent == [("good:1", "hi")]


def test_deliver_multi_target_all_failed():
    plugin, _, _ = _build({"notify_umos": ["a:1", "b:2"]})

    class DeadContext:
        async def send_message(self, session, chain):
            return False

    plugin.context = DeadContext()
    report = asyncio.run(plugin._deliver("", "hi"))

    assert report.ok is False
    assert report.error == "platform_not_found"
    assert report.delivered == 0
    assert report.total == 2


def test_command_records_umo_history():
    plugin, _, _ = _build()
    _run(plugin, FakeEvent("/ha 开电脑"))

    entries = plugin.umo_history.entries()
    assert entries[0]["umo"] == "aiocqhttp:GroupMessage:1"
    assert entries[0]["label"] == "测试用户"


def test_list_umos_endpoint_includes_configured_targets():
    plugin, _, _ = _build({"notify_umos": ["aiocqhttp:GroupMessage:9"]})
    result = asyncio.run(plugin.web_api.list_umos())

    umos = [item["umo"] for item in result["body"]["umos"]]
    assert "aiocqhttp:GroupMessage:9" in umos


# ---------------------------------------------------------------- 日志页


def test_logs_routes_registered():
    plugin, ctx, _ = _build()
    routes = [entry[0] for entry in ctx.routes]

    assert routes.count("/astrbot_plugin_ha_agent/logs") == 1
    assert "/astrbot_plugin_ha_agent/logs/clear" in routes


def test_get_logs_endpoint_returns_entries():
    plugin, _, _ = _build()
    plugin.logs.record("INFO", "hello log")

    body = asyncio.run(plugin.web_api.get_logs())["body"]

    assert body["success"] is True
    assert any("hello log" in item["message"] for item in body["entries"])
    assert body["capacity"] >= 50
    assert "count" in body


def test_clear_logs_endpoint_empties_buffer():
    plugin, _, _ = _build()
    plugin.logs.record("INFO", "x")

    body = asyncio.run(plugin.web_api.clear_logs())["body"]

    assert body["cleared"] is True
    assert plugin.logs.snapshot() == []


# ---------------------------------------------------------------- 视图迁移


def test_legacy_view_dirs_are_reported(tmp_path):
    plugin, _, _ = _build()
    views = tmp_path / "views"
    (views / "settings").mkdir(parents=True)
    (views / "logs").mkdir()

    assert plugin._check_legacy_views(views) == ["logs"]


def test_clean_view_dirs_are_silent(tmp_path):
    plugin, _, _ = _build()
    views = tmp_path / "views"
    (views / "settings").mkdir(parents=True)

    assert plugin._check_legacy_views(views) == []
