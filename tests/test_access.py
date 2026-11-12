"""会话黑白名单的判定逻辑。"""

from __future__ import annotations

from core.access import access_decision, normalize_umo_list


def test_normalize_none_and_empty():
    assert normalize_umo_list(None) == []
    assert normalize_umo_list("") == []
    assert normalize_umo_list([]) == []


def test_normalize_string_forms():
    assert normalize_umo_list("a, b\n c") == ["a", "b", "c"]
    assert normalize_umo_list("a,,a") == ["a"]


def test_normalize_list_dedupes_and_trims():
    assert normalize_umo_list([" a ", "a", "", None, 3]) == ["a", "3"]


def test_empty_whitelist_allows_everyone():
    allowed, reason = access_decision("aiocqhttp:group:1", [], [])
    assert allowed is True
    assert reason == "allowed"


def test_blacklist_wins_even_if_whitelisted():
    umo = "aiocqhttp:group:1"
    allowed, reason = access_decision(umo, [umo], [umo])
    assert allowed is False
    assert reason == "blocked"


def test_not_in_whitelist_is_rejected():
    allowed, reason = access_decision("aiocqhttp:group:2", ["aiocqhttp:group:1"], [])
    assert allowed is False
    assert reason == "not_whitelisted"


def test_missing_umo_is_rejected():
    allowed, reason = access_decision("", [], [])
    assert allowed is False
    assert reason == "missing_umo"


def test_blacklist_only_blocks_listed_session():
    assert access_decision("a", [], ["b"])[0] is True
    assert access_decision("b", [], ["b"])[0] is False
