"""告警回传令牌的生成与持久化。

令牌属于敏感信息，放在 ``data/plugin_data/<插件名>/`` 下，不写回插件配置，
避免随插件目录更新被覆盖，也避免在 WebUI 里被误截图。
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path

TOKEN_BYTES = 24


def generate_token() -> str:
    """生成一个 URL 安全的令牌（约 32 个字符）。"""
    return secrets.token_urlsafe(TOKEN_BYTES)


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
