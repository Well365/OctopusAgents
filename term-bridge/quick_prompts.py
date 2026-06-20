"""Saved quick prompts for Telegram `/p` — name → reusable prompt text.

Phone typing is slow; `/p test` re-sends a saved prompt (e.g. "run the tests and
fix failures") to the target terminal. Stored as a JSON dict in
inbox/quick-prompts.json. resolve_p_command is pure (no I/O, no telegram) so it
is unit-tested; the relay supplies the loaded store and save/delete callbacks.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parent.parent


def store_path() -> Path:
    return ROOT / "inbox" / "quick-prompts.json"


def load(path: Path | None = None) -> dict[str, str]:
    p = path or store_path()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): v for k, v in data.items() if isinstance(v, str)}


def _write(store: dict[str, str], path: Path | None = None) -> None:
    p = path or store_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(store, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)


def save_prompt(name: str, text: str, path: Path | None = None) -> dict[str, str]:
    store = load(path)
    store[name.strip()] = text.strip()
    _write(store, path)
    return store


def delete_prompt(name: str, path: Path | None = None) -> dict[str, str]:
    store = load(path)
    store.pop(name.strip(), None)
    _write(store, path)
    return store


def format_list(store: dict[str, str]) -> str:
    if not store:
        return "还没有保存的快捷提示。\n用法: /p save <名字> <提示文字>"
    lines = ["已保存的快捷提示（发 /p <名字> 注入目标终端）:"]
    for name, text in store.items():
        lines.append(f"• {name}: {text[:50]}")
    lines.append("管理: /p save <名字> <文字> | /p del <名字>")
    return "\n".join(lines)


def resolve_p_command(
    args: list[str],
    store: dict[str, str],
    save_fn: Callable[[str, str], object],
    delete_fn: Callable[[str], object],
) -> tuple[str, str | None]:
    """Resolve a /p subcommand.

    Returns (reply, inject_text). When inject_text is not None the caller should
    inject it into the target terminal (a saved prompt was requested).
    """
    if not args:
        return format_list(store), None
    sub = args[0].strip()
    if sub == "save":
        if len(args) < 3:
            return "用法: /p save <名字> <提示文字>", None
        name = args[1].strip()
        text = " ".join(args[2:]).strip()
        save_fn(name, text)
        return f"✓ 已保存快捷提示 {name}", None
    if sub == "del":
        if len(args) < 2:
            return "用法: /p del <名字>", None
        delete_fn(args[1].strip())
        return f"✓ 已删除快捷提示 {args[1].strip()}", None
    text = store.get(sub)
    if text is None:
        return f"没有快捷提示「{sub}」。发 /p 看列表", None
    return f"▶ {sub}", text
