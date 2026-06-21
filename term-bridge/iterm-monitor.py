#!/usr/bin/env python3
"""Poll iTerm session output and send Claude Code assistant replies to Telegram.

OUTBOUND-ONLY: this sends via `tg-notify send` (HTTP sendMessage) and never calls
getUpdates / run_polling. So it does NOT consume Telegram updates and never
conflicts with tg-relay (the sole updates consumer) over the shared bot token.
"""
from __future__ import annotations

import argparse
import hashlib
import json
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
from interactive_prompt import (  # noqa: E402
    detect_select_prompt,
    extract_select_options,
    options_key,
    should_auto_default,
)
from iterm_target import resolve_target  # noqa: E402
from reply_dedup import append_buffer, is_duplicate, read_buffer  # noqa: E402
from target_default import current_target  # noqa: E402
from tg_format import format_reply, strip_terminal_noise  # noqa: E402
from tg_format_config import get_format  # noqa: E402


def _monitor_file(kind: str) -> Path:
    # The per-message `--once` poll pins its target via ITERM_MONITOR_SUFFIX so its
    # dedup cursors share the namespace of the tab it captured. The long-running
    # daemon sets no such env and follows the live /tab default via current_target().
    suffix = os.environ.get("ITERM_MONITOR_SUFFIX", "").strip() or current_target().log_suffix()
    return ROOT / "inbox" / f"iterm-monitor-{suffix}.{kind}"


def reload_cursors_on_change(old_label: str, new_label: str) -> bool:
    """True when the resolved target changed since last poll (cursors must reload)."""
    return old_label != new_label


# --- Pure, unit-testable helpers ------------------------------------------

# Default bounded tail (lines). 0 (explicitly set) still means "all"; this
# sane cap stops both the every-5s rescan of the whole buffer AND a stale
# "Crunched for Ns" marker high in the scrollback keeping is_reply_complete()
# permanently True. The bounded window already mitigates the stale-marker case
# (the old marker scrolls out of a 400-line tail well before it would mislead
# completion detection), so completion scoping is left to the tail bound.
_DEFAULT_TAIL_LINES = 400

# Textual fragments (AppleScript / backend errors) that mean the target
# window/tab/session no longer exists or the app is gone. Matched case-folded.
_TARGET_LOST_PATTERNS: tuple[str, ...] = (
    "window not found",
    "tab not found",
    "no iterm window open",
    "no terminal window open",
    "no iterm running",
    "no terminal running",
    "invalid window id",
    "session not found",
)

# Apple error numbers raised when an object index/id can't be resolved.
_TARGET_LOST_CODES: tuple[str, ...] = (
    "-1719",  # errAENoSuchObject: invalid index / object not found
    "-1728",  # errAENoSuchObject (can't get object)
)


def is_target_lost_error(text: str) -> bool:
    """True when *text* indicates the target window/tab/session is gone.

    Conservative: only fires on the known "missing object" AppleScript errors
    so a transient capture hiccup is not misread as the tab being closed.
    """
    if not text:
        return False
    low = text.lower()
    if any(p in low for p in _TARGET_LOST_PATTERNS):
        return True
    return any(code in text for code in _TARGET_LOST_CODES)


def should_alert_once(*, condition: bool, already_marked: bool) -> bool:
    """One-shot alert decision for an outage episode.

    Fire only when the *condition* holds and we have not already alerted for the
    current episode (the caller sets the mark on True, clears it on recovery).
    """
    return condition and not already_marked


def sha1_token(text: str) -> str:
    """Short, stable equality token for a (possibly large) capture string."""
    return hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()


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
    # Stores a short sha1 token of the last capture (used only for change
    # detection equality, never parsed/displayed) so the mark stays tiny.
    p = _monitor_file("state")
    if p.is_file():
        return p.read_text(encoding="utf-8")
    return ""


def _write_state(text: str) -> None:
    p = _monitor_file("state")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(sha1_token(text), encoding="utf-8")


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
    # Stores a short sha1 token of the fired stable_key (equality-only, never
    # parsed/displayed) so the mark cannot grow to the size of a full capture.
    p = _monitor_file("auto-default-mark")
    return p.read_text(encoding="utf-8") if p.is_file() else ""


def _write_auto_default_mark(key: str) -> None:
    p = _monitor_file("auto-default-mark")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(sha1_token(key), encoding="utf-8")


def _mark_is_set(kind: str) -> bool:
    """True when an episode mark file exists with a truthy value."""
    p = _monitor_file(kind)
    if not p.is_file():
        return False
    try:
        return bool(p.read_text(encoding="utf-8").strip())
    except OSError:
        return False


