#!/usr/bin/env python3
"""Poll iTerm session output and send Claude Code assistant replies to Telegram."""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(ROOT / "term-bridge"))
import term_backend  # noqa: E402
from iterm_extract import (  # noqa: E402
    extract_latest_reply,
    is_reply_complete,
    new_content_since,
    normalize_for_stable_compare,
    should_text_fallback,
)
from interactive_prompt import detect_select_prompt, should_auto_default  # noqa: E402
from iterm_target import resolve_target  # noqa: E402
from tg_format import format_reply, strip_terminal_noise  # noqa: E402
from tg_format_config import get_format  # noqa: E402


def _monitor_file(kind: str) -> Path:
    return ROOT / "inbox" / f"iterm-monitor-{resolve_target().log_suffix()}.{kind}"



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


def _resolve_monitor_target() -> tuple[str | None, int | None, str | None]:
    """Same bot as tg-relay (@landpage_ipa_addr_bot): TELEGRAM_BOT_TOKEN + private TELEGRAM_CHAT_ID."""
    token = (
        os.environ.get("TG_ITERM_MONITOR_BOT_TOKEN", "").strip()
        or os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    )
    chat_raw = (
        os.environ.get("TG_ITERM_MONITOR_CHAT_ID", "").strip()
        or os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    )
    if not token:
        return None, None, "TELEGRAM_BOT_TOKEN not set (@landpage_ipa_addr_bot)"
    if not chat_raw:
        return None, None, (
            "TELEGRAM_CHAT_ID not set — use your private chat id with @landpage_ipa_addr_bot "
            "(positive number, not a group)"
        )
    try:
        chat_id = int(chat_raw)
    except ValueError:
        return None, None, f"invalid chat id: {chat_raw}"
    if chat_id < 0:
        return (
            None,
            None,
            "TELEGRAM_CHAT_ID is a group (negative). Use private chat with @landpage_ipa_addr_bot "
            "(positive number).",
        )
    return token, chat_id, None


def _capture_tail(lines: int) -> tuple[int, str]:
    cmd = [sys.executable, str(term_backend.capture_script())]
    if lines > 0:
        cmd.extend(["--tail", str(lines)])
    r = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=45,
        stdin=subprocess.DEVNULL,  # daemon fd0 may be closed → child Python would crash
    )
    out = ((r.stdout or "") + (r.stderr or "")).strip()
    return r.returncode, out


def _read_state() -> str:
    p = _monitor_file("state")
    if p.is_file():
        return p.read_text(encoding="utf-8")
    return ""


def _write_state(text: str) -> None:
    p = _monitor_file("state")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _read_last_sent() -> str:
    p = _monitor_file("last-sent")
    if p.is_file():
        return p.read_text(encoding="utf-8")
    return ""


def _write_last_sent(text: str) -> None:
    p = _monitor_file("last-sent")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")



def _read_last_sent_at() -> float:
    p = _monitor_file("last-sent-at")
    if p.is_file():
        try:
            return float(_monitor_file("last-sent-at").read_text(encoding="utf-8").strip())
        except ValueError:
            pass
    return 0.0


def _write_last_sent_at(ts: float) -> None:
    p = _monitor_file("last-sent-at")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(str(ts), encoding="utf-8")


def _screenshot_idle_seconds() -> float:
    raw = os.environ.get("TG_ITERM_MONITOR_SCREENSHOT_IDLE", "60").strip()
    if raw.lower() in ("", "0", "false", "no", "off"):
        return 0.0
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 60.0


def _text_fallback_seconds() -> float:
    """Force-send the latest reply as text after it's been stable this long.

    Safety net for when the completion marker isn't recognized: 90s default,
    0/off disables. Env: TG_ITERM_MONITOR_TEXT_FALLBACK.
    """
    raw = os.environ.get("TG_ITERM_MONITOR_TEXT_FALLBACK", "90").strip()
    if raw.lower() in ("", "0", "false", "no", "off"):
        return 0.0
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 90.0


