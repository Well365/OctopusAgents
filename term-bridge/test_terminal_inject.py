"""Tests for terminal_inject_lib — Terminal.app clipboard+Cmd-V inject builder."""
from __future__ import annotations

from terminal_inject_lib import build_inject_script


def test_sets_clipboard_from_injected_file():
    s = build_inject_script(window=1, tab=1, submit_enter=True)
    assert "set the clipboard to" in s


def test_activates_terminal_and_pastes():
    s = build_inject_script(window=1, tab=1, submit_enter=True)
    assert 'tell application "Terminal"' in s
    assert 'keystroke "v" using command down' in s


def test_submit_presses_return():
    s = build_inject_script(window=1, tab=1, submit_enter=True)
    assert "keystroke return" in s


def test_no_submit_omits_return():
    s = build_inject_script(window=1, tab=1, submit_enter=False)
    assert "keystroke return" not in s


def test_restores_previous_frontmost_app():
    s = build_inject_script(window=1, tab=1, submit_enter=True)
    # captures the prior frontmost app and reactivates it afterwards
    assert "frontmost" in s
    assert s.count("activate") >= 2  # Terminal + restore


def test_front_window_uses_front_not_index():
    s = build_inject_script(window=None, tab=1, submit_enter=True)
    assert "front window" in s


def test_indexed_window_referenced():
    s = build_inject_script(window=2, tab=3, submit_enter=True)
    assert "window 2" in s
    assert "tab 3" in s


def test_guards_not_running_to_avoid_autolaunch():
    assert "is not running" in build_inject_script(window=1, tab=1, submit_enter=True)
