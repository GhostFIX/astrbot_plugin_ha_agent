"""换行符归一化。"""

from __future__ import annotations

from core.text import normalize_newlines


def test_crlf_and_cr_become_lf():
    assert normalize_newlines("a\r\nb\rc\nd") == "a\nb\nc\nd"


def test_plain_lf_is_untouched():
    assert normalize_newlines("第一行\n第二行\n\n第四行") == "第一行\n第二行\n\n第四行"


def test_empty_text():
    assert normalize_newlines("") == ""
