"""MQTT 双向通道。

一条连接同时承担两个方向：

- 出站：把指令 payload 发布到 ``<前缀>/command``，HA 用 ``mqtt`` 触发器监听；
- 入站：订阅 ``<前缀>/notify``，收到告警交给业务层转发到会话。

paho-mqtt 是同步库，自带后台线程；这里只做三件事：转接回调、把回调投递
回 asyncio 事件循环、把异常翻译成能直接展示的文案。客户端可通过
``client_factory`` 注入，测试用假客户端覆盖适配逻辑。
"""

from __future__ import annotations

import json
import logging
import uuid
from asyncio import AbstractEventLoop, get_running_loop, run_coroutine_threadsafe
from typing import Any, Awaitable, Callable

from .constants import (
    MAX_NOTIFY_CHARS,
    MQTT_DEFAULT_PORT,
    MQTT_KEEPALIVE,
    MQTT_QOS,
    PLUGIN_NAME,
)
from .text import normalize_newlines

NotifyHandler = Callable[[str, str], Awaitable[None]]


class MqttError(RuntimeError):
    """MQTT 连接或发布失败，文案可直接展示给用户。"""


def _is_success(rc: Any) -> bool:
    """兼容 paho 1.x 的 int 返回码与 2.x 的 ReasonCode。"""
    value = getattr(rc, "value", rc)
    try:
        return int(value) == 0
    except (TypeError, ValueError):
        return False


def parse_notify_payload(payload: bytes) -> tuple[str, str] | None:
    """把 notify 主题上的负载解析成 ``(title, message)``。

    支持 JSON 对象 ``{"message": "...", "title": "..."}``，也支持纯文本
    （整段当作 message，title 为空）。无法解析或内容为空时返回 ``None``。
    """
    try:
        text = normalize_newlines(payload.decode("utf-8")).strip()
    except UnicodeDecodeError:
        return None
    if not text:
        return None

    try:
        data = json.loads(text)
    except ValueError:
        return "", text[:MAX_NOTIFY_CHARS]

    if isinstance(data, str):
        message = normalize_newlines(data).strip()
        return ("", message[:MAX_NOTIFY_CHARS]) if message else None

    if not isinstance(data, dict):
        return None

    raw_message = data.get("message")
    raw_title = data.get("title")
    if not isinstance(raw_message, (str, int, float)):
        return None
    message = normalize_newlines(str(raw_message)).strip()
    if not message:
        return None
    title = (
        normalize_newlines(str(raw_title)).strip()
        if isinstance(raw_title, (str, int, float))
        else ""
    )
    return title, message[:MAX_NOTIFY_CHARS]


