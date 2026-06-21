#!/usr/bin/env python3
"""Pipeline health-check ("doctor") for the mob-remote Telegram → terminal bridge.

This is the SINGLE source of truth for three consumers:
  - Telegram ``/check`` command — full pipeline report (``run_checks`` / ``format_report``)
  - ``/status`` header — short health line (``health_summary``)
  - ``mob config-check`` CLI — validate config + print resolved settings (``main``)

Unlike the mobile-device doctor (Android/iOS/WDA), these checks tell a stuck
operator whether the *pipeline* is actually wired up: relay daemon running,
monitor daemon running, token / chat-id validity, fail-closed allowlist,
injection enabled, backend valid, a target window to type into, and macOS
automation permission.

Design rules:
  - Every check is best-effort: a failing probe returns a ``Check`` (never raises),
    so one broken step can never crash the whole report.
  - The Telegram token value is NEVER printed (only its presence / shape).
  - Pure data: :class:`Check` is a frozen dataclass; nothing is mutated in place.
"""
from __future__ import annotations

import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "term-bridge"))

import access_hint  # noqa: E402
from chat_allowlist import resolve_allowlist  # noqa: E402
from term_backend import resolve_backend  # noqa: E402

INBOX_DIR = ROOT / "inbox"
RELAY_PIDFILE = INBOX_DIR / "tg-relay-daemon.pid"
MONITOR_PIDFILE = INBOX_DIR / "iterm-monitor-daemon.pid"

# Telegram bot token shape: "<bot_id>:<secret>" (secret is 35 url-safe chars).
_TOKEN_RE = re.compile(r"^\d+:[A-Za-z0-9_-]{30,}$")

# Active backend → the macOS application name AppleScript drives.
_BACKEND_APP = {"iterm": "iTerm", "terminal": "Terminal"}

_STATUS_EMOJI = {"pass": "✅", "warn": "⚠️", "fail": "❌"}


@dataclass(frozen=True)
class Check:
    """One pipeline probe result. ``status`` is "pass" | "warn" | "fail"."""

    name: str
    status: str
    detail: str
    fix: str | None = None


def load_env() -> None:
    """Load ROOT/.env into os.environ without overwriting already-set vars.

    Mirrors tg-relay/tg-relay.py ``_load_env``: honors TGKIT_ENV_FILE (which
    wins over ROOT/.env), uses ``setdefault`` so the live environment is never
    clobbered, and stops after the first readable file.
    """
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


# --------------------------------------------------------------------------- #
# Low-level probes (pure / best-effort)
# --------------------------------------------------------------------------- #
def _read_pid(pidfile: Path) -> int | None:
    """Read an integer pid from *pidfile*, or None if absent/garbage."""
    try:
        raw = pidfile.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _pid_alive(pid: int) -> bool:
    """True when signal 0 to *pid* succeeds (process exists & is reachable)."""
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists but owned by another user
    except OSError:
        return False


def _iterm_inject_enabled() -> bool:
    """Replicate tg-relay.py ``_iterm_inject_enabled`` semantics at read time.

    We read the *observed* behavior of the relay rather than hard-coding it, so
    the doctor stays correct even if another agent changes the truthy set.
    """
    v = os.environ.get("TG_RELAY_ITERM_INJECT", "").strip().lower()
    return v in ("1", "true", "yes", "on")


def _daemon_check(name: str, pidfile: Path) -> Check:
    """Generic pidfile + ``kill(pid, 0)`` liveness probe for a daemon."""
    pid = _read_pid(pidfile)
    if pid is None:
        return Check(name, "fail", "未运行（无 pidfile）", fix="启动: ./mob up")
    if _pid_alive(pid):
        return Check(name, "pass", f"运行中 pid={pid}")
    return Check(name, "fail", f"pidfile 存在但进程已退出 (pid={pid})", fix="启动: ./mob up")


# --------------------------------------------------------------------------- #
# Individual checks
# --------------------------------------------------------------------------- #
def _check_relay() -> Check:
    return _daemon_check("relay 守护进程", RELAY_PIDFILE)


