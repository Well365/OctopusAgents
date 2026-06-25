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
    ("tab", "选择转发目标 tab"),
    ("status", "各终端 agent 状态"),
    ("shot", "截图：设备 / Mac 屏幕 / 终端"),
    ("format", "设置回传格式"),
    ("devices", "列出设备"),
    ("unlock", "解锁 Android（数字 PIN）"),
    ("lockmac", "隐私遮罩：盖屏防窥，不锁机（on/off）"),
    ("check", "环境检查"),
    ("stop", "关闭弹窗 / 取消输入 (Esc)"),
    ("interrupt", "停止运行 (Ctrl-C)"),
    ("approve", "审批模式开关（on/off）"),
    ("reset", "重置当前会话"),
    ("compact", "压缩会话上下文"),
    ("model", "查看或切换模型"),
    ("think", "设置思考强度"),
    ("p", "快捷提示库（/p 名字 注入）"),
    ("diff", "查看 git 改动（可带路径）"),
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
    "/shot": [
        ("android", "shot:android"),
        ("ios", "shot:ios"),
        ("mac屏幕", "shot:mac"),
        ("终端", "shot:term"),
    ],
    "/model": [
        ("opus", "model:opus"),
        ("sonnet", "model:sonnet"),
        ("haiku", "model:haiku"),
        ("fable", "model:fable"),
    ],
    "/think": [
        ("low", "think:low"),
        ("medium", "think:medium"),
        ("high", "think:high"),
        ("xhigh", "think:xhigh"),
        ("max", "think:max"),
        ("auto", "think:auto"),
    ],
    "/lockmac": [("on 开遮罩", "lockmac:on"), ("off 解除", "lockmac:off")],
}

# callback action prefix → slash command base
_ACTION_TO_CMD: dict[str, str] = {
    "new": "/new",
    "fmt": "/format",
    "shot": "/shot",
    "model": "/model",
    "think": "/think",
    "tab": "/tab",
    "sel": "/sel",  # interactive-prompt option button → /sel <window>:<tab>:<n>
    "lockmac": "/lockmac",
}


def menu_for_command(text: str) -> list[tuple[str, str]] | None:
    """Buttons for a bare parent command (no args), else None."""
    parts = text.strip().split()
    if len(parts) != 1:
        return None
    cmd = parts[0].lower().split("@")[0]
    return SUBMENUS.get(cmd)


def tab_submenu(tabs: list[tuple[int, int, str]]) -> list[tuple[str, str]]:
    """Inline buttons for /tab from live tabs: (window, tab, name) → (label, callback)."""
    rows: list[tuple[str, str]] = []
    for window, tab, name in tabs:
        short = name.replace("\n", " ")[:32]
        rows.append((f"w{window}/t{tab} · {short}", f"tab:{window}:{tab}"))
    return rows


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
