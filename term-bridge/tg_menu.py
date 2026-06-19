"""Telegram bot command menu + inline sub-command definitions and callback logic.

Pure single-source-of-truth for `setMyCommands`, the inline-keyboard sub-menus,
and the callback_data → slash-command mapping. The relay glue converts these to
telegram objects and calls handle_command.
"""
from __future__ import annotations

from typing import Callable

MENU_COMMANDS: list[tuple[str, str]] = [
    ("new", "新 tab 启动 agent 会话"),
    ("tabs", "列出终端标签"),
    ("shot", "截图当前屏幕"),
    ("format", "设置回传格式"),
    ("devices", "列出设备"),
    ("check", "环境检查"),
    ("help", "显示可用命令"),
]

SUBMENUS: dict[str, list[tuple[str, str]]] = {
    "/new": [("claude", "new:claude"), ("codex", "new:codex")],
    "/format": [
        ("html", "fmt:html"),
        ("markdown", "fmt:markdown"),
        ("plain", "fmt:plain"),
        ("screenshot", "fmt:screenshot"),
    ],
    "/shot": [("android", "shot:android"), ("ios", "shot:ios")],
}

# callback action prefix → slash command base
_ACTION_TO_CMD: dict[str, str] = {
    "new": "/new",
    "fmt": "/format",
    "shot": "/shot",
    "model": "/model",
    "think": "/think",
}


def menu_for_command(text: str) -> list[tuple[str, str]] | None:
    """Buttons for a bare parent command (no args), else None."""
    parts = text.strip().split()
    if len(parts) != 1:
        return None
    cmd = parts[0].lower().split("@")[0]
    return SUBMENUS.get(cmd)


def parse_callback(data: str) -> tuple[str, str]:
    action, _sep, value = data.partition(":")
    return (action, value)


def callback_to_command(action: str, value: str) -> str | None:
    base = _ACTION_TO_CMD.get(action)
    if base is None:
        return None
    return f"{base} {value}".strip() if value else base


def dispatch_callback(data: str, handle_command: Callable[[str], str]) -> str:
    action, value = parse_callback(data)
    cmd = callback_to_command(action, value)
    if cmd is None:
        return f"未知操作: {data}"
    return handle_command(cmd)