def _check_monitor() -> Check:
    return _daemon_check("monitor 守护进程", MONITOR_PIDFILE)


def _check_token() -> Check:
    """TELEGRAM_BOT_TOKEN present + well-formed. Never prints the value."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        return Check("Bot Token", "fail", "未设置", fix="在 .env 设置 TELEGRAM_BOT_TOKEN")
    if not _TOKEN_RE.match(token):
        return Check(
            "Bot Token", "warn", "格式可疑（应为 <id>:<secret>）",
            fix="从 @BotFather 复制完整 token 到 .env",
        )
    return Check("Bot Token", "pass", "已设置且格式正确")


def _check_chat_id() -> Check:
    """TELEGRAM_CHAT_ID present + a positive integer (private chat, not a group)."""
    raw = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not raw:
        return Check(
            "Chat ID", "fail", "未设置",
            fix="发消息给 bot 后用 ./mob tg-chat-id 获取，写入 .env",
        )
    try:
        value = int(raw)
    except ValueError:
        return Check("Chat ID", "warn", f"不是整数: {raw!r}", fix="应为正整数私聊 chat_id")
    if value <= 0:
        return Check(
            "Chat ID", "warn", "是负数（那是群 ID）",
            fix="本 bot 需要私聊正数 chat_id，不要用群 ID",
        )
    return Check("Chat ID", "pass", "私聊正数 chat_id")


def _check_allowlist() -> Check:
    """Fail-closed allowlist: relay refuses to start when it resolves empty."""
    allowed = resolve_allowlist(
        os.environ.get("TG_RELAY_ALLOWED_CHAT_IDS", ""),
        os.environ.get("TELEGRAM_CHAT_ID", ""),
    )
    if not allowed:
        return Check(
            "白名单", "fail", "为空 → relay 会拒绝启动（fail-closed）",
            fix="设置 TELEGRAM_CHAT_ID 或 TG_RELAY_ALLOWED_CHAT_IDS",
        )
    return Check("白名单", "pass", f"{len(allowed)} 个授权 chat")


def _check_inject() -> Check:
    """Injection on/off (warn when off — messages won't reach the terminal)."""
    if _iterm_inject_enabled():
        return Check("终端注入", "pass", "已开启")
    return Check(
        "终端注入", "warn", "已关闭（消息不会注入终端，只存 inbox）",
        fix="在 .env 设置 TG_RELAY_ITERM_INJECT=1",
    )


def _check_backend() -> Check:
    """Backend valid: TG_TERM_BACKEND empty or in {iterm, terminal}."""
    raw = os.environ.get("TG_TERM_BACKEND", "").strip().lower()
    backend = resolve_backend()
    if raw and raw not in ("iterm", "terminal"):
        return Check(
            "终端后端", "warn", f"未知值 {raw!r}，已回退到 {backend}",
            fix="TG_TERM_BACKEND 仅支持 iterm 或 terminal",
        )
    return Check("终端后端", "pass", f"{backend}")


def _check_target() -> Check:
    """At least one tab of the active backend is open to inject into."""
    if sys.platform != "darwin":
        return Check("目标窗口", "warn", "非 macOS，无法枚举终端窗口")
    try:
        from iterm_route import list_tabs

        code, tabs = list_tabs()
    except Exception as exc:  # never let tab enumeration crash the report
        return Check("目标窗口", "warn", f"无法枚举终端标签: {exc}")
    if code != 0:
        return Check("目标窗口", "warn", "无法读取终端标签列表")
    if not tabs:
        return Check(
            "目标窗口", "warn", "没有可注入的终端窗口",
            fix="用 /new 新建或 /tab 选择一个终端",
        )
    return Check("目标窗口", "pass", f"{len(tabs)} 个可用标签")


def _check_automation() -> Check:
    """macOS Automation permission for the active backend's app (kept LAST).

    Best-effort: ``probe_automation_permission`` shells out to osascript, so the
    whole call is wrapped — any failure degrades to a warn, never a crash.
    """
    if sys.platform != "darwin":
        return Check("自动化权限", "warn", "非 macOS，跳过")
    app = _BACKEND_APP.get(resolve_backend(), "Terminal")
    try:
        ok, message = access_hint.probe_automation_permission(app)
    except Exception as exc:  # osascript is fragile; degrade, don't crash
        return Check("自动化权限", "warn", f"探测失败: {exc}")
    if ok:
        return Check("自动化权限", "pass", f"已授权控制 {app}")
    return Check("自动化权限", "fail", f"未授权控制 {app}: {message}", fix=access_hint.ACCESS_HINT)


def run_checks() -> list[Check]:
    """Run every pipeline check and return them in display order.

    Loads .env first so checks see the resolved config. Each check is itself
    best-effort, but we still guard the dispatch so a bug in one probe cannot
    drop the rest of the report.
    """
    load_env()
    probes = (
        _check_relay,
        _check_monitor,
        _check_token,
        _check_chat_id,
        _check_allowlist,
        _check_inject,
        _check_backend,
        _check_target,
        _check_automation,  # LAST: shells out to osascript
    )
    results: list[Check] = []
    for probe in probes:
        try:
            results.append(probe())
        except Exception as exc:  # belt-and-braces: a probe must never abort
            results.append(Check(probe.__name__, "warn", f"检查异常: {exc}"))
    return results


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
def _summary_line(checks: list[Check]) -> str:
    """One-line tally, e.g. "管道自检: 2❌ 1⚠️ 6✅"."""
    fails = sum(1 for c in checks if c.status == "fail")
    warns = sum(1 for c in checks if c.status == "warn")
    passes = sum(1 for c in checks if c.status == "pass")
    return f"管道自检: {fails}❌ {warns}⚠️ {passes}✅"


def format_report(checks: list[Check], *, header: str | None = None) -> str:
    """Compact, phone-friendly report.

    Each line is ``<emoji> <name>: <detail>``; a present ``fix`` follows on its
    own indented line. Output starts with the summary tally (and an optional
    *header* above it).
    """
    lines: list[str] = []
    if header:
        lines.append(header)
    lines.append(_summary_line(checks))
    for c in checks:
        emoji = _STATUS_EMOJI.get(c.status, "•")
        lines.append(f"{emoji} {c.name}: {c.detail}")
        if c.fix:
            lines.append(f"   ↳ {c.fix}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Short /status header
# --------------------------------------------------------------------------- #
def _last_reply_line() -> str:
    """"上次回传: N 分钟前" from the freshest iterm-monitor-*.last-sent-at, else 无记录."""
    try:
        files = list(INBOX_DIR.glob("iterm-monitor-*.last-sent-at"))
    except OSError:
        files = []
    if not files:
        return "上次回传: 无记录"
    newest = max(files, key=lambda p: p.stat().st_mtime)
    try:
        ts = float(newest.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        ts = newest.stat().st_mtime
    minutes = max(0, int((time.time() - ts) // 60))
    return f"上次回传: {minutes} 分钟前"


def _alive_word(pidfile: Path) -> str:
    pid = _read_pid(pidfile)
    return "✅" if pid is not None and _pid_alive(pid) else "❌"


def health_summary() -> str:
    """Short (2-4 line) header for /status: daemons, inject, backend, last reply."""
    load_env()
    relay = _alive_word(RELAY_PIDFILE)
    monitor = _alive_word(MONITOR_PIDFILE)
    inject = "开" if _iterm_inject_enabled() else "关"
    return "\n".join(
        [
            f"relay {relay}  monitor {monitor}",
            f"注入: {inject}  后端: {resolve_backend()}",
            _last_reply_line(),
        ]
    )


def main() -> int:
    """CLI entry: print the full report; exit 1 if any check failed, else 0."""
    checks = run_checks()
    print(format_report(checks))
    return 1 if any(c.status == "fail" for c in checks) else 0


if __name__ == "__main__":
    raise SystemExit(main())
