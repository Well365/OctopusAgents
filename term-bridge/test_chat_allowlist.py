"""Tests for chat_allowlist — restrict which Telegram chats may drive the bot."""
from __future__ import annotations

import chat_allowlist as al


def test_parse_ids_basic():
    assert al.parse_ids("1,2,3") == frozenset({1, 2, 3})


def test_parse_ids_whitespace_and_semicolons():
    assert al.parse_ids(" 10 ; 20 , 30 ") == frozenset({10, 20, 30})


def test_parse_ids_negative_group_ids():
    assert al.parse_ids("-100123,456") == frozenset({-100123, 456})


def test_parse_ids_empty_and_invalid_skipped():
    assert al.parse_ids("") == frozenset()
    assert al.parse_ids("abc, , 7, x9") == frozenset({7})


def test_resolve_allowlist_explicit_wins():
    assert al.resolve_allowlist("11,22", "999") == frozenset({11, 22})


def test_resolve_allowlist_falls_back_to_owner():
    assert al.resolve_allowlist("", "999") == frozenset({999})


def test_resolve_allowlist_both_empty():
    assert al.resolve_allowlist("", "") == frozenset()


def test_is_allowed_empty_list_allows_all():
    assert al.is_allowed(12345, frozenset()) is True
    assert al.is_allowed(None, frozenset()) is True


def test_is_allowed_membership():
    allowed = frozenset({1, 2})
    assert al.is_allowed(1, allowed) is True
    assert al.is_allowed(3, allowed) is False
    assert al.is_allowed(None, allowed) is False


def test_is_allowed_zero_chat_id():
    # 0 is not a real Telegram id, but the falsy value must not bypass the gate
    assert al.is_allowed(0, frozenset({1})) is False
    assert al.is_allowed(0, frozenset({0})) is True
