"""MQTT 适配层：用假 paho 客户端覆盖回调转接、主题路由与异常文案。

注意：这里没有真实 Broker。假客户端实现的正是本插件用到的那几个方法，
所以测的是"适配逻辑"，不是 paho 本身与 broker 的互通性。
"""

from __future__ import annotations

import asyncio
import json

import pytest

import core.mqtt as mqtt_module
from core.mqtt import MqttBridge, MqttError, MqttTransport, parse_notify_payload


class FakeMessageInfo:
    def __init__(self, rc: int = 0) -> None:
        self.rc = rc


class FakeClient:
    def __init__(self) -> None:
        self.on_connect = None
        self.on_disconnect = None
        self.on_message = None
        self.credentials: tuple | None = None
        self.tls_enabled = False
        self.connected_to: tuple | None = None
        self.subscriptions: list[tuple] = []
        self.published: list[tuple] = []
        self.loop_started = False
        self.stopped = False
        self.publish_rc = 0
        self.subscribe_error: Exception | None = None

    def username_pw_set(self, username, password=None):
        self.credentials = (username, password)

    def tls_set(self):
        self.tls_enabled = True

    def reconnect_delay_set(self, min_delay=1, max_delay=120):
        self.reconnect_delay = (min_delay, max_delay)

    def connect_async(self, host, port, keepalive):
        self.connected_to = (host, port, keepalive)

    def loop_start(self):
        self.loop_started = True

    def loop_stop(self):
        self.loop_started = False

    def disconnect(self):
        self.stopped = True

    def subscribe(self, topic, qos=0):
        if self.subscribe_error is not None:
            raise self.subscribe_error
        self.subscriptions.append((topic, qos))
        return 0, 1

    def publish(self, topic, payload, qos=0):
        self.published.append((topic, payload, qos))
        return FakeMessageInfo(self.publish_rc)

    # 测试里手动触发回调
    def fire_connect(self, rc=0):
        self.on_connect(self, None, {}, rc)

    def fire_message(self, topic: str, payload: bytes):
        message = type("M", (), {"topic": topic, "payload": payload})()
        self.on_message(self, None, message)


def _bridge(client: FakeClient, **kwargs) -> MqttBridge:
    defaults = {
        "host": "192.168.1.5",
        "topic_command": "astrbot/ha/command",
        "topic_notify": "astrbot/ha/notify",
        "client_factory": lambda: client,
    }
    defaults.update(kwargs)
    return MqttBridge(**defaults)


def _running(fn):
    async def scenario():
        return fn()

    return asyncio.run(scenario())


def _capture_scheduled(monkeypatch, bucket: list) -> None:
    """把"投递回事件循环"换成捕获协程，测试里再手动跑掉。"""
    monkeypatch.setattr(
        mqtt_module,
        "run_coroutine_threadsafe",
        lambda coro, loop: bucket.append(coro),
    )


# ---------------------------------------------------------------- 负载解析


def test_parse_json_payload():
    payload = json.dumps({"message": "客厅 31 度", "title": "高温"}).encode()
    assert parse_notify_payload(payload) == ("高温", "客厅 31 度")


def test_parse_json_without_title():
    payload = json.dumps({"message": "hi"}).encode()
    assert parse_notify_payload(payload) == ("", "hi")


def test_parse_plain_text():
    assert parse_notify_payload(b"hello") == ("", "hello")


def test_parse_rejects_empty_and_garbage():
    assert parse_notify_payload(b"   ") is None
    assert parse_notify_payload(b'{"title": "x"}') is None
    assert parse_notify_payload(b"[1,2]") is None
    assert parse_notify_payload(b"\xff\xfe") is None


def test_parse_truncates_long_message():
    long_text = "x" * 9000
    parsed = parse_notify_payload(long_text.encode())
    assert parsed is not None
    assert len(parsed[1]) == 4000


def test_multiline_json_message_is_preserved():
    payload = json.dumps({"message": "第一行\n第二行\n\n第四行"}).encode()
    assert parse_notify_payload(payload) == ("", "第一行\n第二行\n\n第四行")


def test_multiline_plain_text_is_preserved():
    assert parse_notify_payload("A\nB\n\nC".encode()) == ("", "A\nB\n\nC")


def test_crlf_is_normalized_to_lf():
    assert parse_notify_payload(b"A\r\nB\r\n") == ("", "A\nB")

    payload = json.dumps({"message": "A\r\nB\rC", "title": "T\r\nX"}).encode()
    assert parse_notify_payload(payload) == ("T\nX", "A\nB\nC")


# ---------------------------------------------------------------- 连接与发布


