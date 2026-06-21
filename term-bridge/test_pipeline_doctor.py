#!/usr/bin/env python3
"""Unit tests for pipeline_doctor — hermetic (no daemons, network, or osascript)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pipeline_doctor as pd  # noqa: E402
from pipeline_doctor import Check  # noqa: E402


# --------------------------------------------------------------------------- #
# Check dataclass
# --------------------------------------------------------------------------- #
def test_check_is_frozen() -> None:
    c = Check("x", "pass", "ok")
    with pytest.raises(Exception):  # FrozenInstanceError subclasses Exception
        c.status = "fail"  # type: ignore[misc]


def test_check_defaults_fix_none() -> None:
    assert Check("x", "pass", "ok").fix is None


# --------------------------------------------------------------------------- #
# Token shape
# --------------------------------------------------------------------------- #
def test_token_missing_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    c = pd._check_token()
    assert c.status == "fail"


def test_token_malformed_warns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "not-a-real-token")
    assert pd._check_token().status == "warn"


def test_token_valid_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456789:" + "A" * 35)
    c = pd._check_token()
    assert c.status == "pass"


def test_token_value_never_printed(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "987654321:" + "Z" * 35
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", secret)
    c = pd._check_token()
    assert secret not in c.detail and (c.fix is None or secret not in c.fix)


# --------------------------------------------------------------------------- #
# Chat id
# --------------------------------------------------------------------------- #
def test_chat_id_missing_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    assert pd._check_chat_id().status == "fail"


def test_chat_id_positive_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
    assert pd._check_chat_id().status == "pass"


def test_chat_id_negative_warns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-100123")
    assert pd._check_chat_id().status == "warn"


def test_chat_id_nonint_warns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "abc")
    assert pd._check_chat_id().status == "warn"


# --------------------------------------------------------------------------- #
# Allowlist (fail-closed)
# --------------------------------------------------------------------------- #
def test_allowlist_empty_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TG_RELAY_ALLOWED_CHAT_IDS", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    assert pd._check_allowlist().status == "fail"


def test_allowlist_from_owner_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TG_RELAY_ALLOWED_CHAT_IDS", raising=False)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "555")
    assert pd._check_allowlist().status == "pass"


def test_allowlist_explicit_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TG_RELAY_ALLOWED_CHAT_IDS", "1,2,3")
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    assert pd._check_allowlist().status == "pass"


# --------------------------------------------------------------------------- #
# Inject
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("val", ["1", "true", "yes", "on", "ON", "True"])
def test_inject_on_passes(monkeypatch: pytest.MonkeyPatch, val: str) -> None:
    monkeypatch.setenv("TG_RELAY_ITERM_INJECT", val)
    assert pd._check_inject().status == "pass"


@pytest.mark.parametrize("val", ["", "0", "false", "no", "off"])
def test_inject_off_warns(monkeypatch: pytest.MonkeyPatch, val: str) -> None:
    monkeypatch.setenv("TG_RELAY_ITERM_INJECT", val)
    assert pd._check_inject().status == "warn"


# --------------------------------------------------------------------------- #
# Backend
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("val", ["", "iterm", "terminal", "ITERM"])
def test_backend_known_passes(monkeypatch: pytest.MonkeyPatch, val: str) -> None:
    monkeypatch.setenv("TG_TERM_BACKEND", val)
    assert pd._check_backend().status == "pass"


def test_backend_unknown_warns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TG_TERM_BACKEND", "kitty")
    assert pd._check_backend().status == "warn"


# --------------------------------------------------------------------------- #
# Daemon liveness (monkeypatched os.kill / pidfile)
# --------------------------------------------------------------------------- #
def test_daemon_no_pidfile_fails(tmp_path: Path) -> None:
    c = pd._daemon_check("d", tmp_path / "missing.pid")
    assert c.status == "fail"


def test_daemon_alive_passes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pidfile = tmp_path / "d.pid"
    pidfile.write_text("4242", encoding="utf-8")
    monkeypatch.setattr(pd.os, "kill", lambda pid, sig: None)
    c = pd._daemon_check("d", pidfile)
    assert c.status == "pass" and "4242" in c.detail


def test_daemon_dead_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pidfile = tmp_path / "d.pid"
    pidfile.write_text("4242", encoding="utf-8")

    def _boom(pid: int, sig: int) -> None:
        raise ProcessLookupError

    monkeypatch.setattr(pd.os, "kill", _boom)
    assert pd._daemon_check("d", pidfile).status == "fail"


def test_daemon_garbage_pidfile_fails(tmp_path: Path) -> None:
    pidfile = tmp_path / "d.pid"
    pidfile.write_text("not-a-pid", encoding="utf-8")
    assert pd._daemon_check("d", pidfile).status == "fail"


# --------------------------------------------------------------------------- #
# Target reachable (list_tabs / platform monkeypatched)
# --------------------------------------------------------------------------- #
def test_target_non_darwin_warns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pd.sys, "platform", "linux")
    assert pd._check_target().status == "warn"


def test_target_tabs_present_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pd.sys, "platform", "darwin")
    import iterm_route

    monkeypatch.setattr(iterm_route, "list_tabs", lambda: (0, ["tab1", "tab2"]))
    c = pd._check_target()
    assert c.status == "pass" and "2" in c.detail


def test_target_zero_tabs_warns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pd.sys, "platform", "darwin")
    import iterm_route

    monkeypatch.setattr(iterm_route, "list_tabs", lambda: (0, []))
    assert pd._check_target().status == "warn"


def test_target_enumeration_error_warns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pd.sys, "platform", "darwin")
    import iterm_route

    def _boom() -> tuple[int, list]:
        raise RuntimeError("no iTerm")

    monkeypatch.setattr(iterm_route, "list_tabs", _boom)
    assert pd._check_target().status == "warn"


# --------------------------------------------------------------------------- #
# Automation permission (probe monkeypatched — never real osascript)
# --------------------------------------------------------------------------- #
def test_automation_non_darwin_warns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pd.sys, "platform", "linux")
    assert pd._check_automation().status == "warn"


def test_automation_granted_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pd.sys, "platform", "darwin")
    monkeypatch.setattr(
        pd.access_hint, "probe_automation_permission", lambda app: (True, "ok")
    )
    assert pd._check_automation().status == "pass"


def test_automation_denied_fails_with_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pd.sys, "platform", "darwin")
    monkeypatch.setattr(
        pd.access_hint,
        "probe_automation_permission",
        lambda app: (False, pd.access_hint.ACCESS_HINT),
    )
    c = pd._check_automation()
    assert c.status == "fail" and c.fix == pd.access_hint.ACCESS_HINT


def test_automation_probe_raises_warns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pd.sys, "platform", "darwin")

    def _boom(app: str) -> tuple[bool, str]:
        raise OSError("osascript gone")

    monkeypatch.setattr(pd.access_hint, "probe_automation_permission", _boom)
    assert pd._check_automation().status == "warn"


# --------------------------------------------------------------------------- #
# format_report rendering
# --------------------------------------------------------------------------- #
def _sample_checks() -> list[Check]:
    return [
        Check("A", "pass", "good"),
        Check("B", "warn", "careful", fix="do x"),
        Check("C", "fail", "broken", fix="run y"),
    ]


def test_format_report_summary_counts() -> None:
    out = pd.format_report(_sample_checks())
    assert "管道自检: 1❌ 1⚠️ 1✅" in out


def test_format_report_emoji_prefixes() -> None:
    out = pd.format_report(_sample_checks())
    assert "✅ A: good" in out
    assert "⚠️ B: careful" in out
    assert "❌ C: broken" in out


def test_format_report_includes_fix_lines() -> None:
    out = pd.format_report(_sample_checks())
    assert "↳ do x" in out and "↳ run y" in out
    # A passes with no fix → the line right after it must be the next check, not a fix.
    lines = out.splitlines()
    a_idx = lines.index("✅ A: good")
    assert lines[a_idx + 1] == "⚠️ B: careful"


def test_format_report_header_first_line() -> None:
    out = pd.format_report(_sample_checks(), header="HEAD")
    assert out.splitlines()[0] == "HEAD"
    assert out.splitlines()[1].startswith("管道自检:")


def test_format_report_empty_checks() -> None:
    out = pd.format_report([])
    assert out == "管道自检: 0❌ 0⚠️ 0✅"


# --------------------------------------------------------------------------- #
# health_summary
# --------------------------------------------------------------------------- #
def test_health_summary_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pd, "load_env", lambda: None)
    monkeypatch.setattr(pd, "_alive_word", lambda pidfile: "✅")
    monkeypatch.setattr(pd, "_last_reply_line", lambda: "上次回传: 无记录")
    monkeypatch.setenv("TG_RELAY_ITERM_INJECT", "1")
    monkeypatch.setenv("TG_TERM_BACKEND", "iterm")
    out = pd.health_summary()
    lines = out.splitlines()
    assert 2 <= len(lines) <= 4
    assert "relay ✅" in out and "monitor ✅" in out
    assert "注入: 开" in out and "后端: iterm" in out
    assert "上次回传: 无记录" in out


def test_last_reply_line_no_files(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(pd, "INBOX_DIR", tmp_path)
    assert pd._last_reply_line() == "上次回传: 无记录"


def test_last_reply_line_recent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import time as _t

    f = tmp_path / "iterm-monitor-w1-t1.last-sent-at"
    f.write_text(str(_t.time() - 120), encoding="utf-8")  # 2 min ago
    monkeypatch.setattr(pd, "INBOX_DIR", tmp_path)
    line = pd._last_reply_line()
    assert line.startswith("上次回传:") and "分钟前" in line
    assert "2 分钟前" in line


# --------------------------------------------------------------------------- #
# run_checks / main
# --------------------------------------------------------------------------- #
def test_run_checks_returns_checks_and_never_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pd, "load_env", lambda: None)
    # Force darwin probes onto the safe non-darwin / mocked paths.
    monkeypatch.setattr(pd.sys, "platform", "linux")
    checks = pd.run_checks()
    assert checks and all(isinstance(c, Check) for c in checks)
    assert all(c.status in ("pass", "warn", "fail") for c in checks)


def test_main_returns_1_when_any_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pd, "run_checks", lambda: [Check("x", "fail", "boom")])
    assert pd.main() == 1


def test_main_returns_0_when_no_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        pd, "run_checks", lambda: [Check("x", "pass", "ok"), Check("y", "warn", "meh")]
    )
    assert pd.main() == 0
