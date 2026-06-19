#!/usr/bin/env python3
"""Telegram bot: receive device commands, execute via droid-ctl/ioskit, reply with results."""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "term-bridge"))
from chat_allowlist import is_allowed, resolve_allowlist  # noqa: E402
from tg_menu import MENU_COMMANDS, dispatch_callback, menu_for_command  # noqa: E402

INBOX_DIR = ROOT / "inbox"
INBOX_FILE = INBOX_DIR / "pending.txt"


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


def _iterm_inject_enabled() -> bool:
    v = os.environ.get("TG_RELAY_ITERM_INJECT", "").strip().lower()
    return v in ("1", "true", "yes", "on")


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
    return "task queued — ./mob tg-inbox (set TG_RELAY_ITERM_INJECT=1 for iTerm)"


def _parse_tap(args: list[str]) -> tuple[str, int, int] | None:
    if len(args) < 2:
        return None
    platform = args[2] if len(args) >= 3 and args[2] in ("android", "ios") else "android"
    try:
        return platform, int(args[0]), int(args[1])
    except ValueError:
        return None


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
            "mobile-agent bot\n\n"
            "/check — environment check\n"
            "/shot android|ios — screenshot to Telegram\n"
            "/tap X Y [android|ios]\n"
            "/swipe X1 Y1 X2 Y2 [android|ios]\n"
            "/devices — list devices\n\n"
            "Natural language -> iTerm (1st window) + inbox\n"
            "  (enable: TG_RELAY_ITERM_INJECT=1 in .env)"
        )

    if cmd == "/check":
        code, out = _run([str(ROOT / "mob-compose" / "compose"), "check"])
        return out[:4000] if out else f"exit {code}"

    if cmd == "/devices":
        lines = []
        for label, cli in (("Android", "droid-ctl"), ("iOS", "iphone-ctl")):
            code, out = _run([cli, "devices"])
            lines.append(f"{label}:\n{out or f'exit {code}'}")
        return "\n\n".join(lines)[:4000]

    if cmd == "/shot" and args:
        platform = args[0].lower()
        script = "shot-android.sh" if platform == "android" else "shot-ios.sh" if platform == "ios" else ""
        if not script:
            return "usage: /shot android|ios"
        code, out = _run([str(ROOT / "mob-compose" / "scripts" / script), "-c", f"TG /shot {platform}"])
        return out or f"{platform} screenshot sent" if code == 0 else (out or f"failed ({code})")

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
    except ImportError:
        print("pip install python-telegram-bot", file=sys.stderr)
        return 1

    allowed = resolve_allowlist(
        os.environ.get("TG_RELAY_ALLOWED_CHAT_IDS", ""),
        os.environ.get("TELEGRAM_CHAT_ID", ""),
    )
    allow_all = os.environ.get("TG_RELAY_ALLOW_ALL_CHATS", "").strip().lower() in (
        "1", "true", "yes", "on",
    )
    if allowed:
        print(f"chat allow-list active: {sorted(allowed)}")
    elif allow_all:
        print(
            "WARNING: TG_RELAY_ALLOW_ALL_CHATS set — bot accepts ALL chats; "
            "anyone who finds the bot can drive your terminal",
            file=sys.stderr,
        )
    else:
        # Fail closed: this bot types into a live terminal, so never accept
        # arbitrary chats by default.
        print(
            "ERROR: no chat allow-list — refusing to start. Set TELEGRAM_CHAT_ID "
            "(owner) or TG_RELAY_ALLOWED_CHAT_IDS, or TG_RELAY_ALLOW_ALL_CHATS=1 "
            "to explicitly allow all chats.",
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
        text = update.message.text.strip()
        chat_id = chat_id or 0
        if text.startswith("/"):
            sub = menu_for_command(text)
            if sub:
                await update.message.reply_text("请选择：", reply_markup=_keyboard(sub))
                return
            reply = _handle_command(text)
        else:
            reply = _handle_natural_language(chat_id, text)
        await update.message.reply_text(reply[:4000])

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
    print(f"mobile-agent tg-relay listening (root={ROOT})")
    app.run_polling(allowed_updates=["message", "callback_query"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
