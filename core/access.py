"""会话黑白名单判定。

规则与同作者的 AstrBot 插件 Council 保持一致，方便两处配置互通：

1. 黑名单命中一律拒绝，优先级最高；
2. 白名单非空时，只有名单内的会话可用；
3. 白名单为空表示不限制。
"""

from __future__ import annotations

from collections.abc import Iterable


def normalize_umo_list(value: object) -> list[str]:
    """把配置里的会话列表归一化成去重的字符串列表。

    兼容三种写法：正常的 list、逗号/换行分隔的字符串、None。
    """
    if value is None:
        return []
    if isinstance(value, str):
        raw: list[object] = value.replace("\r", ",").replace("\n", ",").split(",")
    elif isinstance(value, Iterable):
        raw = list(value)
    else:
        raw = [value]

    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if item is None:
            continue
        text = str(item).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def access_decision(
    umo: str,
    allowed_umos: object,
    blocked_umos: object,
) -> tuple[bool, str]:
    """返回 (是否放行, 判定原因)。

    原因取值：``missing_umo`` / ``blocked`` / ``not_whitelisted`` / ``allowed``。
    """
    target = str(umo or "").strip()
    if not target:
        return False, "missing_umo"

    allowed = normalize_umo_list(allowed_umos)
    blocked = normalize_umo_list(blocked_umos)

    if target in blocked:
        return False, "blocked"
    if allowed and target not in allowed:
        return False, "not_whitelisted"
    return True, "allowed"
