"""Tests for tg_new_command — pure /new parse + validate + reply formatting."""
from __future__ import annotations

import tg_new_command as nc
from tg_new_command import SpawnResult


def _ok(key, prompt):
    return SpawnResult(code=0, tab=3, workdir="/Users/x/fullStar/2026-06-19-2230", raw="tab=3")


def _fail(key, prompt):
    return SpawnResult(code=1, tab=None, workdir="", raw="osascript boom")


def test_parse_bare_returns_none_key():
    assert nc.parse_new([]) == (None, "")


def test_parse_agent_and_prompt():
    assert nc.parse_new(["Claude", "fix", "it"]) == ("claude", "fix it")


def test_bare_new_gives_usage_no_tab():
    from agent_cli import valid_keys
    reply, tab = nc.handle_new([], is_macos=True, spawn=_ok)
    assert tab is None
    assert "claude" in reply and "codex" in reply
    assert ", ".join(valid_keys()) in reply


def test_unknown_agent_lists_valid():
    reply, tab = nc.handle_new(["kitty"], is_macos=True, spawn=_ok)
    assert tab is None
    assert "kitty" in reply
    assert "claude" in reply


def test_non_macos_message():
    reply, tab = nc.handle_new(["claude"], is_macos=False, spawn=_ok)
    assert tab is None
    assert "macOS" in reply
    assert tab is None


def test_success_returns_tab_and_mentions_dir():
    reply, tab = nc.handle_new(["claude", "fix bug"], is_macos=True, spawn=_ok)
    assert tab == 3
    assert "claude" in reply
    assert "2026-06-19-2230" in reply
    assert "fix bug" in reply


def test_failure_reports_error_no_tab():
    reply, tab = nc.handle_new(["claude"], is_macos=True, spawn=_fail)
    assert tab is None
    assert "boom" in reply
