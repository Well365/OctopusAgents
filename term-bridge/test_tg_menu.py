"""Tests for tg_menu — command list, sub-menus, and callback dispatch."""
from __future__ import annotations

import tg_menu as m


def test_menu_commands_includes_core():
    cmds = [c for c, _ in m.MENU_COMMANDS]
    for expected in ("new", "tabs", "shot", "format", "devices", "check", "help"):
        assert expected in cmds


def test_submenus_for_parents():
    assert ("claude", "new:claude") in m.SUBMENUS["/new"]
    assert ("codex", "new:codex") in m.SUBMENUS["/new"]
    assert ("html", "fmt:html") in m.SUBMENUS["/format"]
    assert ("android", "shot:android") in m.SUBMENUS["/shot"]


def test_menu_for_command_bare_parent_returns_buttons():
    assert m.menu_for_command("/new") == m.SUBMENUS["/new"]
    assert m.menu_for_command("/format") == m.SUBMENUS["/format"]


def test_menu_for_command_with_args_returns_none():
    assert m.menu_for_command("/new claude fix bug") is None


def test_menu_for_command_non_submenu_returns_none():
    assert m.menu_for_command("/help") is None


def test_parse_callback():
    assert m.parse_callback("new:claude") == ("new", "claude")
    assert m.parse_callback("nocolon") == ("nocolon", "")


def test_callback_to_command():
    assert m.callback_to_command("new", "claude") == "/new claude"
    assert m.callback_to_command("fmt", "html") == "/format html"
    assert m.callback_to_command("shot", "android") == "/shot android"
    assert m.callback_to_command("bogus", "x") is None


def test_dispatch_callback_calls_handler():
    seen = {}
    def fake_handle(cmd):
        seen["cmd"] = cmd
        return "ok"
    assert m.dispatch_callback("new:claude", fake_handle) == "ok"
    assert seen["cmd"] == "/new claude"


def test_dispatch_callback_unknown():
    assert "未知操作" in m.dispatch_callback("bogus:x", lambda c: "ok")
