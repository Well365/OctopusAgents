"""Map session-control commands to inject actions for the active Claude Code tab.

Verified Claude Code semantics (2026-06-19): /stop = one Esc, /reset = /clear,
/compact = /compact, /model <alias>, /think <level> = /effort <level>. The relay
executes a `text` action by typing it (+Enter) and a `key` action via the
backend `--key` mode.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class InjectAction:
    kind: str     # "key" | "text"
    payload: str  # key name ("esc") or text to type ("/clear")


MODELS: tuple[str, ...] = ("opus", "sonnet", "haiku", "fable")
EFFORT_LEVELS: tuple[str, ...] = ("low", "medium", "high", "xhigh", "max", "auto")


def resolve_session_command(cmd: str, arg: str = "") -> InjectAction | None:
    cmd = cmd.strip().lower()
    arg = arg.strip().lower()
    if cmd == "/stop":
        return InjectAction(kind="key", payload="esc")
    if cmd == "/reset":
        return InjectAction(kind="text", payload="/clear")
    if cmd == "/compact":
        return InjectAction(kind="text", payload="/compact")
    if cmd == "/model":
        return InjectAction(kind="text", payload=f"/model {arg}") if arg in MODELS else None
    if cmd == "/think":
        return InjectAction(kind="text", payload=f"/effort {arg}") if arg in EFFORT_LEVELS else None
    return None


def session_usage(cmd: str) -> str:
    cmd = cmd.strip().lower()
    if cmd == "/model":
        return "用法: /model " + "|".join(MODELS) + "（或点按钮选择）"
    if cmd == "/think":
        return "用法: /think " + "|".join(EFFORT_LEVELS) + "（设置思考强度，等价 /effort）"
    return f"未知会话命令: {cmd}"