class MqttBridge:
    """paho-mqtt 的薄适配层。"""

    def __init__(
        self,
        *,
        host: str,
        topic_command: str,
        topic_notify: str,
        port: int = MQTT_DEFAULT_PORT,
        username: str = "",
        password: str = "",
        tls: bool = False,
        client_id: str = "",
        on_notify: NotifyHandler | None = None,
        client_factory: Callable[[], Any] | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._tls = tls
        self._client_id = client_id or f"{PLUGIN_NAME}-{uuid.uuid4().hex[:8]}"
        self._topic_command = topic_command
        self._topic_notify = topic_notify
        self._on_notify = on_notify
        self._client_factory = client_factory
        self._logger = logger or logging.getLogger(__name__)
        self._client: Any = None
        self._loop: AbstractEventLoop | None = None
        self._connected = False

    # ---------------------------------------------------------------- 生命周期

    def start(self) -> None:
        """建立连接并启动后台线程。连不上不会抛错，paho 会自动重连。"""
        if self._client is not None:
            return
        self._loop = get_running_loop()
        client = self._build_client()
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message

        if self._username:
            client.username_pw_set(self._username, self._password or None)
        if self._tls:
            client.tls_set()
        try:
            client.reconnect_delay_set(min_delay=1, max_delay=30)
        except AttributeError:  # 个别版本没有这个方法
            pass

        try:
            client.connect_async(self._host, self._port, MQTT_KEEPALIVE)
            client.loop_start()
        except Exception as exc:  # noqa: BLE001 - 统一转成可展示的文案
            raise MqttError(f"MQTT 启动失败：{exc}") from exc
        self._client = client

    def stop(self) -> None:
        client = self._client
        self._client = None
        self._connected = False
        if client is None:
            return
        try:
            client.disconnect()
        except Exception:  # noqa: BLE001 - 关闭阶段不抛异常
            self._logger.debug("mqtt disconnect failed", exc_info=True)
        try:
            client.loop_stop()
        except Exception:  # noqa: BLE001
            self._logger.debug("mqtt loop_stop failed", exc_info=True)

    # ------------------------------------------------------------------ 发布

    def publish_command(self, payload: dict[str, Any]) -> None:
        client = self._client
        if client is None:
            raise MqttError("MQTT 尚未启动。")
        if not self._connected:
            raise MqttError("MQTT 未连接 Broker，请检查地址、账号和网络。")
        body = json.dumps(payload, ensure_ascii=False)
        try:
            info = client.publish(self._topic_command, body, qos=MQTT_QOS)
        except Exception as exc:  # noqa: BLE001
            raise MqttError(f"MQTT 发布失败：{exc}") from exc
        rc = getattr(info, "rc", 0)
        if rc != 0:
            raise MqttError(f"MQTT 发布失败（rc={rc}），客户端可能正在重连。")

    def is_connected(self) -> bool:
        return self._connected

    # ------------------------------------------------------------------ 回调

    def _on_connect(
        self, client: Any, userdata: Any, flags: Any, rc: Any, properties: Any = None
    ) -> None:
        """paho 1.x 传 4 个参数，2.x 传 5 个，签名兼容两者。"""
        if not _is_success(rc):
            self._connected = False
            self._logger.warning("MQTT 连接被拒绝：%s", rc)
            return
        self._connected = True
        self._logger.info("MQTT 已连接 %s:%s", self._host, self._port)
        if self._on_notify is not None:
            try:
                client.subscribe(self._topic_notify, qos=MQTT_QOS)
            except Exception:  # noqa: BLE001
                self._logger.exception("MQTT 订阅失败：%s", self._topic_notify)

    def _on_disconnect(self, client: Any, userdata: Any, *args: Any) -> None:
        self._connected = False
        self._logger.warning("MQTT 连接断开，paho 会自动重连")

    def _on_message(self, client: Any, userdata: Any, message: Any) -> None:
        topic = getattr(message, "topic", "") or ""
        payload = getattr(message, "payload", b"") or b""
        self.handle_message(topic, payload)

    # ------------------------------------------------------------------ 路由

    def handle_message(self, topic: str, payload: bytes) -> None:
        """同步入口：校验主题、解析负载、投递回事件循环。"""
        if self._on_notify is None or topic != self._topic_notify:
            return
        parsed = parse_notify_payload(payload)
        if parsed is None:
            self._logger.warning("MQTT 告警负载无法解析，已忽略：%r", payload[:80])
            return
        title, message = parsed
        loop = self._loop
        if loop is None:
            self._logger.warning("MQTT 事件循环未就绪，丢弃一条告警")
            return
        run_coroutine_threadsafe(self._on_notify(title, message), loop)

    # ------------------------------------------------------------------ 构造

    def _build_client(self) -> Any:
        if self._client_factory is not None:
            return self._client_factory()
        try:
            import paho.mqtt.client as mqtt
        except ImportError as exc:  # pragma: no cover - 依赖缺失时的兜底
            raise MqttError(
                "未安装 paho-mqtt，无法使用 MQTT：pip install paho-mqtt"
            ) from exc

        if hasattr(mqtt, "CallbackAPIVersion"):
            return mqtt.Client(
                callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
                client_id=self._client_id,
                protocol=mqtt.MQTTv311,
            )
        return mqtt.Client(client_id=self._client_id, protocol=mqtt.MQTTv311)


class MqttTransport:
    """把 :class:`MqttBridge` 包装成统一的传输层接口。"""

    def __init__(self, bridge: MqttBridge, topic: str) -> None:
        self._bridge = bridge
        self.label = f"MQTT · {topic}"

    async def send(self, payload: dict[str, Any]) -> None:
        self._bridge.publish_command(payload)

    async def ping(self) -> tuple[bool, str]:
        if self._bridge.is_connected():
            return True, "已连接"
        return False, "MQTT 未连接 Broker"

    async def close(self) -> None:
        self._bridge.stop()