def _set_mark(kind: str, value: str = "1") -> None:
    """Write (or clear, with "") an episode mark; never raises out of the loop."""
    try:
        p = _monitor_file(kind)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(value, encoding="utf-8")
    except OSError as exc:
        print(f"iterm-monitor: could not write mark {kind}: {exc}", flush=True)


def _read_prompt_alert_mark() -> str:
    p = _monitor_file("prompt-alert-mark")
    return p.read_text(encoding="utf-8") if p.is_file() else ""


def _write_prompt_alert_mark(key: str) -> None:
    p = _monitor_file("prompt-alert-mark")
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
    # subprocess args cannot contain NUL — terminal capture occasionally yields one
    # (e.g. partial escape sequences), which would crash fork_exec with
    # "ValueError: embedded null byte". Strip it defensively.
    text = text.replace("\x00", "")
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


def _send_tg_buttons(text: str, buttons: list[list[str]]) -> tuple[int, str]:
    """Send a plain message with an inline keyboard (one button per row)."""
    token, chat_id, err = _resolve_monitor_target()
    if err:
        return 1, err
    env = {**os.environ, "TELEGRAM_BOT_TOKEN": token or ""}
    cmd = [
        "tg-notify", "send", "--chat-id", str(chat_id),
        "--text", text.replace("\x00", ""),
        "--buttons", json.dumps(buttons, ensure_ascii=False),
    ]
    r = subprocess.run(
        cmd, cwd=ROOT, capture_output=True, text=True, timeout=60, env=env,
        stdin=subprocess.DEVNULL,
    )
    return r.returncode, ((r.stdout or "") + (r.stderr or "")).strip()


_TARGET_LOST_ALERT = "⚠️ 目标终端已关闭或不可达，发送 /tab 重新选择目标"


def _send_owner_alert(text: str) -> bool:
    """Best-effort one-line alert to the owner chat.

    Returns True on success. Never raises and never retries: if even sending the
    alert fails, it is printed locally so the loop can never retry-storm on it.
    """
    try:
        code, msg = _send_tg(text, "plain")
    except Exception as exc:  # noqa: BLE001 — loop must never die on a probe
        print(f"iterm-monitor: alert send crashed: {exc}", flush=True)
        return False
    if code != 0:
        print(f"iterm-monitor: alert send failed: {msg}", flush=True)
        return False
    return True


def _alert_target_lost() -> None:
    """Fire the target-lost alert at most once per outage episode."""
    if not should_alert_once(condition=True, already_marked=_mark_is_set("target-lost-mark")):
        return
    if _send_owner_alert(_TARGET_LOST_ALERT):
        _set_mark("target-lost-mark")
    # On a failed send we leave the mark unset so the next outage poll can retry
    # the one alert; _send_owner_alert already prints, so this cannot storm.


def _clear_target_lost() -> None:
    """Clear the outage mark once the target is reachable again."""
    if _mark_is_set("target-lost-mark"):
        _set_mark("target-lost-mark", "")


def _flatten_reason(reason: str) -> str:
    """One-line, length-capped reason for an alert (keeps any embedded hint)."""
    clean = " ".join((reason or "未知错误").split())
    return clean[:300] or "未知错误"


def _alert_send_failed(reason: str) -> None:
    """Fire the reply-send-failure alert at most once per failure episode.

    The reason is flattened (not truncated to its first line) so an embedded
    Screen-Recording-denied hint from the screenshot fallback still reaches the
    phone user.
    """
    if not should_alert_once(condition=True, already_marked=_mark_is_set("send-fail-mark")):
        return
    if _send_owner_alert(f"⚠️ 回传失败：{_flatten_reason(reason)}（发 /status 排查）"):
        _set_mark("send-fail-mark")


def _clear_send_failed() -> None:
    """Clear the send-failure mark after a successful reply send."""
    if _mark_is_set("send-fail-mark"):
        _set_mark("send-fail-mark", "")


def _send_screenshot_or_text(to_send: str) -> tuple[int, str]:
    """Screenshot mode: send a screenshot, else fall back to text.

    On a double failure the screenshot reason (incl. any Screen-Recording-denied
    hint) is surfaced alongside the text failure so the owner alert can relay it.
    """
    code, msg = _send_iterm_screenshot()
    if code == 0:
        return code, msg
    shot_reason = msg
    t_code, t_msg = _send_tg(to_send, "html")
    if t_code == 0:
        return 0, f"screenshot failed → sent text ({shot_reason})"
    return t_code, f"screenshot+text failed: {shot_reason}; text: {t_msg}"


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

    # Rolling similarity gate: skip a candidate that repeats any of the last
    # BUFFER_CAP sent messages (catches non-consecutive / re-emitted duplicates
    # that new_content_since — which only compares the previous send — misses).
    buf_path = _monitor_file("sent-buffer")
    if is_duplicate(to_send, read_buffer(buf_path)):
        return 0, "already sent (similar)"

    fmt = _output_format()
    if fmt == "screenshot":
        code, msg = _send_screenshot_or_text(to_send)
    else:
        code, msg = _send_tg(to_send, fmt)

    if code == 0:
        _write_last_sent(reply)
        _write_last_sent_at(time.time())
        append_buffer(buf_path, to_send)
        _clear_send_failed()
        return code, msg or "sent"

    # Hard failure for this turn (text — and screenshot fallback if attempted):
    # alert the owner once per failure episode so the channel isn't silently dead.
    _alert_send_failed(msg or "send failed")
    return code, msg or "send failed"


