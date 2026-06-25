#!/usr/bin/env python3
"""Telegram bot: receive device commands, execute via droid-ctl/ioskit, reply with results."""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "term-bridge"))
from chat_allowlist import is_allowed, resolve_allowlist  # noqa: E402
from tg_menu import MENU_COMMANDS, dispatch_callback, menu_for_command, tab_submenu  # noqa: E402
from iterm_route import list_tabs  # noqa: E402
from term_backend import screenshot_script  # noqa: E402
from message_guard import sanitize_injection  # noqa: E402
from rate_limit import RateLimiter  # noqa: E402

INBOX_DIR = ROOT / "inbox"
INBOX_FILE = INBOX_DIR / "pending.txt"

# Shown when injection is explicitly disabled: the message is only stored in the
# inbox and never reaches the terminal. Make that unmistakable to the user.
INJECT_DISABLED_NOTICE = (
    "⚠️ 注入已关闭：消息只存入 inbox，未发送到终端"
    "（在 .env 设 TG_RELAY_ITERM_INJECT=1 开启）"
)


def _load_env() -> None:
    candidates = [ROOT / ".env"]
    if os.environ.get("TGKIT_ENV_FILE"):
        candidates.insert(0, Path(os.environ["TGKIT_ENV_FILE"]))
    for p in candidates:
        if not p.is_file():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip("'\""))
        return


def _run(cmd: list[str]) -> tuple[int, str]:
    try:
        r = subprocess.run(
            cmd, cwd=ROOT, capture_output=True, text=True, timeout=120,
            stdin=subprocess.DEVNULL,  # daemon fd0 may be closed → child Python would crash
        )
        return r.returncode, ((r.stdout or "") + (r.stderr or "")).strip()
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return 1, str(e)


def _append_inbox(chat_id: int, text: str) -> None:
    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with INBOX_FILE.open("a", encoding="utf-8") as f:
        f.write(f"[{ts}] chat={chat_id}\n{text.strip()}\n---\n")


# Explicit "off" values that disable terminal injection. Anything else —
# including unset/empty — is treated as ENABLED: injection is the product's
# whole purpose and *who* may inject is already fail-closed by the chat-id
# allowlist, so requiring an opt-in only made the bot silently no-op.
_INJECT_OFF_VALUES = ("0", "false", "no", "off")


def _iterm_inject_enabled() -> bool:
    """True unless TG_RELAY_ITERM_INJECT is an explicit off value (0/false/no/off)."""
    v = os.environ.get("TG_RELAY_ITERM_INJECT", "").strip().lower()
    return v not in _INJECT_OFF_VALUES


def _injection_banner() -> str:
    """One-line, secret-free summary of injection state + active backend.

    Logged at startup so the relay's behavior is never silent — e.g.
    ``inject=ON backend=terminal``. Never includes the bot token.
    """
    state = "ON" if _iterm_inject_enabled() else "OFF"
    try:
        from term_backend import resolve_backend

        backend = resolve_backend()
    except Exception:  # backend resolution must never block startup logging
        backend = "unknown"
    return f"inject={state} backend={backend}"


def _inject_iterm(text: str) -> tuple[int, str]:
    return _run([sys.executable, str(ROOT / "term-bridge" / "iterm-inject.py"), text])


def _schedule_iterm_monitor_poll() -> None:
    delay = os.environ.get("TG_ITERM_MONITOR_AFTER", "").strip()
    if not delay or delay.lower() in ("0", "false", "no", "off"):
        return
    try:
        secs = max(5, int(delay))
    except ValueError:
        secs = 30
    monitor = str(ROOT / "term-bridge" / "iterm-monitor.py")
    subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "import time, subprocess, sys; "
                f"time.sleep({secs}); "
                f"subprocess.run([sys.executable, {monitor!r}, '--once'])"
            ),
        ],
        stdin=subprocess.DEVNULL,  # detached child Python needs a valid fd0
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def _handle_natural_language(chat_id: int, text: str) -> str:
    _append_inbox(chat_id, text)
    if _iterm_inject_enabled():
        if sys.platform != "darwin":
            return "saved to inbox (iTerm inject needs macOS)"
        code, out = _inject_iterm(text)
        if code == 0:
            _schedule_iterm_monitor_poll()
            preview = text[:80] + ("…" if len(text) > 80 else "")
            extra = ""
            if os.environ.get("TG_ITERM_MONITOR_AFTER", "").strip() not in ("", "0", "false", "no", "off"):
                extra = "\n(output -> TG after delay)"
            return f"✓ typed into iTerm (1st window)\n{preview}\n(+ inbox backup){extra}"
        return f"inbox saved; iTerm failed:\n{out[:800]}"
    return INJECT_DISABLED_NOTICE


