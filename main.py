"""AstrBot ⇄ Home Assistant 文本通道。

这个插件不调用任何大模型，只做两件事：

1. ``/ha <文字>``：把 ``<文字>`` 原样作为 ``command`` 发到 HA（REST 事件或 MQTT）；
2. 接收 HA 推来的告警文字（HTTP 端口或 MQTT 订阅），转发到配置的会话。

出站与入站的传输方式各自独立可选：REST / MQTT / 关闭。
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star

from .core.access import access_decision
from .core.config import Settings
from .core.constants import (
    DATA_DIR_NAME,
    INBOUND_TOKEN_FILE,
    MAX_COMMAND_CHARS,
    PLUGIN_NAME,
    VIEW_NAME,
)
from .core.inbound import DeliveryReport, InboundServer
from .core.logs import LogBuffer
from .core.mqtt import MqttBridge, MqttError, MqttTransport
from .core.settings import merge_payload, public_snapshot
from .core.store import SecretStore, UmoHistory, generate_token
from .core.transport import RestTransport, TransportError
from .core.web_api import HaAgentWebAPI

try:  # AstrBot 提供的 logger，能按插件单独调等级。
    from astrbot.api import logger
except ImportError:  # pragma: no cover - 仅测试环境会走到
    logger = logging.getLogger(PLUGIN_NAME)

try:
    from astrbot.core.utils.astrbot_path import get_astrbot_data_path
except ImportError:  # pragma: no cover - 仅测试环境会走到
    def get_astrbot_data_path() -> str:
        return str(Path.cwd() / "data")


# 兼容 /ha、!ha、！ha、ha、/ha@bot 几种前缀写法。
_COMMAND_RE = re.compile(r"^\s*[/!！]?\s*ha(?:@\S+)?(?:\s+|$)", re.IGNORECASE)

USAGE_HEAD = (
    "📮 HA 指令助手\n"
    "\n"
    "用法：/ha <任意文字>\n"
    "  例：/ha 开电脑\n"
    "  重复发送同一条命令，每次都会送达 HA。\n"
    "\n"
    "查看通道状态：/ha check"
)


class HaAgentPlugin(Star):
    """把 AstrBot 变成 Home Assistant 的文本输入输出端口。"""

    def __init__(self, context: Context, config: Any = None):
        super().__init__(context)
        self.config = config if config is not None else {}
        self.settings = Settings.from_config(self.config)
        self.inbound_error = ""
        self.mqtt_error = ""

        data_root = Path(get_astrbot_data_path())
        self.data_dir = data_root / DATA_DIR_NAME / PLUGIN_NAME
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            logger.exception("ha-agent data directory could not be created")

        self.secret_store = SecretStore(self.data_dir / INBOUND_TOKEN_FILE)
        # 见过的会话，供设置页下拉选择。
        self.umo_history = UmoHistory(self.data_dir / "known_umos.json")
        # 令牌只存在数据目录里；1.x 写在配置里的旧值迁移过来后即视为历史。
        stored = self.secret_store.load()
        legacy = self.settings.inbound_token
        if not stored and legacy:
            try:
                self.secret_store.save(legacy)
            except OSError:
                logger.warning("%s: 旧令牌迁移失败，本次沿用配置值", PLUGIN_NAME)
            stored = legacy
        self.inbound_token = stored or legacy
        if not self.inbound_token:
            self.inbound_token, _ = self.secret_store.ensure_token()

        self.transport: RestTransport | MqttTransport | None = None
        self.inbound: InboundServer | None = None
        self._bridge: MqttBridge | None = None
        self.last_saved_at = ""

        # WebUI 日志页的数据来源：挂在 AstrBot 的日志器上，只保留本插件的记录。
        self.logs = LogBuffer()
        self._attach_logs()

        self.web_api = HaAgentWebAPI(self)
        try:
            self.web_api.register()
        except Exception:  # noqa: BLE001 - 老版本没有 register_web_api 也不该炸
            logger.exception("%s: web API registration failed", PLUGIN_NAME)

    @property
    def bridge(self) -> MqttBridge | None:
        """供 WebUI 状态查询用，避免把私有字段暴露给调用方。"""
        return self._bridge

    # ---------------------------------------------------------------- 生命周期

    async def initialize(self) -> None:
        """插件加载完成后按配置建立出站通道与入站监听。"""
        self._warn_configuration()
        self._check_legacy_views()
        await self._apply_runtime()
        self.logs.record(
            "INFO",
            f"通道已就绪：出站 {self.settings.outbound_transport}，"
            f"入站 {self.settings.inbound_transport}"
            f"，告警目标 {len(self.settings.notify_umos)} 个",
        )

    async def terminate(self) -> None:
        """插件卸载/停用时释放端口、连接池与 MQTT 连接。"""
        await self._shutdown_runtime()
        self.logs.detach()

    def _attach_logs(self) -> None:
        """把日志缓冲挂到可用的日志器上；挂不上也不影响插件运行。"""
        candidates = [
            getattr(self, "logger", None),  # 有的版本会给每个插件独立 logger
            logger,
            logging.getLogger("astrbot"),
        ]
        try:
            names = self.logs.attach(candidates)
            if not names:
                # 都挂不上时退到 root，再靠来源过滤只收本插件的记录。
                names = self.logs.attach([logging.getLogger()])
        except Exception:  # noqa: BLE001
            names = []
            logger.exception("%s: 日志缓冲挂载失败", PLUGIN_NAME)
        self.logs.record(
            "INFO",
            f"{PLUGIN_NAME} 日志缓冲已启动（监听：{'、'.join(names) or '无'}）",
        )

    def _check_legacy_views(self, root: Path | None = None) -> list[str]:
        """1.2.x 早期版本曾把日志做成独立视图；残留目录会让 WebUI 出现重复入口。

        这里只提醒、不自动删文件（插件不该动自己的安装目录），
        返回多余目录名便于测试。
        """
        base = root if root is not None else Path(__file__).resolve().parent / "views"
        try:
            names = sorted(item.name for item in base.iterdir() if item.is_dir())
        except OSError:
            return []

        stale = [name for name in names if name != VIEW_NAME]
        if stale:
            message = (
                f"检测到多余的插件视图目录 {stale}，WebUI 会出现重复入口；"
                f"请删除 {base / stale[0]} 后重载插件"
            )
            logger.warning("%s: %s", PLUGIN_NAME, message)
            self.logs.record("WARNING", message)
        return stale

    async def _apply_runtime(self) -> None:
        """按当前配置重建全部通道。改配置后也走这里热生效。"""
        await self._shutdown_runtime()
        self._setup_outbound()
        await self._setup_inbound()

    async def _shutdown_runtime(self) -> None:
        if self.inbound is not None:
            try:
                await self.inbound.stop()
            except Exception:  # noqa: BLE001 - 卸载阶段不抛异常
                logger.exception("%s: inbound server stop failed", PLUGIN_NAME)
            self.inbound = None
        if self.transport is not None:
            try:
                await self.transport.close()
            except Exception:  # noqa: BLE001
                logger.exception("%s: transport close failed", PLUGIN_NAME)
            self.transport = None
        if self._bridge is not None:
            try:
                self._bridge.stop()
            except Exception:  # noqa: BLE001
                logger.exception("%s: mqtt bridge stop failed", PLUGIN_NAME)
            self._bridge = None
        self.inbound_error = ""
        self.mqtt_error = ""

    def _warn_configuration(self) -> None:
        settings = self.settings
        if not settings.outbound_ready:
            hint = (
                "mqtt_host"
                if settings.outbound_transport == "mqtt"
                else "ha_url / ha_token"
            )
            logger.warning("%s: %s 未配置，/ha 指令暂不可用", PLUGIN_NAME, hint)
        if not settings.notify_enabled:
            logger.warning(
                "%s: 未配置告警目标，HA 推来的告警将被丢弃", PLUGIN_NAME
            )

    def _setup_outbound(self) -> None:
        settings = self.settings
        if settings.outbound_transport == "mqtt":
            bridge = self._ensure_bridge()
            if bridge is not None:
                self.transport = MqttTransport(bridge, settings.mqtt_command_topic)
            return
        if settings.ha_url and settings.ha_token:
            self.transport = RestTransport(
                settings.ha_url,
                settings.ha_token,
                event_type=settings.event_type,
                verify_ssl=settings.verify_ssl,
            )

    async def _setup_inbound(self) -> None:
        mode = self.settings.inbound_transport
        if mode == "off":
            logger.info("%s: 告警回传已关闭", PLUGIN_NAME)
            return

        if mode == "mqtt":
            if self._ensure_bridge() is None:
                self.inbound_error = self.mqtt_error or "MQTT 未就绪"
                logger.error("%s: %s", PLUGIN_NAME, self.inbound_error)
            else:
                logger.info(
                    "%s: MQTT 告警订阅已启动 %s",
                    PLUGIN_NAME,
                    self.settings.mqtt_notify_topic,
                )
            return

        server = InboundServer(
            deliver=self._deliver,
            token=self.inbound_token,
            port=self.settings.inbound_port,
        )
        try:
            port = await server.start()
        except OSError as exc:
            self.inbound_error = f"端口 {self.settings.inbound_port} 绑定失败：{exc}"
            logger.error("%s: %s", PLUGIN_NAME, self.inbound_error)
            return

        self.inbound = server
        logger.info(
            "%s: 告警回传监听已启动 http://0.0.0.0:%s%s",
            PLUGIN_NAME,
            port,
            server.path,
        )

    def _ensure_bridge(self) -> MqttBridge | None:
        """按需创建唯一一条 MQTT 连接，出站和入站共用。"""
        if self._bridge is not None:
            return self._bridge
        settings = self.settings
        if not settings.mqtt_host:
            self.mqtt_error = "未配置 mqtt_host"
            return None

        on_notify = (
            self._handle_mqtt_notify if settings.inbound_transport == "mqtt" else None
        )
        bridge = MqttBridge(
            host=settings.mqtt_host,
            port=settings.mqtt_port,
            username=settings.mqtt_username,
            password=settings.mqtt_password,
            tls=settings.mqtt_tls,
            topic_command=settings.mqtt_command_topic,
            topic_notify=settings.mqtt_notify_topic,
            on_notify=on_notify,
        )
        try:
            bridge.start()
        except MqttError as exc:
            self.mqtt_error = str(exc)
            logger.error("%s: %s", PLUGIN_NAME, exc)
            return None
        self._bridge = bridge
        return bridge

    # -------------------------------------------------------------- 设置热更新

    async def save_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        """合并并落盘配置，再按新配置重建通道；返回更新后的快照。"""
        merged = merge_payload(self.config, payload)

        # 1.x 的单值 notify_umo 已被 notify_umos 取代，保存时清掉旧键，
        # 否则它会在下次读取时被当成目标"复活"。
        if "notify_umos" in merged and "notify_umo" in self.config:
            try:
                self.config.pop("notify_umo", None)
            except Exception:  # noqa: BLE001
                logger.debug("legacy notify_umo cleanup failed", exc_info=True)

        saver = getattr(self.config, "save_config", None)
        if callable(saver):
            # AstrBotConfig：更新内存字典并写入 data/config/<插件>_config.json
            saver(merged)
        elif hasattr(self.config, "update"):
            self.config.update(merged)
        else:  # pragma: no cover - 正常不会走到
            self.config = merged

        # 令牌不再由配置承载：迁移完成后清掉旧键，避免它继续留在配置文件里。
        # merged 也要清，否则 save_config(merged) 会把旧键再写回来。
        merged.pop("inbound_token", None)
        if "inbound_token" in self.config:
            try:
                self.config.pop("inbound_token", None)
            except Exception:  # noqa: BLE001
                logger.debug("legacy inbound_token cleanup failed", exc_info=True)

        self.settings = Settings.from_config(self.config)
        if not self.inbound_token:
            self.inbound_token, _ = self.secret_store.ensure_token()

        # 手填的 UMO 也进候选，下次就不用再抄。
        self.umo_history.remember_many(
            list(self.settings.notify_umos)
            + list(self.settings.allowed_umos)
            + list(self.settings.blocked_umos)
        )

        self.last_saved_at = datetime.now().astimezone().isoformat(timespec="seconds")
        await self._apply_runtime()
        logger.info("%s: 设置已保存，通道已按新配置重建", PLUGIN_NAME)
        return public_snapshot(self.config)

    async def check_channels(self) -> dict[str, Any]:
        """给 WebUI「测试连接」用的自检结果。"""
        settings = self.settings

        outbound: dict[str, Any] = {
            "mode": settings.outbound_transport,
            "ok": False,
            "detail": "",
        }
        if self.transport is None:
            if settings.outbound_transport == "mqtt":
                outbound["detail"] = self.mqtt_error or "未配置 mqtt_host"
            else:
                outbound["detail"] = "未配置 ha_url / ha_token"
        else:
            ok, detail = await self.transport.ping()
            outbound["ok"] = ok
            outbound["detail"] = detail

        mode = settings.inbound_transport
        inbound: dict[str, Any] = {"mode": mode, "ok": False, "detail": ""}
        if mode == "off":
            inbound["detail"] = "已关闭"
        elif mode == "mqtt":
            connected = self._bridge is not None and self._bridge.is_connected()
            inbound["ok"] = connected
            inbound["detail"] = (
                f"订阅 {settings.mqtt_notify_topic}"
                if connected
                else (self.mqtt_error or "MQTT 未连接")
            )
        elif self.inbound is not None and self.inbound.running:
            inbound["ok"] = True
            inbound["detail"] = f"监听 :{self.inbound.port}{self.inbound.path}"
        else:
            inbound["detail"] = self.inbound_error or "未启动"

        return {
            "outbound": outbound,
            "inbound": inbound,
            "notify_enabled": settings.notify_enabled,
            "notify_umos": list(settings.notify_umos),
        }

    # ------------------------------------------------------------------ 指令

    @filter.command("ha")
    async def ha_command(self, event: AstrMessageEvent, _arg: str = ""):
        """把 /ha 后面的文字原样发送到 Home Assistant。"""
        event.call_llm = False
        umo = str(getattr(event, "unified_msg_origin", "") or "")
        command = self._extract_command(event, _arg)

        # 权限先于一切：连帮助卡片也不给名单外的会话。
        allowed, reason = access_decision(
            umo, self.settings.allowed_umos, self.settings.blocked_umos
        )
        if not allowed:
            yield event.plain_result(self._deny_text(reason))
            return

        # 通过权限的会话都记进候选，设置页的下拉里才有它。
        self.umo_history.remember(umo, self._sender_label(event))

        if not command:
            yield event.plain_result(USAGE_HEAD)
            return

        if command.strip().lower() == "check":
            yield event.plain_result(await self._check_text(umo))
            return

        if len(command) > MAX_COMMAND_CHARS:
            yield event.plain_result(
                f"❌ 指令太长（{len(command)} 字），上限 {MAX_COMMAND_CHARS} 字。"
            )
            return

        if self.transport is None:
            if self.settings.outbound_transport == "mqtt":
                reason = self.mqtt_error or "未配置 mqtt_host"
                yield event.plain_result(f"❌ MQTT 未就绪：{reason}")
            else:
                yield event.plain_result(
                    "❌ 还没配置 Home Assistant：请先在插件配置里填写 "
                    "ha_url 与 ha_token。"
                )
            return

        # 记下这个会话，设置页的下拉候选里才有它。
        payload = self._build_payload(event, umo, command)
        try:
            await self.transport.send(payload)
        except (TransportError, MqttError) as exc:
            logger.warning("%s: send failed: %s", PLUGIN_NAME, exc)
            yield event.plain_result(f"❌ 发送失败：{exc}")
            return
        except Exception:  # noqa: BLE001 - 兜底，避免一条指令把插件打挂
            logger.exception("%s: unexpected error while sending", PLUGIN_NAME)
            yield event.plain_result("❌ 发送失败：插件内部错误，请查看 AstrBot 日志。")
            return

        # 成功提示可关；失败提示永远发。
        if self.settings.reply_on_success:
            yield event.plain_result(
                f"✅ 已发送 → {self.transport.label}\n「{command}」"
            )

    # ------------------------------------------------------------------ 入站

    async def _deliver(self, title: str, message: str) -> DeliveryReport:
        """把 HA 推来的文字发到配置的会话（可能多个）。"""
        targets = self.settings.notify_umos
        if not targets:
            return DeliveryReport(False, "notify_umo_not_configured")

        text = f"【{title}】\n{message}" if title else message
        try:
            chain = MessageChain().message(text)
        except Exception:  # noqa: BLE001 - 兼容个别版本的 MessageChain 构造差异
            logger.exception("%s: build message chain failed", PLUGIN_NAME)
            return DeliveryReport(False, "deliver_failed", 0, len(targets))

        delivered = 0
        failures: list[str] = []
        raised = False
        invalid = False

        for target in targets:
            try:
                ok = bool(await self.context.send_message(target, chain))
            except ValueError:
                # UMO 字符串不合法，通常是手抄时少了平台前缀。
                invalid = True
                failures.append(f"{target}: UMO 不合法")
                logger.warning("%s: 告警目标格式不合法 %r", PLUGIN_NAME, target)
                continue
            except Exception as exc:  # noqa: BLE001
                # 平台适配器内部失败（掉线、风控、窗口过期……），原文最有诊断价值。
                raised = True
                failures.append(f"{target}: {type(exc).__name__}: {exc}")
                logger.exception("%s: 向 %s 发送失败", PLUGIN_NAME, target)
                continue

            if ok:
                delivered += 1
                self.umo_history.remember(target)
            else:
                failures.append(f"{target}: 平台未确认送达")
                logger.warning("%s: 告警目标 %s 未送达到", PLUGIN_NAME, target)

        if delivered == 0:
            if raised:
                error = "send_failed"
            elif invalid:
                error = "invalid_umo"
            else:
                error = "platform_not_found"
            if failures:
                self.logs.record("WARNING", "告警投递失败：" + "；".join(failures)[:500])
            return DeliveryReport(False, error, 0, len(targets), tuple(failures))

        if delivered < len(targets):
            logger.warning(
                "%s: 告警部分送达 %s/%s", PLUGIN_NAME, delivered, len(targets)
            )
        return DeliveryReport(True, None, delivered, len(targets))

    async def _handle_mqtt_notify(self, title: str, message: str) -> None:
        """MQTT 入站：没有 HTTP 状态码，失败只写日志。"""
        report = await self._deliver(title, message)
        if not report.ok:
            reason = report.failures[0] if report.failures else "未知原因"
            logger.warning(
                "%s: MQTT 告警未投递（%s）：%s | %s",
                PLUGIN_NAME,
                report.error,
                message[:120],
                reason,
            )
        elif report.delivered < report.total:
            logger.warning(
                "%s: MQTT 告警部分送达 %s/%s",
                PLUGIN_NAME,
                report.delivered,
                report.total,
            )

    # ------------------------------------------------------------------ 工具

    def _extract_command(self, event: AstrMessageEvent, fallback: str) -> str:
        """取出 ``/ha`` 之后的部分，保留中间空格，不做任何解析。"""
        raw = ""
        getter = getattr(event, "get_message_str", None)
        try:
            raw = str(getter() if callable(getter) else "") or ""
        except Exception:  # noqa: BLE001
            raw = str(getattr(event, "message_str", "") or "")
        if not raw:
            raw = str(getattr(event, "message_str", "") or "")
        raw = raw.strip()

        match = _COMMAND_RE.match(raw)
        if match is not None:
            return raw[match.end() :].strip()
        # AstrBot 已经把指令前缀剥掉了，此时整段文本就是命令内容。
        if raw:
            return raw
        return str(fallback or "").strip()

    @staticmethod
    def _sender_label(event: AstrMessageEvent) -> str:
        getter = getattr(event, "get_sender_name", None)
        try:
            return str(getter()).strip() if callable(getter) else ""
        except Exception:  # noqa: BLE001
            return ""

    def _build_payload(
        self, event: AstrMessageEvent, umo: str, command: str
    ) -> dict[str, Any]:
        def safe(call: Any, default: str = "") -> str:
            try:
                value = call() if callable(call) else call
            except Exception:  # noqa: BLE001
                return default
            return str(value) if value is not None else default

        return {
            "command": command,
            "raw": safe(getattr(event, "message_str", "")),
            "sender": safe(getattr(event, "get_sender_name", None)),
            "sender_id": safe(getattr(event, "get_sender_id", None)),
            "session": umo,
            "platform": safe(getattr(event, "get_platform_name", None)),
            "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
        }

    @staticmethod
    def _deny_text(reason: str) -> str:
        if reason == "blocked":
            return "❌ 无权限：本会话已被禁止使用 HA 指令。"
        if reason == "not_whitelisted":
            return "❌ 无权限：本会话不在 HA 指令白名单中。"
        return "❌ 无权限：当前会话无法使用 HA 指令。"

    async def _check_text(self, umo: str) -> str:
        """``/ha check``：连通性、SID、告警目标；不含令牌明文。"""
        settings = self.settings
        lines = [
            "📮 HA 指令助手 · 状态",
            "",
            f"会话 ID（SID）：{umo or '（未知）'}",
            "",
        ]
        lines.append(await self._outbound_line())
        lines.append(self._inbound_line())

        if settings.notify_enabled:
            targets = list(settings.notify_umos)
            shown = "、".join(targets[:3])
            if len(targets) > 3:
                shown += f" 等 {len(targets)} 个"
            lines.append(f"告警目标：{shown}")
        else:
            lines.append("告警目标：未配置（HA 推送将被丢弃）")

        status = self.token_status()
        lines.append(
            "回传令牌：" + ("已生成" if status["set"] else "未生成（在插件页生成）")
        )
        return "\n".join(lines)

    def token_status(self) -> dict[str, Any]:
        """给 WebUI 展示的令牌状态，只含指纹，不含明文。"""
        token = self.inbound_token or ""
        if not token:
            return {"set": False, "hint": ""}
        hint = token if len(token) <= 10 else f"{token[:4]}…{token[-4:]}"
        return {"set": True, "hint": hint}

    async def rotate_inbound_token(self) -> str:
        """生成新令牌并落盘；明文只返回给调用方一次。"""
        token = generate_token()
        self.secret_store.save(token)
        self.inbound_token = token
        if self.inbound is not None:
            # 监听器持有的是构造时的令牌，这里原地换掉，不必重启端口。
            self.inbound.set_token(token)
        self.logs.record("INFO", "回传令牌已重新生成（明文仅返回一次）")
        return token

    async def _outbound_line(self) -> str:
        settings = self.settings
        if settings.outbound_transport == "mqtt":
            if self.transport is None:
                reason = self.mqtt_error or "未配置 mqtt_host"
                return f"出站：MQTT ✗ {reason}"
            reachable, detail = await self.transport.ping()
            mark = "✓" if reachable else "✗"
            return (
                f"出站：MQTT → {settings.mqtt_host}:{settings.mqtt_port} "
                f"{mark} {detail}（主题 {settings.mqtt_command_topic}）"
            )

        if self.transport is None:
            return "出站：HA 事件 未配置（请填写 ha_url 与 ha_token）"
        reachable, detail = await self.transport.ping()
        mark = "✓" if reachable else "✗"
        return (
            f"出站：HA 事件 {settings.event_type} → {settings.ha_url} "
            f"{mark} {detail}"
        )

    def _inbound_line(self) -> str:
        settings = self.settings
        if settings.inbound_transport == "off":
            return "入站：已关闭"
        if settings.inbound_transport == "mqtt":
            connected = self._bridge is not None and self._bridge.is_connected()
            mark = "✓" if connected else "✗"
            note = "" if connected else f" {self.mqtt_error or 'MQTT 未连接'}"
            return f"入站：MQTT 订阅 {settings.mqtt_notify_topic} {mark}{note}"
        if self.inbound is not None and self.inbound.running:
            return f"入站：HTTP :{self.inbound.port} ✓"
        return f"入站：HTTP ✗ {self.inbound_error or '未启动'}"

    def _token_hint(self, event: AstrMessageEvent) -> str:
        if not self.inbound_token:
            return "未生成"
        is_admin = False
        try:
            checker = getattr(event, "is_admin", None)
            is_admin = bool(checker()) if callable(checker) else False
        except Exception:  # noqa: BLE001
            is_admin = False
        if is_admin:
            return f"{self.inbound_token}（请勿外泄）"
        return "已配置（管理员在私聊查看完整值）"
