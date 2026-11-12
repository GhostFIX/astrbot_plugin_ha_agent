"""文本归一化小工具。

告警正文经常是多行的，换行符来源却不统一：Windows 客户端发 ``\\r\\n``，
HA 模板可能拼出裸 ``\\r``。直接透传会让聊天里出现多余回车，这里统一成 ``\\n``。
"""

from __future__ import annotations


def normalize_newlines(text: str) -> str:
    """把 ``\\r\\n`` 和裸 ``\\r`` 统一替换成 ``\\n``。"""
    return str(text).replace("\r\n", "\n").replace("\r", "\n")