def test_start_connects_and_uses_credentials():
    client = FakeClient()
    bridge = _bridge(client, username="user", password="pw")

    _running(bridge.start)

    assert client.connected_to == ("192.168.1.5", 1883, 60)
    assert client.loop_started is True
    assert client.credentials == ("user", "pw")


def test_start_without_credentials_skips_auth():
    client = FakeClient()
    _running(_bridge(client).start)
    assert client.credentials is None


def test_tls_flag_enables_tls():
    client = FakeClient()
    _running(_bridge(client, tls=True).start)
    assert client.tls_enabled is True


def test_connect_callback_subscribes_notify_topic():
    client = FakeClient()
    bridge = _bridge(client, on_notify=lambda t, m: asyncio.sleep(0))

    _running(bridge.start)
    client.fire_connect(0)

    assert bridge.is_connected() is True
    assert ("astrbot/ha/notify", 1) in client.subscriptions


def test_refused_connection_is_not_connected():
    client = FakeClient()
    bridge = _bridge(client, on_notify=lambda t, m: asyncio.sleep(0))

    _running(bridge.start)
    client.fire_connect(5)

    assert bridge.is_connected() is False
    assert client.subscriptions == []


def test_publish_sends_json_to_command_topic():
    client = FakeClient()
    bridge = _bridge(client)

    _running(bridge.start)
    client.fire_connect(0)
    bridge.publish_command({"command": "开电脑"})

    topic, body, qos = client.published[0]
    assert topic == "astrbot/ha/command"
    assert qos == 1
    assert json.loads(body) == {"command": "开电脑"}


def test_publish_before_connected_raises():
    client = FakeClient()
    bridge = _bridge(client)

    _running(bridge.start)
    with pytest.raises(MqttError, match="未连接"):
        bridge.publish_command({"command": "x"})


def test_publish_failure_code_raises():
    client = FakeClient()
    client.publish_rc = 4
    bridge = _bridge(client)

    _running(bridge.start)
    client.fire_connect(0)
    with pytest.raises(MqttError, match="rc=4"):
        bridge.publish_command({"command": "x"})


def test_stop_disconnects_and_clears_state():
    client = FakeClient()
    bridge = _bridge(client, on_notify=lambda t, m: asyncio.sleep(0))

    _running(bridge.start)
    client.fire_connect(0)
    bridge.stop()

    assert client.stopped is True
    assert client.loop_started is False
    assert bridge.is_connected() is False


# ---------------------------------------------------------------- 入站路由


def test_message_on_notify_topic_is_scheduled(monkeypatch):
    scheduled: list = []
    _capture_scheduled(monkeypatch, scheduled)

    delivered: list[tuple[str, str]] = []

    async def on_notify(title: str, message: str) -> None:
        delivered.append((title, message))

    client = FakeClient()
    bridge = _bridge(client, on_notify=on_notify)
    _running(bridge.start)
    client.fire_message("astrbot/ha/notify", b'{"message":"hi","title":"t"}')

    assert len(scheduled) == 1
    # 手动跑掉被调度的协程，避免 "never awaited" 警告
    asyncio.run(scheduled[0])


def test_message_on_other_topic_is_ignored(monkeypatch):
    scheduled: list = []
    _capture_scheduled(monkeypatch, scheduled)

    client = FakeClient()
    bridge = _bridge(client, on_notify=lambda t, m: asyncio.sleep(0))
    _running(bridge.start)
    client.fire_message("astrbot/ha/other", b"hi")

    assert scheduled == []


def test_unparsable_message_is_dropped(monkeypatch):
    scheduled: list = []
    _capture_scheduled(monkeypatch, scheduled)

    client = FakeClient()
    bridge = _bridge(client, on_notify=lambda t, m: asyncio.sleep(0))
    _running(bridge.start)
    client.fire_message("astrbot/ha/notify", b"   ")

    assert scheduled == []


# ---------------------------------------------------------------- Transport


def test_transport_send_and_ping():
    client = FakeClient()
    bridge = _bridge(client)
    transport = MqttTransport(bridge, "astrbot/ha/command")

    _running(bridge.start)
    assert transport.label == "MQTT · astrbot/ha/command"

    async def scenario():
        await transport.send({"command": "x"})
        return await transport.ping()

    # 未连接时 send 抛错
    with pytest.raises(MqttError):
        asyncio.run(scenario())

    client.fire_connect(0)

    async def scenario2():
        await transport.send({"command": "x"})
        return await transport.ping()

    assert asyncio.run(scenario2()) == (True, "已连接")
    assert client.published[-1][0] == "astrbot/ha/command"


def test_transport_close_stops_bridge():
    client = FakeClient()
    bridge = _bridge(client)
    transport = MqttTransport(bridge, "astrbot/ha/command")

    _running(bridge.start)
    asyncio.run(transport.close())

    assert client.loop_started is False
