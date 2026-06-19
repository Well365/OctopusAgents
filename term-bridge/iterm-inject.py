#!/usr/bin/env python3
"""Inject text into a chosen iTerm2 window / tab / session."""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "term-bridge"))
from iterm_target import (  # noqa: E402
    ItermTarget,
    applescript_session_block,
    applescript_session_close,
    apply_target_env,
    resolve_target,
)

APPLESCRIPT = (
    r"""
on run
    set msgPath to system attribute "ITERM_INJECT_FILE"
    set theText to read POSIX file msgPath as «class utf8»
    if theText ends with (return) then
        set theText to text 1 thru -2 of theText
    end if
    set submitEnter to system attribute "ITERM_INJECT_SUBMIT"
"""
    + applescript_session_block()
    + r"""
                    if submitEnter is "0" then
                        write text theText without newline
                    else
                        write text theText
                    end if
"""
    + applescript_session_close(extra_close=0)
    + r"""
    tell application "iTerm" to activate
end run
"""
)


def _key_applescript(key: str) -> str:
    """AppleScript that sends a single key to the target iTerm session.

    enter → write an empty line (iTerm appends newline = Return).
    esc   → write the ESC byte without a trailing newline.
    """
    if key == "enter":
        action = '                    write text ""'
    elif key == "esc":
        action = "                    write text (character id 27) without newline"
    else:
        raise ValueError(f"unknown key: {key!r}")
    return (
        "on run\n"
        + applescript_session_block()
        + "\n" + action + "\n"
        + applescript_session_close(extra_close=0)
        + '\n    tell application "iTerm" to activate\nend run\n'
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
    target: ItermTarget | None = None,
) -> tuple[int, str]:
    if sys.platform != "darwin":
        return 1, "iTerm inject requires macOS"
    text = text.strip("\n")
    if not text:
        return 1, "empty text"

    _load_env()
    t = target or resolve_target()

    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", delete=False, suffix=".txt") as f:
        f.write(text)
        path = f.name

    try:
        env = apply_target_env(t)
        env["ITERM_INJECT_FILE"] = path
        env["ITERM_INJECT_SUBMIT"] = "0" if not submit_enter else "1"
        r = subprocess.run(
            ["osascript", "-e", APPLESCRIPT],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            stdin=subprocess.DEVNULL,
        )
        out = ((r.stdout or "") + (r.stderr or "")).strip()
        return r.returncode, out or ("ok" if r.returncode == 0 else "osascript failed")
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def inject_key(key: str, *, target: ItermTarget | None = None) -> tuple[int, str]:
    """Execute _key_applescript via osascript; returns (returncode, message)."""
    if sys.platform != "darwin":
        return 1, "iTerm inject requires macOS"
    _load_env()
    t = target or resolve_target()
    env = apply_target_env(t)
    r = subprocess.run(
        ["osascript", "-e", _key_applescript(key)],
        env=env, capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL,
    )
    out = ((r.stdout or "") + (r.stderr or "")).strip()
    return r.returncode, out or ("ok" if r.returncode == 0 else "osascript failed")


def main() -> int:
    parser = argparse.ArgumentParser(description="Type text into iTerm2 window/tab")
    parser.add_argument("text", nargs="?", help="Text to inject (or stdin)")
    parser.add_argument("--window", type=int, help="Window index (1-based), omit with --front-window")
    parser.add_argument("--front-window", action="store_true", help="Use frontmost iTerm window")
    parser.add_argument("--tab", type=int, help="Tab index (1-based)")
    parser.add_argument("--session", type=int, help="Split pane index (1-based); default active pane")
    parser.add_argument("--no-enter", action="store_true", help="Type without pressing Enter")
    parser.add_argument("--key", choices=("enter", "esc"), help="Press a single key instead of typing text")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    win = None if args.front_window else args.window
    target = resolve_target(window=win, tab=args.tab, session=args.session)

    if args.key:
        if args.dry_run:
            print(f"would press {args.key} to iTerm ({target.label()})")
            return 0
        code, out = inject_key(args.key, target=target)
        if code != 0:
            print(out, file=sys.stderr)
            return code
        print(f"{out} [{target.label()}]")
        return 0

    text = args.text if args.text is not None else sys.stdin.read()
    if args.dry_run:
        print(f"would inject {len(text)} chars to iTerm ({target.label()})")
        return 0

    code, out = inject(text, submit_enter=not args.no_enter, target=target)
    if code != 0:
        print(out, file=sys.stderr)
        return code
    print(f"{out} [{target.label()}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