def _parse_other_pids(pgrep_output: str, me: int) -> list[int]:
    """PIDs from pgrep output excluding our own pid (single-instance guard)."""
    pids: list[int] = []
    for tok in pgrep_output.split():
        try:
            p = int(tok)
        except ValueError:
            continue
        if p != me:
            pids.append(p)
    return pids


def _other_relay_pids() -> list[int]:
    """Other run_tg_relay.py processes on this host (excludes self).

    Two relays polling the same token conflict on getUpdates (Telegram 409),
    which also blocks tools like tg-setup from reading /start. Detect and refuse.
    """
    try:
        out = subprocess.run(
            ["pgrep", "-f", "run_tg_relay.py"],
            capture_output=True, text=True, timeout=5,
        ).stdout
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return []
    return _parse_other_pids(out, os.getpid())


def _parse_tap(args: list[str]) -> tuple[str, int, int] | None:
    if len(args) < 2:
        return None
    platform = args[2] if len(args) >= 3 and args[2] in ("android", "ios") else "android"
    try:
        return platform, int(args[0]), int(args[1])
    except ValueError:
        return None


def _pipeline_report() -> str:
    """Pipeline doctor report (relay/monitor/token/inject/...), best-effort.

    A stuck operator needs the *pipeline* status before the device doctor, so
    this runs first in /check. Any failure degrades to a one-line note instead
    of crashing the command.
    """
    try:
        import pipeline_doctor

        checks = pipeline_doctor.run_checks()
        return pipeline_doctor.format_report(checks, header="管道自检")
    except Exception as e:  # never let the doctor crash /check
        return f"管道自检失败: {e}"


def _device_report() -> str:
    """Mobile-device doctor (compose check), best-effort."""
    code, out = _run([str(ROOT / "mob-compose" / "compose"), "check"])
    return out if out else f"exit {code}"


def _check_report() -> str:
    """/check output: pipeline doctor first, mobile-device check below."""
    pipeline = _pipeline_report()
    devices = _device_report()
    combined = f"{pipeline}\n\n— 设备检查 —\n{devices}"
    return combined[:4000]


def _handle_command(text: str) -> str:
    text = text.strip()
    if not text:
        return "empty message"
    if not text.startswith("/"):
        return _handle_natural_language(0, text)

    parts = text.split()
    cmd = parts[0].lower().split("@")[0]
    args = parts[1:]

    if cmd in ("/start", "/help"):
        return (
            "mob-remote bot\n\n"
            "/check — environment check\n"
            "/shot android|ios|mac|term — screenshot to Telegram\n"
            "/tap X Y [android|ios]\n"
            "/swipe X1 Y1 X2 Y2 [android|ios]\n"
            "/devices — list devices\n\n"
            "Natural language -> terminal (routed tab) + inbox\n"
            "  (injection on by default; set TG_RELAY_ITERM_INJECT=0 to disable)"
        )

    if cmd == "/check":
        return _check_report()

    if cmd == "/devices":
        lines = []
        for label, cli in (("Android", "droid-ctl"), ("iOS", "iphone-ctl")):
            code, out = _run([cli, "devices"])
            lines.append(f"{label}:\n{out or f'exit {code}'}")
        return "\n\n".join(lines)[:4000]

    if cmd == "/unlock":
        # Numeric-PIN unlock of an Android device. PIN comes from the env
        # (ANDROID_UNLOCK_PIN), inherited by the child — never passed on argv.
        if not os.environ.get("ANDROID_UNLOCK_PIN", "").strip():
            return "未配置 PIN：在 .env 设 ANDROID_UNLOCK_PIN=数字密码（仅支持数字 PIN 锁）"
        code, out = _run(["droid-ctl", "unlock"])
        if code != 0:
            return out or f"解锁失败 ({code})"
        # screenshot the result so you can confirm it worked
        _run([str(ROOT / "mob-compose" / "scripts" / "shot-android.sh"), "-c", "解锁后"])
        return f"{out}\n（已截图发送确认）" if out else "已尝试解锁（截图已发）"

    if cmd == "/lockmac":
        # delegates to the standalone `lockmac` package (CLI on PATH)
        sub = args[0].lower() if args else "status"
        if sub == "on":
            code, out = _run(["lockmac", "on"])
            return f"{out}\n（旁人看物理屏=黑；你 /shot 截图仍拿真实内容）" if code == 0 else out
        if sub == "off":
            return _run(["lockmac", "off"])[1]
        if sub == "boot":
            val = args[1].lower() if len(args) > 1 else ""
            if val in ("on", "off"):
                return _run(["lockmac", "boot", val])[1]
            return "用法: /lockmac boot on|off"
        return _run(["lockmac", "status"])[1] + "\n用法: /lockmac on | off | boot on|off"

    if cmd == "/shot" and args:
        platform = args[0].lower()
        if platform in ("android", "ios"):
            script = "shot-android.sh" if platform == "android" else "shot-ios.sh"
            code, out = _run([str(ROOT / "mob-compose" / "scripts" / script), "-c", f"TG /shot {platform}"])
            return out or f"{platform} screenshot sent" if code == 0 else (out or f"failed ({code})")
        if platform == "mac":
            code, out = _run([sys.executable, str(ROOT / "term-bridge" / "mac-screenshot.py"), "--caption", "TG /shot mac"])
            return out or "mac screenshot sent" if code == 0 else (out or f"failed ({code})")
        if platform in ("term", "terminal", "iterm"):
            code, out = _run([sys.executable, str(screenshot_script()), "--caption", "TG /shot term"])
            return out or "terminal screenshot sent" if code == 0 else (out or f"failed ({code})")
        return "usage: /shot android|ios|mac|term"

    if cmd == "/tap":
        parsed = _parse_tap(args)
        if not parsed:
            return "usage: /tap X Y [android|ios]"
        platform, x, y = parsed
        cli = "iphone-ctl" if platform == "ios" else "droid-ctl"
        code, out = _run([cli, "tap", str(x), str(y)])
        if code != 0:
            return out or f"tap failed ({code})"
        shot = "shot-ios.sh" if platform == "ios" else "shot-android.sh"
        _run([str(ROOT / "mob-compose" / "scripts" / shot), "-c", f"after tap {x},{y}"])
        return f"tap {x},{y} on {platform} — screenshot sent"

    if cmd == "/swipe" and len(args) >= 4:
        platform = args[4] if len(args) > 4 and args[4] in ("android", "ios") else "android"
        try:
            x1, y1, x2, y2 = map(int, args[:4])
        except ValueError:
            return "usage: /swipe X1 Y1 X2 Y2 [android|ios]"
        cli = "iphone-ctl" if platform == "ios" else "droid-ctl"
        code, out = _run([cli, "swipe", str(x1), str(y1), str(x2), str(y2)])
        if code != 0:
            return out or f"swipe failed ({code})"
        shot = "shot-ios.sh" if platform == "ios" else "shot-android.sh"
        _run([str(ROOT / "mob-compose" / "scripts" / shot), "-c", "after swipe"])
        return f"swipe on {platform} — screenshot sent"

    return f"unknown command: {cmd} (try /help)"