def _auto_default_seconds() -> float:
    """Auto-press Enter on a stuck select-prompt after this long. Env
    TG_ITERM_MONITOR_AUTO_DEFAULT (default 60, 0/off/false/no/empty disables)."""
    raw = os.environ.get("TG_ITERM_MONITOR_AUTO_DEFAULT", "60").strip()
    if raw.lower() in ("", "0", "false", "no", "off"):
        return 0.0
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 60.0


def _auto_default_caption() -> str:
    return os.environ.get(
        "TG_ITERM_AUTO_DEFAULT_CAPTION", "⏱ 1 分钟未选择，已默认选择第一项"
    ).strip() or "⏱ 已默认选择第一项"


def _read_auto_default_mark() -> str:
    p = _monitor_file("auto-default-mark")
    return p.read_text(encoding="utf-8") if p.is_file() else ""


def _write_auto_default_mark(key: str) -> None:
    p = _monitor_file("auto-default-mark")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(key, encoding="utf-8")


def _inject_key(key: str, target) -> tuple[int, str]:
    cmd = [sys.executable, str(term_backend.inject_script()), "--key", key]
    if target.window is None:
        cmd.append("--front-window")
    else:
        cmd.extend(["--window", str(target.window)])
    cmd.extend(["--tab", str(target.tab)])
    r = subprocess.run(
        cmd, cwd=ROOT, capture_output=True, text=True, timeout=30,
        env=os.environ.copy(), stdin=subprocess.DEVNULL,
    )
    return r.returncode, ((r.stdout or "") + (r.stderr or "")).strip()


def _screenshot_marked_for_turn(sent_at: float) -> bool:
    if sent_at <= 0 or not _monitor_file("screenshot-mark").is_file():
        return False
    try:
        return float(_monitor_file("screenshot-mark").read_text(encoding="utf-8").strip()) >= sent_at
    except ValueError:
        return False


def _mark_screenshot_for_turn(sent_at: float) -> None:
    p = _monitor_file("screenshot-mark")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(str(sent_at), encoding="utf-8")


