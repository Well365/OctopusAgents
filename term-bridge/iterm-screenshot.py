#!/usr/bin/env python3
"""Capture the target iTerm2 window (no activate) and send to Telegram.

Uses `screencapture -l <window-id>` so iTerm is never pulled to the front and
no System Events automation is needed — see iterm_shot for the rationale.
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
from iterm_shot import capture_window_png, select_tab  # noqa: E402
from iterm_target import resolve_target  # noqa: E402
from screenshot_dedup import (  # noqa: E402
    DEFAULT_THRESHOLD,
    fingerprint,
    is_duplicate,
    read_fingerprint,
    write_fingerprint,
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


def _chat_id() -> str | None:
    raw = (
        os.environ.get("TG_ITERM_MONITOR_CHAT_ID", "").strip()
        or os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    )
    return raw or None


def _no_shadow() -> bool:
    raw = os.environ.get("TG_ITERM_SCREENSHOT_NO_SHADOW", "1").strip().lower()
    return raw not in ("", "0", "false", "no", "off")


def _similarity_threshold() -> float:
    """Skip a screenshot when >= this fraction similar to the last sent one.
    Env TG_ITERM_SCREENSHOT_SIMILARITY (default 0.95)."""
    raw = os.environ.get("TG_ITERM_SCREENSHOT_SIMILARITY", "").strip()
    if not raw:
        return DEFAULT_THRESHOLD
    try:
        return min(1.0, max(0.0, float(raw)))
    except ValueError:
        return DEFAULT_THRESHOLD


def _send_photo(path: Path, chat: str, caption: str, env: dict) -> tuple[int, str]:
    cmd = [
        "tg-notify", "send",
        "--photo", str(path),
        "--chat-id", chat,
        "--caption", caption,
    ]
    r = subprocess.run(
        cmd, cwd=ROOT, capture_output=True, text=True, timeout=120, env=env,
        stdin=subprocess.DEVNULL,
    )
    out = ((r.stdout or "") + (r.stderr or "")).strip()
    return r.returncode, out


def capture_and_send(*, caption: str | None = None, dedup_state: str | None = None) -> tuple[int, str]:
    if sys.platform != "darwin":
        return 1, "iTerm screenshot requires macOS"

    _load_env()
    chat = _chat_id()
    if not chat:
        return 1, "TELEGRAM_CHAT_ID not set"

    cap = caption or os.environ.get("TG_ITERM_SCREENSHOT_CAPTION", "iTerm").strip() or "iTerm"
    target = resolve_target()

    env = {**os.environ}
    if os.environ.get("TELEGRAM_BOT_TOKEN"):
        env["TELEGRAM_BOT_TOKEN"] = os.environ["TELEGRAM_BOT_TOKEN"]

    handle = tempfile.NamedTemporaryFile(suffix=".png", prefix="iterm-shot-", delete=False)
    handle.close()
    path = Path(handle.name)
    try:
        # Bring the target tab to the front of its window so we capture it,
        # not whatever tab happens to be visible.
        select_tab(target.window, target.tab, app="iTerm")
        capture_window_png(target.window, path, no_shadow=_no_shadow())
        # Skip near-identical screenshots so an idle session doesn't spam Telegram.
        fp = None
        if dedup_state:
            fp = fingerprint(path.read_bytes())
            if is_duplicate(fp, read_fingerprint(dedup_state), threshold=_similarity_threshold()):
                return 0, "similar screenshot (>= threshold), skipped"
        code, out = _send_photo(path, chat, cap, env)
        if code == 0:
            if dedup_state and fp is not None:
                write_fingerprint(dedup_state, fp)
            return 0, out or "screenshot sent"
        return code, out or "screenshot send failed"
    except (RuntimeError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)
    finally:
        try:
            path.unlink()
        except OSError:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(description="iTerm window screenshot -> Telegram")
    parser.add_argument("--caption", help="Telegram photo caption")
    parser.add_argument("--dedup-state", help="File storing last-sent screenshot fingerprint; skip if similar")
    args = parser.parse_args()
    code, msg = capture_and_send(caption=args.caption, dedup_state=args.dedup_state)
    print(msg)
    return 0 if code == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
