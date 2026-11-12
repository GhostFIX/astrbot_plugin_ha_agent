"""回传令牌的生成与持久化。"""

from __future__ import annotations

from pathlib import Path

from core.store import SecretStore, generate_token


def test_generate_token_is_urlsafe_and_long_enough():
    token = generate_token()
    assert len(token) >= 24
    assert "/" not in token and "+" not in token


def test_ensure_token_creates_file_once(tmp_path: Path):
    path = tmp_path / "inbound_token.txt"
    store = SecretStore(path)

    first, created = store.ensure_token()
    assert created is True
    assert path.read_text(encoding="utf-8").strip() == first

    second, created_again = store.ensure_token()
    assert created_again is False
    assert second == first


def test_tokens_differ_between_installs(tmp_path: Path):
    a, _ = SecretStore(tmp_path / "a.txt").ensure_token()
    b, _ = SecretStore(tmp_path / "b.txt").ensure_token()
    assert a != b


def test_empty_file_is_treated_as_missing(tmp_path: Path):
    path = tmp_path / "inbound_token.txt"
    path.write_text("\n", encoding="utf-8")
    store = SecretStore(path)
    assert store.load() == ""
    token, created = store.ensure_token()
    assert created is True
    assert store.load() == token
