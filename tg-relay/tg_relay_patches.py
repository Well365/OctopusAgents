"""Apply iTerm tab routing patches to tg-relay module."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "term-bridge"))

from iterm_route import format_tabs_message, parse_routed_message
from iterm_target import apply_target_env, resolve_target
from tg_format_config import VALID as _FORMATS, get_format, set_format


def _inject_iterm(text: str, target=None) -> tuple[int, str]:
    t = target or resolve_target()
    cmd = [sys.executable, str(ROOT / "term-bridge" / "iterm-inject.py")]
    if t.window is None:
        cmd.append("--front-window")
    else:
        cmd.extend(["--window", str(t.window)])
    cmd.extend(["--tab", str(t.tab)])
    if t.session is not None:
        cmd.extend(["--session", str(t.session)])
    cmd.append(text)
    try:
        r = subprocess.run(
            cmd,
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=120,
            env=apply_target_env(t),
        )
        return r.returncode, ((r.stdout or "") + (r.stderr or "")).strip()
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return 1, str(e)


def _schedule_iterm_monitor_poll(target=None) -> None:
    delay = os.environ.get("TG_ITERM_MONITOR_AFTER", "").strip()
    if not delay or delay.lower() in ("0", "false", "no", "off"):
        return
    try:
        secs = max(5, int(delay))
    except ValueError:
        secs = 30
    monitor = str(ROOT / "term-bridge" / "iterm-monitor.py")
    t = target or resolve_target()
    env = apply_target_env(t)
    env_str = repr(env)
    subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "import os, time, subprocess, sys; "
                f"env = {env_str}; "
                f"time.sleep({secs}); "
                f"subprocess.run([sys.executable, {monitor!r}, '--once', '--force'], env=env)"
            ),
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def apply(mod: ModuleType) -> None:
    def handle_natural_language(chat_id: int, text: str) -> str:
        target, body, hit = parse_routed_message(text)
        mod._append_inbox(chat_id, f"[{target.label()}] {body}")
        if mod._iterm_inject_enabled():
            if sys.platform != "darwin":
                return "saved to inbox (iTerm inject needs macOS)"
            code, out = _inject_iterm(body, target=target)
            if code == 0:
                _schedule_iterm_monitor_poll(target=target)
                preview = body[:80] + ("…" if len(body) > 80 else "")
                tab_note = f"tab {hit.tab} ({hit.name[:40]})" if hit else target.label()
                extra = ""
                if os.environ.get("TG_ITERM_MONITOR_AFTER", "").strip() not in ("", "0", "false", "no", "off"):
                    extra = "\n(output -> TG after delay)"
                return f"typed into iTerm {tab_note}\n{preview}\n(+ inbox backup){extra}"
            return f"inbox saved; iTerm failed:\n{out[:800]}"
        return "task queued — ./mob tg-inbox (set TG_RELAY_ITERM_INJECT=1 for iTerm)"

    orig_cmd = mod._handle_command

    def handle_command(text: str) -> str:
        text = text.strip()
        if not text:
            return "empty message"
        if not text.startswith("/"):
            return handle_natural_language(0, text)
        parts = text.split()
        cmd = parts[0].lower().split("@")[0]
        if cmd in ("/start", "/help"):
            return (
                "mobile-agent bot\n\n"
                "/check — environment check\n"
                "/shot android|ios — screenshot to Telegram\n"
                "/tap X Y [android|ios]\n"
                "/swipe X1 Y1 X2 Y2 [android|ios]\n"
                "/devices — list devices\n"
                "/tabs — list iTerm tabs + routing hints\n"
                "/format html|markdown|plain|screenshot — 回传格式\n\n"
                "Natural language -> iTerm + inbox\n"
                "  Prefix examples:\n"
                "  [t2] question — tab 2\n"
                "  [mobile-agent] question — match directory name\n"
                "  @t3: question — tab 3\n"
                "  (no prefix = .env default TG_ITERM_TAB)\n"
                "  TG_RELAY_ITERM_INJECT=1"
            )
        if cmd == "/tabs":
            return format_tabs_message()[:4000]
        if cmd.startswith("/format") or cmd.startswith("/fmt"):
            prefix = "/format" if cmd.startswith("/format") else "/fmt"
            glued = cmd[len(prefix):].lstrip("-_:")  # /format-html → html
            value = glued or (parts[1] if len(parts) > 1 else "")
            if not value:
                return (
                    f"当前回传格式: {get_format()}\n"
                    f"可选: {' | '.join(_FORMATS)}\n"
                    "用法: /format html  （也可 /format-markdown）"
                )
            norm = set_format(value)
            if norm is None:
                return f"未知格式: {value}\n可选: {' | '.join(_FORMATS)}"
            return f"✓ 回传格式已设为 {norm}（立即生效，无需重启）"
        return orig_cmd(text)

    mod._handle_natural_language = handle_natural_language
    mod._handle_command = handle_command

    orig_main = mod.main

    def main() -> int:
        import argparse

        parser = argparse.ArgumentParser(description="mobile-agent Telegram relay")
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("message", nargs="?")
        args = parser.parse_args()

        mod._load_env()
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        if not token:
            print("TELEGRAM_BOT_TOKEN not set", file=sys.stderr)
            return 1

        if args.dry_run:
            msg = (args.message or "/help").strip()
            if msg.startswith("/"):
                print(handle_command(msg))
            else:
                target, body, hit = parse_routed_message(msg)
                tab = f" ({hit.name})" if hit else ""
                print(f"[dry-run] would inject to iTerm {target.label()}{tab} + inbox:\n{body}")
            return 0

        return orig_main()

    mod.main = main
