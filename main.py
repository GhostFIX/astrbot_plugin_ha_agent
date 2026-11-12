"""AstrBot ⇄ Home Assistant 文本通道。

这个插件不调用任何大模型，只做两件事：

1. ``/ha <文字>``：把 ``<文字>`` 原样作为 ``command`` 发到 HA 事件总线；
2. 监听 ``/ha/notify``：把 HA 推来的告警文字转发到配置里指定的会话。

设计取舍见 README：命令用 HA 事件总线而不是实体，因为事件按"发生"设计，
同一条命令重复发送每次都会送达。
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
)
from .core.inbound import InboundServer
from .core.store import SecretStore
from .core.transport import RestTransport, TransportError

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
)


class HaAgentPlugin(Star):
    """把 AstrBot 变成 Home Assistant 的文本输入输出端口。"""

    def __init__(self, context: Context, config: Any = None):
        super().__init__(context)
        self.config = config if config is not None else {}
        self.settings = Settings.from_config(self.config)
        self.inbound_error = ""

        data_root = Path(get_astrbot_data_path())
        self.data_dir = data_root / DATA_DIR_NAME / PLUGIN_NAME
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            logger.exception("ha-agent data directory could not be created")

        self.secret_store = SecretStore(self.data_dir / INBOUND_TOKEN_FILE)
        # 配置里填了就用配置值，留空则用自动生成的令牌。
        self.inbound_token = self.settings.inbound_token
        if not self.inbound_token:
            self.inbound_token, _ = self.secret_store.ensure_token()

        self.transport: RestTransport | None = None
        if self.settings.outbound_ready:
            self.transport = RestTransport(
                self.settings.ha_url,
                self.settings.ha_token,
                verify_ssl=self.settings.verify_ssl,
            )
        self.inbound: InboundServer | None = None

    # ---------------------------------------------------------------- 生命周期

    async def initialize(self) -> None:
        """插件加载完成后启动告警回传监听。"""
        if not self.settings.outbound_ready:
            logger.warning(
                "%s: ha_url 或 ha_token 未配置，/ha 指令暂不可用",
                PLUGIN_NAME,
            )
        if not self.settings.notify_enabled:
            logger.warning(
                "%s: notify_umo 未配置，HA 的告警推送将被拒绝（409）",
                PLUGIN_NAME,
            )
        if not self.settings.inbound_enabled:
            logger.info("%s: 告警回传监听已关闭", PLUGIN_NAME)
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

    async def terminate(self) -> None:
        """插件卸载/停用时释放端口与连接池。"""
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

    # ------------------------------------------------------------------ 指令

    @filter.command("ha")
    async def ha_command(self, event: AstrMessageEvent, _arg: str = ""):
        """把 /ha 后面的文字原样发送到 Home Assistant 事件总线。"""
        event.call_llm = False
        umo = str(getattr(event, "unified_msg_origin", "") or "")
        command = self._extract_command(event, _arg)

        if not command:
            yield event.plain_result(await self._help_text(event, umo))
            return

        allowed, reason = access_decision(
            umo, self.settings.allowed_umos, self.settings.blocked_umos
        )
        if not allowed:
            yield event.plain_result(self._deny_text(reason))
            return

        if len(command) > MAX_COMMAND_CHARS:
            yield event.plain_result(
                f"❌ 指令太长（{len(command)} 字），上限 {MAX_COMMAND_CHARS} 字。"
            )
            return

        if self.transport is None:
            yield event.plain_result(
                "❌ 还没配置 Home Assistant：请先在插件配置里填写 "
                "ha_url 与 ha_token。"
            )
            return

        payload = self._build_payload(event, umo, command)
        try:
            await self.transport.fire_event(self.settings.event_type, payload)
        except TransportError as exc:
            logger.warning("%s: fire_event failed: %s", PLUGIN_NAME, exc)
            yield event.plain_result(f"❌ 发送失败：{exc}")
            return
        except Exception:  # noqa: BLE001 - 兜底，避免一条指令把插件打挂
            logger.exception("%s: unexpected error while firing event", PLUGIN_NAME)
            yield event.plain_result("❌ 发送失败：插件内部错误，请查看 AstrBot 日志。")
            return

        yield event.plain_result(
            f"✅ 已发送 → HA · {self.settings.event_type}\n「{command}」"
        )

    # ------------------------------------------------------------------ 入站

    async def _deliver(self, title: str, message: str) -> tuple[bool, str | None]:
        """把 HA 推来的文字发到配置的会话。返回 (是否成功, 失败原因)。"""
        target = self.settings.notify_umo
        if not target:
            return False, "notify_umo_not_configured"

        text = f"【{title}】\n{message}" if title else message
        try:
            chain = MessageChain().message(text)
        except Exception:  # noqa: BLE001 - 兼容个别版本的 MessageChain 构造差异
            logger.exception("%s: build message chain failed", PLUGIN_NAME)
            return False, "deliver_failed"

        try:
            delivered = await self.context.send_message(target, chain)
        except ValueError:
            # UMO 字符串不合法，通常是手抄时少了平台前缀。
            logger.warning("%s: invalid notify_umo %r", PLUGIN_NAME, target)
            return False, "platform_not_found"
        except Exception:  # noqa: BLE001
            logger.exception("%s: send_message failed", PLUGIN_NAME)
            return False, "deliver_failed"

        if not delivered:
            logger.warning(
                "%s: 没有平台实例匹配 notify_umo=%s", PLUGIN_NAME, target
            )
            return False, "platform_not_found"
        return True, None

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
            return "本会话已被禁止使用 HA 指令。"
        if reason == "not_whitelisted":
            return "本会话不在 HA 指令白名单中。"
        return "当前会话无法使用 HA 指令。"

    async def _help_text(self, event: AstrMessageEvent, umo: str) -> str:
        settings = self.settings
        lines = [USAGE_HEAD.rstrip("\n"), "", "当前会话 UMO：", f"  {umo or '（未知）'}", ""]

        if self.transport is not None:
            reachable, detail = await self.transport.ping()
            mark = "✓" if reachable else "✗"
            lines.append(f"HA 地址：{settings.ha_url} {mark} {detail}")
        else:
            lines.append(
                "HA 地址：未配置（请在插件配置里填写 ha_url 与 ha_token）"
            )
        lines.append(f"事件类型：{settings.event_type}")

        if not settings.inbound_enabled:
            lines.append("告警回传：已关闭")
        elif self.inbound is not None and self.inbound.running:
            lines.append(f"告警回传：监听 :{self.inbound.port} ✓")
        else:
            reason = self.inbound_error or "未启动"
            lines.append(f"告警回传：✗ {reason}")

        if settings.notify_enabled:
            lines.append(f"告警目标：{settings.notify_umo}")
        else:
            lines.append("告警目标：未配置（HA 推送将被拒绝）")

        lines.append(f"回传令牌：{self._token_hint(event)}")
        return "\n".join(lines)

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
