#!/usr/bin/env python3
"""Read iTerm2 window / tab / session scrollback."""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "term-bridge"))
from iterm_log_buffer import combined_text, read_log, reset as reset_log  # noqa: E402
from iterm_target import (  # noqa: E402
    ItermTarget,
    applescript_session_block,
    applescript_session_close,
    apply_target_env,
    resolve_target,
)

READ_SCRIPT = (
    applescript_session_block()
    + "                    return contents\n"
    + applescript_session_close(extra_close=0)
    + "\n"
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


def capture_iterm(*, target: ItermTarget | None = None) -> tuple[int, str]:
    if sys.platform != "darwin":
        return 1, "iTerm capture requires macOS"
    _load_env()
    t = target or resolve_target()
    try:
        r = subprocess.run(
            ["osascript", "-e", READ_SCRIPT],
            env=apply_target_env(t),
            capture_output=True,
            text=True,
            timeout=30,
            stdin=subprocess.DEVNULL,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return 1, str(e)
    if r.returncode != 0:
        return r.returncode, (r.stderr or r.stdout or "osascript failed").strip()
    return 0, r.stdout or ""


def capture(
    *,
    tail_lines: int | None = None,
    use_log: bool = True,
    target: ItermTarget | None = None,
) -> tuple[int, str]:
    _load_env()
    code, text = capture_iterm(target=target)
    if code != 0:
        return code, text

    if use_log:
        text = combined_text(text, tail_lines=tail_lines)
    elif tail_lines is not None and tail_lines > 0:
        lines = text.splitlines()
        text = "\n".join(lines[-tail_lines:])

    return 0, text


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture iTerm2 session text")
    parser.add_argument("--tail", type=int, default=0, help="Only last N lines (0 = all)")
    parser.add_argument("--window", type=int, help="Window index (1-based)")
    parser.add_argument("--front-window", action="store_true", help="Frontmost window")
    parser.add_argument("--tab", type=int, help="Tab index (1-based)")
    parser.add_argument("--session", type=int, help="Split pane index (1-based)")
    parser.add_argument("--no-log-buffer", action="store_true", help="Skip local log buffer")
    parser.add_argument("--log-only", action="store_true", help="Read local log only")
    parser.add_argument("--reset-log", action="store_true", help="Clear local session log")
    parser.add_argument("-o", "--output", help="Write to file instead of stdout")
    args = parser.parse_args()

    win = None if args.front_window else args.window
    target = resolve_target(window=win, tab=args.tab, session=args.session)

    if args.reset_log:
        reset_log()
        print(f"iterm session log cleared ({target.label()})")
        return 0

    tail = args.tail if args.tail > 0 else None
    if args.log_only:
        text = read_log(tail_lines=tail)
        code = 0
    else:
        code, text = capture(tail_lines=tail, use_log=not args.no_log_buffer, target=target)

    if code != 0:
        print(text, file=sys.stderr)
        return code
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"wrote {len(text)} chars -> {args.output} [{target.label()}]")
    else:
        sys.stdout.write(text)
        if text and not text.endswith("\n"):
            sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