def main() -> int:
    parser = argparse.ArgumentParser(description="mobile-agent Telegram relay")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("message", nargs="?")
    args = parser.parse_args()

    _load_env()
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        print("TELEGRAM_BOT_TOKEN not set", file=sys.stderr)
        return 1

    if args.dry_run:
        msg = (args.message or "/help").strip()
        if msg.startswith("/"):
            print(_handle_command(msg))
        else:
            print(f"[dry-run] would inject to iTerm + inbox:\n{msg}")
        return 0

    # Single-instance guard: a second relay polling the same token gets a
    # getUpdates 409 and silently steals updates. Refuse instead.
    others = _other_relay_pids()
    if others:
        print(
            f"ERROR: another tg-relay is already running (pid {others[0]}). "
            "Two pollers conflict on getUpdates (409) — run ./mob tg-stop first.",
            file=sys.stderr,
        )
        return 1

    try:
        from telegram import (
            BotCommand,
            InlineKeyboardButton,
            InlineKeyboardMarkup,
            Update,
        )
        from telegram.ext import (
            Application,
            CallbackQueryHandler,
            CommandHandler,
            MessageHandler,
            filters,
        )
        from telegram.error import Conflict
    except ImportError:
        print("pip install python-telegram-bot", file=sys.stderr)
        return 1

    allowed = resolve_allowlist(
        os.environ.get("TG_RELAY_ALLOWED_CHAT_IDS", ""),
        os.environ.get("TELEGRAM_CHAT_ID", ""),
    )
    limiter = RateLimiter()
    if allowed:
        print(f"chat allow-list active: {sorted(allowed)}")
        if limiter.min_interval > 0:
            print(f"rate limit: 1 msg / {limiter.min_interval}s per chat")
    else:
        # Fail closed: this bot types into a live terminal running an agent with
        # bypassed permissions, so an empty allow-list must never mean "allow all".
        print(
            "ERROR: no chat allow-list — refusing to start. Set TELEGRAM_CHAT_ID "
            "(owner) or TG_RELAY_ALLOWED_CHAT_IDS to the chat(s) allowed to drive "
            "this bot.",
            file=sys.stderr,
        )
        return 1

    def _keyboard(rows):
        return InlineKeyboardMarkup(
            [[InlineKeyboardButton(label, callback_data=data)] for label, data in rows]
        )

    async def on_message(update: Update, context) -> None:
        if not update.message or not update.message.text:
            return
        chat_id = update.effective_chat.id if update.effective_chat else None
        if not is_allowed(chat_id, allowed):
            print(f"ignored message from unauthorized chat {chat_id}", file=sys.stderr)
            return
        if chat_id is not None and not limiter.allow(chat_id, time.monotonic()):
            print(f"rate-limited chat {chat_id}", file=sys.stderr)
            return
        # Strip control chars + cap length before anything reaches the terminal.
        text, truncated = sanitize_injection(update.message.text.strip())
        if not text:
            return
        chat_id = chat_id or 0
        if text.startswith("/"):
            base = text.strip().split()[0].lower().split("@")[0]
            if base == "/tab" and len(text.strip().split()) == 1:
                code, tabs = list_tabs()
                rows = tab_submenu([(t.window, t.tab, t.name) for t in tabs]) if code == 0 else []
                if rows:
                    await update.message.reply_text("选择默认 tab：", reply_markup=_keyboard(rows))
                    return
                await update.message.reply_text(_handle_command(text)[:4000])
                return
            sub = menu_for_command(text)
            if sub:
                await update.message.reply_text("请选择：", reply_markup=_keyboard(sub))
                return
            reply = _handle_command(text)
        else:
            reply = _handle_natural_language(chat_id, text)
        if truncated:
            reply = f"⚠️ 消息过长，已截断到 {len(text)} 字后注入\n{reply}"
        await update.message.reply_text(reply[:4000])

    async def on_media(update: Update, context) -> None:
        # Photo/document → save under inbox/attachments/ → inject the path so the
        # agent can read it. Same auth + rate-limit gate as text.
        msg = update.message
        if not msg:
            return
        chat_id = update.effective_chat.id if update.effective_chat else None
        if not is_allowed(chat_id, allowed):
            print(f"ignored media from unauthorized chat {chat_id}", file=sys.stderr)
            return
        if chat_id is not None and not limiter.allow(chat_id, time.monotonic()):
            print(f"rate-limited chat {chat_id}", file=sys.stderr)
            return
        chat_id = chat_id or 0
        try:
            from attachments import attachment_dir, dest_path, inject_text

            attachment_dir().mkdir(parents=True, exist_ok=True)
            if msg.photo:
                ph = msg.photo[-1]  # largest size
                tg_file = await context.bot.get_file(ph.file_id)
                dest = dest_path(ph.file_unique_id, ".jpg")
            elif msg.document:
                doc = msg.document
                tg_file = await context.bot.get_file(doc.file_id)
                ext = Path(doc.file_name or "").suffix or ".bin"
                dest = dest_path(doc.file_unique_id, ext)
            else:
                return
            await tg_file.download_to_drive(str(dest))
            reply = _handle_natural_language(chat_id, inject_text(str(dest), msg.caption or ""))
            await msg.reply_text(reply[:4000])
        except Exception as e:  # never crash the bot on a bad attachment
            await msg.reply_text(f"附件处理失败: {e}")

    async def on_callback(update: Update, context) -> None:
        q = update.callback_query
        if not q:
            return
        chat_id = update.effective_chat.id if update.effective_chat else None
        if not is_allowed(chat_id, allowed):
            await q.answer("未授权", show_alert=False)
            return
        await q.answer()
        reply = dispatch_callback(q.data or "", _handle_command)
        await q.edit_message_text(reply[:4000])

    async def start_cmd(update: Update, context) -> None:
        if not update.message:
            return
        chat_id = update.effective_chat.id if update.effective_chat else None
        if not is_allowed(chat_id, allowed):
            print(f"ignored /start from unauthorized chat {chat_id}", file=sys.stderr)
            return
        await update.message.reply_text(_handle_command("/help"))

    async def post_init(app) -> None:
        try:
            await app.bot.set_my_commands([BotCommand(c, d) for c, d in MENU_COMMANDS])
        except Exception as e:  # menu is a convenience; never block startup
            print(f"set_my_commands failed: {e}", file=sys.stderr)

    app = Application.builder().token(token).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("help", start_cmd))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    app.add_handler(MessageHandler(filters.COMMAND, on_message))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, on_media))
    print(f"mobile-agent tg-relay listening (root={ROOT})")
    print(_injection_banner())
    try:
        app.run_polling(allowed_updates=["message", "callback_query"])
    except Conflict:
        # Another consumer is polling the same token. Exit clearly so the
        # supervisor's burst-limit stops the loop instead of crash-spamming.
        print(
            "ERROR: getUpdates 409 Conflict — another instance is consuming "
            "updates. Run ./mob tg-stop, then restart.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
