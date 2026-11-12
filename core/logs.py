"""插件日志的内存缓冲，供 WebUI 日志页读取。

AstrBot 的 ``logger`` 是标准库 ``logging.Logger``，所以直接挂一个
:class:`logging.Handler` 最省事；不同版本可能换过 logger 对象，因此这里
按「能挂就挂」的方式依次尝试 ``astrbot.api.logger``、``astrbot`` 日志器
与 root，并用记录上的标记位去重（同一条记录可能经过多个日志器）。

只有与本插件相关的记录会被收进来：日志器名、源文件路径或消息文本里出现
插件目录名即视为本插件。
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from datetime import datetime
from typing import Any, Iterable

from .constants import PLUGIN_NAME

LEVEL_ORDER = {
    "DEBUG": 10,
    "INFO": 20,
    "WARNING": 30,
    "ERROR": 40,
    "CRITICAL": 50,
}
DEFAULT_CAPACITY = 400
MAX_MESSAGE_CHARS = 4000
_CAPTURED_FLAG = "_ha_agent_captured"


def level_value(name: str | None) -> int:
    return LEVEL_ORDER.get(str(name or "").upper(), 0)


def matches_plugin(record: logging.LogRecord) -> bool:
    """判断一条日志是否来自本插件。"""
    if PLUGIN_NAME in (record.name or ""):
        return True
    if PLUGIN_NAME in (record.pathname or ""):
        return True
    try:
        return PLUGIN_NAME in record.getMessage()
    except Exception:  # noqa: BLE001 - 格式化失败时按不相关处理
        return False


class LogBuffer(logging.Handler):
    """定长环形缓冲，线程安全。"""

    def __init__(self, capacity: int = DEFAULT_CAPACITY) -> None:
        super().__init__(level=logging.DEBUG)
        self.capacity = max(50, int(capacity))
        self._items: deque[dict[str, Any]] = deque(maxlen=self.capacity)
        self._lock = threading.Lock()
        self._loggers: list[Any] = []
        self.setFormatter(logging.Formatter("%(message)s"))

    # ---------------------------------------------------------------- 捕获

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if getattr(record, _CAPTURED_FLAG, False):
                return
            setattr(record, _CAPTURED_FLAG, True)
            if not matches_plugin(record):
                return

            message = self.format(record)
            if record.exc_info and record.exc_info[1] is not None:
                exc = record.exc_info[1]
                message = f"{message} | {type(exc).__name__}: {exc}"

            entry = {
                "time": datetime.fromtimestamp(record.created)
                .astimezone()
                .isoformat(timespec="seconds"),
                "level": record.levelname,
                "logger": record.name,
                "message": message[:MAX_MESSAGE_CHARS],
            }
            with self._lock:
                self._items.append(entry)
        except Exception:  # noqa: BLE001 - 日志处理不能反过来炸主流程
            self.handleError(record)

    def record(self, level: str, message: str) -> None:
        """直接写一条（插件自身关键事件用，不经过 logging）。"""
        entry = {
            "time": datetime.now().astimezone().isoformat(timespec="seconds"),
            "level": str(level or "INFO").upper(),
            "logger": PLUGIN_NAME,
            "message": str(message)[:MAX_MESSAGE_CHARS],
        }
        with self._lock:
            self._items.append(entry)

    # ---------------------------------------------------------------- 挂载

    def attach(self, loggers: Iterable[Any]) -> list[str]:
        """给能挂的日志器挂上自己，返回成功挂载的日志器名。"""
        names: list[str] = []
        for candidate in loggers:
            if candidate is None or not hasattr(candidate, "addHandler"):
                continue
            if any(candidate is existing for existing in self._loggers):
                continue
            try:
                candidate.addHandler(self)
            except Exception:  # noqa: BLE001
                continue
            self._loggers.append(candidate)
            names.append(getattr(candidate, "name", "?") or "?")
        return names

    def detach(self) -> None:
        for candidate in self._loggers:
            try:
                candidate.removeHandler(self)
            except Exception:  # noqa: BLE001
                pass
        self._loggers.clear()

    @property
    def attached(self) -> list[str]:
        return [getattr(item, "name", "?") or "?" for item in self._loggers]

    # ---------------------------------------------------------------- 读取

    def snapshot(
        self, limit: int = 200, min_level: str | None = None
    ) -> list[dict[str, Any]]:
        threshold = level_value(min_level)
        with self._lock:
            items = list(self._items)
        selected = [
            item for item in items if level_value(item.get("level")) >= threshold
        ]
        if limit > 0:
            selected = selected[-limit:]
        return selected

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    def stats(self) -> dict[str, Any]:
        with self._lock:
            count = len(self._items)
        return {
            "capacity": self.capacity,
            "count": count,
            "attached": self.attached,
        }
