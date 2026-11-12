"""告警回传令牌的生成与持久化，以及已知会话（UMO）历史。

令牌属于敏感信息，放在 ``data/plugin_data/<插件名>/`` 下，不写回插件配置，
避免随插件目录更新被覆盖，也避免在 WebUI 里被误截图。

UMO 历史用于设置页的「下拉选择」：任何发过 ``/ha`` 的会话都会自动进入候选，
再加上手动填写，比让用户去别处抄 ID 现实得多。
"""

from __future__ import annotations

import json
import os
import secrets
from datetime import datetime
from pathlib import Path
from typing import Any

TOKEN_BYTES = 24
MAX_UMO_ENTRIES = 200


def generate_token() -> str:
    """生成一个 URL 安全的令牌（约 32 个字符）。"""
    return secrets.token_urlsafe(TOKEN_BYTES)


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class SecretStore:
    """读写单个令牌文件。"""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> str:
        try:
            text = self.path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError):
            return ""
        return text

    def save(self, token: str) -> None:
        """原子写入，尽量收紧权限，失败不抛异常。"""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(token + "\n", encoding="utf-8")
        os.replace(tmp, self.path)
        try:  # POSIX 上收紧到仅属主可读，Windows 上 os.chmod 语义有限。
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def ensure_token(self) -> tuple[str, bool]:
        """返回 (令牌, 是否本次新建)。已有可用令牌则原样复用。"""
        existing = self.load()
        if existing:
            return existing, False
        token = generate_token()
        try:
            self.save(token)
        except OSError:
            # 落盘失败也要能继续跑：本次进程内使用内存令牌。
            return token, True
        return token, True


class UmoHistory:
    """记录见过的会话，供设置页下拉选择。"""

    def __init__(self, path: Path, limit: int = MAX_UMO_ENTRIES) -> None:
        self.path = path
        self.limit = limit
        self._entries: list[dict[str, str]] | None = None

    def _load_file(self) -> list[dict[str, str]]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return []
        items = raw.get("umos") if isinstance(raw, dict) else None
        if not isinstance(items, list):
            return []

        entries: list[dict[str, str]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            umo = str(item.get("umo") or "").strip()
            if not umo:
                continue
            entries.append(
                {
                    "umo": umo,
                    "label": str(item.get("label") or "").strip(),
                    "last_seen": str(item.get("last_seen") or ""),
                }
            )
        return entries[: self.limit]

    def _save_file(self, entries: list[dict[str, str]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        payload = {"umos": entries[: self.limit]}
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(tmp, self.path)

    def entries(self) -> list[dict[str, str]]:
        if self._entries is None:
            self._entries = self._load_file()
        return list(self._entries)

    def remember(self, umo: str, label: str = "") -> None:
        """记下一条会话；已存在则更新标签与时间并提到最前。"""
        target = str(umo or "").strip()
        if not target:
            return
        current = self.entries()
        existing = next((item for item in current if item["umo"] == target), None)
        rest = [item for item in current if item["umo"] != target]
        merged = {
            "umo": target,
            "label": str(label or "").strip() or (existing or {}).get("label", ""),
            "last_seen": _now(),
        }
        self._entries = [merged, *rest][: self.limit]
        try:
            self._save_file(self._entries)
        except OSError:
            # 历史只是便利功能，写不进去不该影响主流程。
            pass

    def remember_many(self, umos: Any) -> None:
        for item in umos or []:
            self.remember(str(item))
