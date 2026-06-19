"""Tests for iterm_shot — no-activate iTerm window capture helpers.

These cover the pure pieces: building the window-id AppleScript, parsing the
returned id, and assembling the screencapture command. The actual osascript /
screencapture calls are integration-only (macOS) and exercised manually.
"""
from __future__ import annotations

import pytest

from iterm_shot import (
    build_screencapture_cmd,
    build_window_id_script,
    parse_window_id,
)


# ── build_window_id_script ──

def test_script_targets_iterm_app():
    script = build_window_id_script(1)
    assert 'tell application "iTerm"' in script


def test_script_for_indexed_window():
    assert "window 2" in build_window_id_script(2)


def test_script_for_front_window_uses_current_window():
    script = build_window_id_script(None)
    assert "current window" in script
    assert "window 1" not in script


def test_script_defaults_to_iterm_app():
    assert 'tell application "iTerm"' in build_window_id_script(1)


def test_script_targets_terminal_app_when_requested():
    script = build_window_id_script(1, app="Terminal")
    assert 'tell application "Terminal"' in script
    assert "window 1" in script


def test_script_guards_not_running_to_avoid_autolaunch():
    assert "is not running" in build_window_id_script(1, app="Terminal")


# ── parse_window_id ──

def test_parse_plain_id():
    assert parse_window_id("337") == 337


def test_parse_strips_whitespace_and_newline():
    assert parse_window_id("  64 \n") == 64


@pytest.mark.parametrize("raw", ["", "   ", "abc", "0", "-5", "12x"])
def test_parse_rejects_invalid(raw):
    with pytest.raises(ValueError):
        parse_window_id(raw)


# ── build_screencapture_cmd ──

def test_capture_cmd_uses_window_id_flag():
    cmd = build_screencapture_cmd(337, "/tmp/a.png")
    assert cmd == ["screencapture", "-x", "-l", "337", "/tmp/a.png"]


def test_capture_cmd_no_shadow_adds_o_flag():
    cmd = build_screencapture_cmd(337, "/tmp/a.png", no_shadow=True)
    assert "-o" in cmd
    assert cmd[-1] == "/tmp/a.png"
    assert cmd[-2] == "337"
