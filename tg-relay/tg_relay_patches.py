"""Apply iTerm tab routing patches to tg-relay module."""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "term-bridge"))

import term_backend
from iterm_route import format_tabs_message, list_tabs, parse_routed_message
from iterm_target import apply_target_env, resolve_target
from tg_format_config import VALID as _FORMATS, get_format, set_format
from tg_new_command import SpawnResult, retarget_env


def _is_slash_command(text: str) -> bool:
    """Slash commands need Return twice — the TUI's autocomplete menu eats the first."""
    return text.lstrip().startswith("/")


def _inject_iterm(text: str, target=None, enter_twice: bool | None = None) -> tuple[int, str]:
    t = target or resolve_target()
    if enter_twice is None:
        enter_twice = _is_slash_command(text)
    cmd = [sys.executable, str(term_backend.inject_script())]
    if t.window is None:
        cmd.append("--front-window")
    else:
        cmd.extend(["--window", str(t.window)])
    cmd.extend(["--tab", str(t.tab)])
    if t.session is not None:
        cmd.extend(["--session", str(t.session)])
    if enter_twice:
        # slash commands: clear any leftover input first (no concatenation),
        # and press Return twice (autocomplete eats the first).
        cmd.append("--enter-twice")
        cmd.append("--clear-line")
    cmd.append(text)
    try:
        r = subprocess.run(
            cmd,
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=120,
            env=apply_target_env(t),
            stdin=subprocess.DEVNULL,  # daemon fd0 may be closed → child Python would crash
        )
        return r.returncode, ((r.stdout or "") + (r.stderr or "")).strip()
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return 1, str(e)