def _send_iterm_screenshot() -> tuple[int, str]:
    cap = os.environ.get(
        "TG_ITERM_SCREENSHOT_CAPTION",
        "iTerm · 1 分钟内无新输出",
    ).strip() or "iTerm"
    r = subprocess.run(
        [
            sys.executable,
            str(term_backend.screenshot_script()),
            "--caption", cap,
            "--dedup-state", str(_monitor_file("shot-fp")),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        env=os.environ.copy(),
        stdin=subprocess.DEVNULL,  # daemon fd0 may be closed → child Python would crash
    )
    out = ((r.stdout or "") + (r.stderr or "")).strip()
    if r.returncode == 0:
        return 0, out or "screenshot sent"
    return r.returncode, out or "screenshot failed"

def _output_format() -> str:
    """Current format: Telegram /format state file → env TG_ITERM_FORMAT → html.

    Read fresh each send so a /format command takes effect without a restart.
    """
    return get_format()


def _run_send(env: dict, chat_id: int, text: str, parse_mode: str | None) -> tuple[int, str]:
    cmd = ["tg-notify", "send", "--chat-id", str(chat_id)]
    if parse_mode:
        cmd += ["--parse-mode", parse_mode]
    cmd += ["--text", text]  # --text avoids leading '-' being parsed as a flag
    r = subprocess.run(
        cmd, cwd=ROOT, capture_output=True, text=True, timeout=60, env=env,
        stdin=subprocess.DEVNULL,  # daemon fd0 may be closed → child Python would crash
    )
    out = ((r.stdout or "") + (r.stderr or "")).strip()
    return r.returncode, out


def _send_tg(text: str, fmt: str = "html") -> tuple[int, str]:
    raw = text.strip()
    if not raw:
        return 0, "skip empty"

    token, chat_id, err = _resolve_monitor_target()
    if err:
        return 1, err
    env = {**os.environ, "TELEGRAM_BOT_TOKEN": token or ""}

    # Preferred: phone-friendly markup in the chosen format.
    body, parse_mode = format_reply(raw, fmt)
    if body:
        code, out = _run_send(env, chat_id, body, parse_mode)
        if code == 0:
            return 0, f"sent to private chat {chat_id} [{fmt}] ({out or 'ok'})"

    # Fallback: cleaned plain text, so a reply is never lost on a markup error.
    plain = strip_terminal_noise(raw) or raw
    if len(plain) > 3900:
        plain = "…\n" + plain[-3900:]
    code, out = _run_send(env, chat_id, plain, parse_mode=None)
    if code == 0:
        return 0, f"sent to private chat {chat_id} plain ({out or 'ok'})"
    return code, out


def _maybe_send_reply(capture: str, *, force: bool = False) -> tuple[int, str]:
    reply = extract_latest_reply(capture)
    if not reply:
        return 0, "no assistant reply"

    last_sent = _read_last_sent()
    if not force and reply == last_sent:
        return 0, "already sent"

    # Strip any leading block already delivered in the previous message so the
    # same text isn't repeated (streaming progress sends / verbatim re-sends).
    to_send = reply if force else new_content_since(reply, last_sent)
    if not to_send.strip():
        return 0, "already sent"

    fmt = _output_format()
    if fmt == "screenshot":
        # New reply detected → send an iTerm screenshot instead of text.
        code, msg = _send_iterm_screenshot()
        if code != 0:
            # Screenshot failed (e.g. macOS Automation/Screen-Recording perms)
            # → fall back to text so the reply is never lost.
            t_code, t_msg = _send_tg(to_send, "html")
            if t_code == 0:
                code, msg = 0, f"screenshot failed → sent text ({msg})"
    else:
        code, msg = _send_tg(to_send, fmt)
    if code == 0:
        _write_last_sent(reply)
        _write_last_sent_at(time.time())
    return code, msg or "sent"


def poll_once(*, tail_lines: int, force: bool = False) -> tuple[int, str]:
    code, current = _capture_tail(tail_lines)
    if code != 0:
        return code, current

    prev = _read_state()
    if not force and current == prev:
        return 0, "no change"

    _write_state(current)
    return _maybe_send_reply(current, force=force)


def run_loop(*, interval: float, tail_lines: int, once: bool) -> int:
    _load_env()
    _, chat_id, err = _resolve_monitor_target()
    if err:
        print(f"iterm-monitor: config error: {err}", flush=True)
        return 1
    stable_polls = max(1, int(os.environ.get("TG_ITERM_MONITOR_STABLE_POLLS", "2")))
    screenshot_idle = _screenshot_idle_seconds()
    text_fallback = _text_fallback_seconds()
    auto_default = _auto_default_seconds()
    target = resolve_target()
    print(
        f"iterm-monitor: @landpage_ipa_addr_bot private chat_id={chat_id} "
        f"target={target.label()} tail={tail_lines} interval={interval}s "
        f"stable_polls={stable_polls} screenshot_idle={screenshot_idle}s "
        f"text_fallback={text_fallback}s",
        flush=True,
    )
    last_capture = ""
    last_stable = ""
    stable_count = 0
    stable_since = time.time()
    last_seen_reply = _read_last_sent()
    last_extract_change_at = _read_last_sent_at() or time.time()
    while True:
        code, current = _capture_tail(tail_lines)
        ts = time.strftime("%H:%M:%S")
        if code != 0:
            print(f"[{ts}] error: {code} {current}", flush=True)
            if once:
                return 1
            time.sleep(interval)
            continue

        stable_key = normalize_for_stable_compare(current)
        complete = is_reply_complete(current)

        if stable_key != last_stable:
            last_capture = current
            last_stable = stable_key
            stable_count = 0
            stable_since = time.time()
            _write_state(current)
        else:
            stable_count += 1

        reply_now = extract_latest_reply(current)
        if reply_now != last_seen_reply:
            last_seen_reply = reply_now
            last_extract_change_at = time.time()

        ready = complete or stable_count >= stable_polls
        if ready:
            send_code, msg = _maybe_send_reply(current)
            if send_code != 0:
                print(f"[{ts}] error: {msg}", flush=True)
            elif msg == "no assistant reply":
                if complete:
                    print(f"[{ts}] warn: output complete but could not extract reply", flush=True)
            elif msg != "already sent":
                print(f"[{ts}] {msg}", flush=True)
            stable_count = 0

        # 90s text catch-up: completion marker missed but the reply settled →
        # force-send the text so a finished answer never sits unsent.
        if should_text_fallback(
            reply_now, _read_last_sent(), time.time() - last_extract_change_at, text_fallback
        ):
            fb_code, fb_msg = _maybe_send_reply(current)
            if fb_code == 0 and fb_msg not in ("no assistant reply", "already sent"):
                print(f"[{ts}] {fb_msg} (text fallback {text_fallback:.0f}s)", flush=True)
                stable_count = 0
                if once:
                    return 0
                time.sleep(interval)
                continue

        if screenshot_idle > 0:
            last_sent_at = _read_last_sent_at()
            if last_sent_at > 0:
                now = time.time()
                since_send = now - last_sent_at
                since_extract = now - last_extract_change_at
                if (
                    since_send >= screenshot_idle
                    and since_extract >= screenshot_idle
                    and not _screenshot_marked_for_turn(last_sent_at)
                ):
                    # Re-try text extract before screenshot (marker format may have changed)
                    retry_code, retry_msg = _maybe_send_reply(current)
                    if retry_code == 0 and retry_msg not in (
                        "no assistant reply",
                        "already sent",
                    ):
                        print(f"[{ts}] {retry_msg} (before screenshot)", flush=True)
                        stable_count = 0
                        if once:
                            return 0
                        time.sleep(interval)
                        continue
                    shot_code, shot_msg = _send_iterm_screenshot()
                    if shot_code == 0:
                        _mark_screenshot_for_turn(last_sent_at)
                        print(f"[{ts}] screenshot: {shot_msg}", flush=True)
                    else:
                        print(f"[{ts}] screenshot error: {shot_msg}", flush=True)

        if auto_default > 0:
            is_prompt = detect_select_prompt(current)
            if should_auto_default(
                is_prompt=is_prompt,
                stable_elapsed=time.time() - stable_since,
                threshold=auto_default,
                stable_key=stable_key,
                last_fired_key=_read_auto_default_mark(),
            ):
                k_code, k_msg = _inject_key("enter", target)
                if k_code == 0:
                    _write_auto_default_mark(stable_key)
                    _send_tg(_auto_default_caption(), "plain")
                    print(f"[{ts}] auto-default: pressed enter ({k_msg})", flush=True)
                else:
                    print(f"[{ts}] auto-default error: {k_msg}", flush=True)

        if once:
            return 0
        time.sleep(interval)


def main() -> int:
    parser = argparse.ArgumentParser(description="Monitor iTerm output -> Telegram (assistant reply only)")
    parser.add_argument("--interval", type=float, default=float(os.environ.get("TG_ITERM_MONITOR_INTERVAL", "5")))
    parser.add_argument("--tail", type=int, default=int(os.environ.get("TG_ITERM_MONITOR_TAIL", "0")))
    parser.add_argument("--once", action="store_true", help="Poll once and exit")
    parser.add_argument("--force", action="store_true", help="Send even if unchanged")
    parser.add_argument("--reset", action="store_true", help="Clear state file")
    args = parser.parse_args()

    if args.reset:
        from iterm_log_buffer import reset as reset_log_buffer
        for kind in ("state", "last-sent", "last-sent-at", "screenshot-mark", "auto-default-mark", "shot-fp"):
            p = _monitor_file(kind)
            if p.is_file():
                p.unlink()
        reset_log_buffer()
        print("state cleared (incl. session log buffer)")
        return 0

    if args.once or args.force:
        _load_env()
        code, msg = poll_once(tail_lines=args.tail, force=args.force)
        print(msg)
        return 0 if code == 0 else 1

    try:
        return run_loop(interval=args.interval, tail_lines=args.tail, once=False)
    except KeyboardInterrupt:
        print("\nstopped")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