def poll_once(*, tail_lines: int, force: bool = False) -> tuple[int, str]:
    code, current = _capture_tail(tail_lines)
    if code != 0:
        return code, current

    prev = _read_state()
    if not force and sha1_token(current) == prev:
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
    target = current_target()
    print(
        f"iterm-monitor: @landpage_ipa_addr_bot private chat_id={chat_id} "
        f"target={target.label()} (可被 /tab 覆盖) tail={tail_lines} interval={interval}s "
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
        new_target = current_target()
        if reload_cursors_on_change(target.log_suffix(), new_target.log_suffix()):
            target = new_target
            last_seen_reply = _read_last_sent()
            last_extract_change_at = _read_last_sent_at() or time.time()
            last_capture = ""
            last_stable = ""
            stable_count = 0
            stable_since = time.time()
        code, current = _capture_tail(tail_lines)
        ts = time.strftime("%H:%M:%S")
        if code != 0:
            print(f"[{ts}] error: {code} {current}", flush=True)
            # Target window/tab gone → tell the phone user once per outage so
            # they aren't left with a silently dead channel. Any other capture
            # error is transient and just logged + retried.
            if is_target_lost_error(current):
                _alert_target_lost()
            if once:
                return 1
            time.sleep(interval)
            continue

        # Capture succeeded → target is reachable; reset the outage one-shot so a
        # future outage re-alerts.
        _clear_target_lost()
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

        # Interactive prompt → push the options as inline buttons (once per
        # distinct prompt) so the operator picks the right one from the phone,
        # instead of silently auto-defaulting to the first option.
        if detect_select_prompt(current):
            opts = extract_select_options(current)
            # Dedup on the options themselves, not the full screen: the spinner
            # and timer churn `stable_key` every poll, which would otherwise
            # re-push the same menu over and over.
            opt_key = options_key(opts)
            if opts and _read_prompt_alert_mark() != opt_key:
                _write_prompt_alert_mark(opt_key)
                buttons = [
                    [f"{n}. {label[:40]}", f"sel:{target.window}:{target.tab}:{n}"]
                    for n, label in opts
                ]
                b_code, b_msg = _send_tg_buttons("⏳ Agent 在等你选择（点按钮选）:", buttons)
                print(f"[{ts}] prompt buttons: {len(opts)} opts ({b_code} {b_msg[:50]})", flush=True)
        elif _read_prompt_alert_mark():
            # Prompt is gone — clear the mark so the next menu re-alerts even if
            # it happens to have identical option labels.
            _write_prompt_alert_mark("")

        if auto_default > 0:
            is_prompt = detect_select_prompt(current)
            if should_auto_default(
                is_prompt=is_prompt,
                stable_elapsed=time.time() - stable_since,
                threshold=auto_default,
                # Compare hashed tokens on both sides: the mark stores a sha1
                # token of stable_key, so feed the matching token here.
                stable_key=sha1_token(stable_key),
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
    # Bounded default (400 lines): avoids rescanning the whole accumulated buffer
    # every poll AND prevents a stale "Crunched for Ns" marker high in the
    # scrollback from keeping is_reply_complete() permanently True. Set
    # TG_ITERM_MONITOR_TAIL=0 explicitly to restore the unbounded "all" behavior.
    parser.add_argument(
        "--tail",
        type=int,
        default=int(os.environ.get("TG_ITERM_MONITOR_TAIL", str(_DEFAULT_TAIL_LINES))),
    )
    parser.add_argument("--once", action="store_true", help="Poll once and exit")
    parser.add_argument("--force", action="store_true", help="Send even if unchanged")
    parser.add_argument("--reset", action="store_true", help="Clear state file")
    args = parser.parse_args()

    if args.reset:
        from iterm_log_buffer import reset as reset_log_buffer
        for kind in ("state", "last-sent", "last-sent-at", "screenshot-mark", "auto-default-mark", "prompt-alert-mark", "shot-fp", "sent-buffer", "target-lost-mark", "send-fail-mark"):
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
