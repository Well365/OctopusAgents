#!/usr/bin/env python3
"""Inject text into a Terminal.app window/tab via clipboard + Cmd-V (no silent API).

Needs Accessibility permission for the host process (to send keystrokes). The
target window is brought to front, text is pasted, then the prior frontmost app
and clipboard are restored. See terminal_inject_lib for the AppleScript.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "term-bridge"))
from iterm_target import ItermTarget, resolve_target  # noqa: E402
from terminal_inject_lib import build_inject_script, build_key_script  # noqa: E402

_ACCESS_HINT = (
    "\n\n需要授权宿主进程（Terminal / Claude Code / Python）："
    "系统设置 → 隐私与安全性 → 辅助功能（发送按键），"
    "以及 自动化 中允许控制 “System Events” 与 “Terminal”。"
)


def _needs_access_hint(out: str) -> bool:
    low = out.lower()
    return (
        "-25211" in out  # accessibility / assistive access
        or "-1743" in out  # not authorized to send Apple events (Automation)
        or "assistive access" in low
        or "辅助" in out
        or "授权" in out
    )


def _load_env() -> None:
    env_path = ROOT / ".env"
    if os.environ.get("TGKIT_ENV_FILE"):
        env_path = Path(os.environ["TGKIT_ENV_FILE"])
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip("'\""))


def inject(
    text: str,
    *,
    submit_enter: bool = True,
    enter_twice: bool = False,
    clear_line: bool = False,
    target: ItermTarget | None = None,
) -> tuple[int, str]:
    if sys.platform != "darwin":
        return 1, "Terminal inject requires macOS"
    text = text.strip("\n")
    if not text:
        return 1, "empty text"

    _load_env()
    t = target or resolve_target()
    script = build_inject_script(
        window=t.window, tab=t.tab, submit_enter=submit_enter,
        enter_twice=enter_twice, clear_line=clear_line,
    )

    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", delete=False, suffix=".txt") as f:
        f.write(text)
        path = f.name

    try:
        env = {**os.environ, "TERM_INJECT_FILE": path}
        r = subprocess.run(
            ["osascript", "-e", script],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            stdin=subprocess.DEVNULL,
        )
        out = ((r.stdout or "") + (r.stderr or "")).strip()
        if r.returncode != 0:
            if _needs_access_hint(out):
                out += _ACCESS_HINT
            return r.returncode, out or "osascript failed"
        return 0, out or "ok"
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def inject_key(key: str, *, target: ItermTarget | None = None) -> tuple[int, str]:
    if sys.platform != "darwin":
        return 1, "Terminal inject requires macOS"
    _load_env()
    t = target or resolve_target()
    script = build_key_script(window=t.window, tab=t.tab, key=key)
    r = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL,
    )
    out = ((r.stdout or "") + (r.stderr or "")).strip()
    if r.returncode != 0:
        if _needs_access_hint(out):
            out += _ACCESS_HINT
        return r.returncode, out or "osascript failed"
    return 0, out or "ok"


def main() -> int:
    parser = argparse.ArgumentParser(description="Paste text or press a key (--key) in a Terminal.app window/tab")
    parser.add_argument("text", nargs="?", help="Text to inject (or stdin)")
    parser.add_argument("--window", type=int, help="Window index (1-based); omit with --front-window")
    parser.add_argument("--front-window", action="store_true", help="Use frontmost Terminal window")
    parser.add_argument("--tab", type=int, help="Tab index (1-based)")
    parser.add_argument("--session", type=int, help="Ignored for Terminal (no split panes)")
    parser.add_argument("--no-enter", action="store_true", help="Paste without pressing Return")
    parser.add_argument("--enter-twice", action="store_true", help="Press Return twice (slash commands in TUIs)")
    parser.add_argument("--clear-line", action="store_true", help="Ctrl-U before paste (wipe leftover input)")
    parser.add_argument("--key", choices=("enter", "esc"), help="Press a single key instead of typing text")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    win = None if args.front_window else args.window
    target = resolve_target(window=win, tab=args.tab, session=None)

    if args.key:
        if args.dry_run:
            print(f"would press {args.key} to Terminal ({target.label()})")
            return 0
        code, out = inject_key(args.key, target=target)
        if code != 0:
            print(out, file=sys.stderr)
            return code
        print(f"{out} [{target.label()}]")
        return 0

    text = args.text if args.text is not None else sys.stdin.read()
    if args.dry_run:
        print(f"would inject {len(text)} chars to Terminal ({target.label()})")
        return 0

    code, out = inject(
        text, submit_enter=not args.no_enter, enter_twice=args.enter_twice,
        clear_line=args.clear_line, target=target,
    )
    if code != 0:
        print(out, file=sys.stderr)
        return code
    print(f"{out} [{target.label()}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
