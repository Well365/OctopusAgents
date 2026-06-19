"""Tests for terminal_capture_lib — Terminal.app scrollback read builder."""
from __future__ import annotations

from terminal_capture_lib import build_read_script


def test_targets_terminal_app():
    assert 'tell application "Terminal"' in build_read_script(window=1, tab=1)


def test_reads_history_scrollback():
    # history = full scrollback (vs contents = only visible)
    assert "history" in build_read_script(window=1, tab=1)


def test_front_window_when_none():
    s = build_read_script(window=None, tab=1)
    assert "front window" in s


def test_indexed_window_and_tab():
    s = build_read_script(window=2, tab=3)
    assert "window 2" in s
    assert "tab 3" in s


def test_guards_no_window():
    assert "No Terminal window" in build_read_script(window=1, tab=1)


def test_guards_not_running_to_avoid_autolaunch():
    # `tell application "Terminal"` auto-launches a closed Terminal; guard first.
    assert "is not running" in build_read_script(window=1, tab=1)
