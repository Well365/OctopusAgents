#!/usr/bin/env python3
"""Capture the whole Mac screen and send it to Telegram.

Uses `screencapture -x` (silent, no shutter sound) so nothing steals focus.
Sent via tg-notify, the same outbound path as the device/terminal screenshots.

Requires the Screen Recording permission for the process that launches the
relay (Terminal/iTerm) — same as the terminal-window screenshot.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


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


def _chat_id() -> str | None:
    raw = (
        os.environ.get("TG_ITERM_MONITOR_CHAT_ID", "").strip()
        or os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    )
    return raw or None


def _send_photo(path: Path, chat: str, caption: str) -> tuple[int, str]:
    cmd = [
        "tg-notify", "send",
        "--photo", str(path),
        "--chat-id", chat,
        "--caption", caption,
    ]
    r = subprocess.run(
        cmd, cwd=ROOT, capture_output=True, text=True, timeout=120,
        stdin=subprocess.DEVNULL,
    )
    out = ((r.stdout or "") + (r.stderr or "")).strip()
    return r.returncode, out


def capture_and_send(
    *, caption: str | None = None, display: str | None = None
) -> tuple[int, str]:
    _load_env()
    chat = _chat_id()
    if not chat:
        return 1, "no chat id (set TELEGRAM_CHAT_ID)"
    cap = caption or "Mac"
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "mac-screen.png"
        cmd = ["screencapture", "-x"]
        if display:
            cmd += ["-D", display]
        cmd.append(str(path))
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return 1, f"screencapture failed: {exc}"
        if r.returncode != 0 or not path.is_file():
            return 1, (r.stderr or "screencapture failed").strip()
        code, out = _send_photo(path, chat, cap)
        if code == 0:
            return 0, out or "mac screenshot sent"
        return code, out or "screenshot send failed"


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture full Mac screen -> Telegram")
    parser.add_argument("--caption", help="Telegram photo caption")
    parser.add_argument(
        "--display", help="Display number for `screencapture -D` (default: main)"
    )
    args = parser.parse_args()
    code, msg = capture_and_send(caption=args.caption, display=args.display)
    print(msg)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
