"""Pure logic for the `/new <agent> [prompt]` Telegram command.

Parses the command, validates the agent against the registry, delegates the
actual terminal spawn to an injected callable, and formats the reply. Keeping
this free of subprocess/platform calls makes it unit-testable; tg_relay_patches
wires it to the real spawn and applies the retarget.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from agent_cli import get_agent, valid_keys


@dataclass(frozen=True)
class SpawnResult:
    code: int
    tab: int | None
    workdir: str
    raw: str


def _usage() -> str:
    keys = "|".join(valid_keys())
    return (
        f"用法: /new {keys} [初始提示词]\n"
        f"可用 agent: {', '.join(valid_keys())}"
    )


def parse_new(args: list[str]) -> tuple[str | None, str]:
    if not args:
        return (None, "")
    return (args[0].strip().lower(), " ".join(args[1:]).strip())


def handle_new(
    args: list[str],
    *,
    is_macos: bool,
    spawn: Callable[[str, str], SpawnResult],
) -> tuple[str, int | None]:
    key, prompt = parse_new(args)
    if key is None:
        return (_usage(), None)
    if get_agent(key) is None:
        return (f"未知 agent: {key}\n可用: {', '.join(valid_keys())}", None)
    if not is_macos:
        return ("开新会话需要 macOS", None)

    res = spawn(key, prompt)
    if res.code != 0:
        return (f"spawn 失败:\n{res.raw[:800]}", None)

    preview = (prompt[:80] + ("…" if len(prompt) > 80 else "")) if prompt else "(无初始提示)"
    where = res.workdir or "~/fullStar/<ts>"
    tabnote = f"tab {res.tab}" if res.tab is not None else "新 tab"
    reply = (
        f"✓ 已启动 {key} @ {where} ({tabnote})\n"
        f"{preview}\n"
        "(后续消息将注入此会话)"
    )
    return (reply, res.tab)
