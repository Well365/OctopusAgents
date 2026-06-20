"""Install / uninstall the Claude Code Stop+Notification → Telegram hook.

Idempotent merge (add_hook) and surgical removal (remove_hook) of the hook
entries that point at our script in ~/.claude/settings.json, plus the
CLAUDE_CODE_DISABLE_TERMINAL_TITLE env. The pure dict transforms are unit-tested;
the CLI applies them to a settings.json file atomically so `./mob install-skill`
can wire the notification in and `--uninstall` can cleanly remove it.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import sys
from pathlib import Path

HOOK_EVENTS = ("Stop", "Notification")
TITLE_ENV = "CLAUDE_CODE_DISABLE_TERMINAL_TITLE"


def _entry(script_path: str) -> dict:
    return {"hooks": [{"type": "command", "command": script_path, "timeout": 15}]}


def _has_command(arr: list, script_path: str) -> bool:
    return any(
        any(h.get("command") == script_path for h in e.get("hooks", []))
        for e in arr
    )


def add_hook(settings: dict, script_path: str) -> dict:
    """Idempotently register Stop+Notification hooks + the title env."""
    s = copy.deepcopy(settings)
    hooks = s.setdefault("hooks", {})
    for event in HOOK_EVENTS:
        arr = hooks.setdefault(event, [])
        if not _has_command(arr, script_path):
            arr.append(_entry(script_path))
    s.setdefault("env", {})[TITLE_ENV] = "1"
    return s


def remove_hook(settings: dict, script_path: str) -> dict:
    """Remove only the hook entries pointing at script_path, plus the title env."""
    s = copy.deepcopy(settings)
    hooks = s.get("hooks", {})
    for event in HOOK_EVENTS:
        arr = hooks.get(event)
        if not arr:
            continue
        new_arr = []
        for entry in arr:
            kept = [h for h in entry.get("hooks", []) if h.get("command") != script_path]
            if kept:
                e = dict(entry)
                e["hooks"] = kept
                new_arr.append(e)
        if new_arr:
            hooks[event] = new_arr
        else:
            hooks.pop(event, None)
    if not hooks:
        s.pop("hooks", None)
    env = s.get("env")
    if isinstance(env, dict) and TITLE_ENV in env:
        env.pop(TITLE_ENV, None)
        if not env:
            s.pop("env", None)
    return s


def _apply(action: str, settings_path: Path, script_path: str) -> None:
    settings = json.loads(settings_path.read_text(encoding="utf-8")) if settings_path.exists() else {}
    new = add_hook(settings, script_path) if action == "install" else remove_hook(settings, script_path)
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = settings_path.with_suffix(settings_path.suffix + ".tmp")
    tmp.write_text(json.dumps(new, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, settings_path)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Install/uninstall the Claude TG-notify hook")
    p.add_argument("action", choices=["install", "uninstall"])
    p.add_argument("--script", required=True, help="Absolute path to claude-tg-hook.sh")
    p.add_argument("--settings", default=os.path.expanduser("~/.claude/settings.json"))
    args = p.parse_args(argv)
    _apply(args.action, Path(args.settings), args.script)
    verb = "installed" if args.action == "install" else "removed"
    print(f"Claude TG-notify hook {verb} in {args.settings}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