def _inject_key(key: str, target=None) -> tuple[int, str]:
    t = target or resolve_target()
    cmd = [sys.executable, str(term_backend.inject_script())]
    if t.window is None:
        cmd.append("--front-window")
    else:
        cmd.extend(["--window", str(t.window)])
    cmd.extend(["--tab", str(t.tab), "--key", key])
    try:
        r = subprocess.run(
            cmd, cwd=ROOT, capture_output=True, text=True, timeout=30,
            env=apply_target_env(t), stdin=subprocess.DEVNULL,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return 1, str(e)
    return r.returncode, ((r.stdout or "") + (r.stderr or "")).strip()


# Secrets that must never land in a child's argv or be inherited by children
# that don't need them. Children that DO need the token (iterm-monitor) re-read
# it from .env via their own _load_env(), so dropping it here is safe.
_SECRET_ENV_KEYS = ("TELEGRAM_BOT_TOKEN",)


def _sanitized_env(env: dict[str, str]) -> dict[str, str]:
    """Return a copy of env with secret keys removed (see _SECRET_ENV_KEYS)."""
    return {k: v for k, v in env.items() if k not in _SECRET_ENV_KEYS}


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
    # Drop the token from the env: it must not appear in argv (it never did via
    # -c now) nor in this child's environment. iterm-monitor re-reads it from
    # .env. The grandchild inherits this sanitized env (no env= passed below).
    env = _sanitized_env(apply_target_env(t))
    env["ITERM_MONITOR_SUFFIX"] = t.log_suffix()
    subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "import time, subprocess, sys; "
                f"time.sleep({secs}); "
                f"subprocess.run([sys.executable, {monitor!r}, '--once', '--force'])"
            ),
        ],
        env=env,
        stdin=subprocess.DEVNULL,  # detached child Python needs a valid fd0
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def _schedule_confirm_enter(target=None, delay: float | None = None) -> None:
    """Press Enter once after a short delay to accept a confirmation dialog.

    Some slash commands (notably /model) pop a "Switch model? 1.Yes 2.No" prompt
    with Yes highlighted; the in-line double-Return fires before the dialog renders,
    so this delayed Enter lands on the dialog and accepts the default.
    Delay via TG_MODEL_CONFIRM_DELAY (seconds, default 1.3).
    """
    if delay is None:
        try:
            delay = float(os.environ.get("TG_MODEL_CONFIRM_DELAY", "1.3"))
        except ValueError:
            delay = 1.3
    t = target or resolve_target()
    # Pressing Enter needs no token; strip it and let the child inherit the env.
    env = _sanitized_env(apply_target_env(t))
    cmd = [str(term_backend.inject_script())]
    if t.window is None:
        cmd.append("--front-window")
    else:
        cmd.extend(["--window", str(t.window)])
    cmd.extend(["--tab", str(t.tab), "--key", "enter"])
    subprocess.Popen(
        [
            sys.executable, "-c",
            "import time, subprocess, sys; "
            f"time.sleep({float(delay)}); "
            f"subprocess.run([sys.executable] + {cmd!r}, stdin=subprocess.DEVNULL)",
        ],
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def _parse_spawn_output(code: int, stdout: str, stderr: str) -> SpawnResult:
    tab = None
    workdir = ""
    m = re.search(r"^tab=(\d+)$", stdout or "", re.M)
    if m:
        tab = int(m.group(1))
    d = re.search(r"^dir=(.+)$", stdout or "", re.M)
    if d:
        workdir = d.group(1).strip()
    raw = ((stdout or "") + (stderr or "")).strip()
    return SpawnResult(code=code, tab=tab, workdir=workdir, raw=raw)


def _spawn_session(agent_key: str, prompt: str) -> SpawnResult:
    cmd = [sys.executable, str(ROOT / "term-bridge" / "terminal-spawn.py"), "--agent", agent_key]
    if prompt:
        cmd.extend(["--prompt", prompt])
    try:
        r = subprocess.run(
            cmd, cwd=ROOT, capture_output=True, text=True, timeout=60,
            stdin=subprocess.DEVNULL,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return _parse_spawn_output(1, "", str(e))
    return _parse_spawn_output(r.returncode, r.stdout or "", r.stderr or "")


def apply(mod: ModuleType) -> None:
    def handle_natural_language(chat_id: int, text: str) -> str:
        target, body, hit = parse_routed_message(text)
        mod._append_inbox(chat_id, f"[{target.label()}] {body}")
        if mod._iterm_inject_enabled():
            if sys.platform != "darwin":
                return "saved to inbox (iTerm inject needs macOS)"
            backend_name = "iTerm" if term_backend.resolve_backend() == "iterm" else "Terminal"
            code, out = _inject_iterm(body, target=target)
            if code == 0:
                _schedule_iterm_monitor_poll(target=target)
                preview = body[:80] + ("…" if len(body) > 80 else "")
                tab_note = f"tab {hit.tab} ({hit.name[:40]})" if hit else target.label()
                extra = ""
                if os.environ.get("TG_ITERM_MONITOR_AFTER", "").strip() not in ("", "0", "false", "no", "off"):
                    extra = "\n(output -> TG after delay)"
                return f"typed into {backend_name} {tab_note}\n{preview}\n(+ inbox backup){extra}"
            return f"inbox saved; {backend_name} failed:\n{out[:800]}"
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
                "/new claude|codex [prompt] — 新 tab 启动 agent 会话\n"
                "/format html|markdown|plain|screenshot — 回传格式\n"
                "/stop /reset /compact /model /think — 控制当前会话\n\n"
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
        if cmd == "/new":
            from tg_new_command import handle_new
            reply, new_tab = handle_new(
                parts[1:], is_macos=(sys.platform == "darwin"), spawn=_spawn_session
            )
            os.environ.update(retarget_env(new_tab))
            return reply
        if cmd == "/tab":
            from tg_tab_command import resolve_tab_command
            from target_default import write_default, clear_default
            return resolve_tab_command(
                parts[1:], list_tabs, write_default, clear_default
            )
        if cmd == "/p":
            from quick_prompts import load, save_prompt, delete_prompt, resolve_p_command
            reply, inject_text = resolve_p_command(
                parts[1:], load(), save_prompt, delete_prompt
            )
            if inject_text is not None:
                return handle_natural_language(0, inject_text)
            return reply
        if cmd in ("/stop", "/interrupt", "/reset", "/compact", "/model", "/think"):
            from tg_session_control import resolve_session_command, session_usage
            arg = parts[1] if len(parts) > 1 else ""
            action = resolve_session_command(cmd, arg)
            if action is None:
                return session_usage(cmd)
            if sys.platform != "darwin":
                return "会话控制需要 macOS"
            if action.kind == "key":
                code, out = _inject_key(action.payload)
            else:
                code, out = _inject_iterm(action.payload, target=resolve_target())
                # /model pops a "Switch model?" confirmation — auto-accept default (Yes)
                if code == 0 and cmd == "/model":
                    _schedule_confirm_enter(target=resolve_target())
            if code == 0:
                return f"✓ 已发送 {cmd} → {action.payload}"
            return f"会话控制失败:\n{out[:800]}"
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
