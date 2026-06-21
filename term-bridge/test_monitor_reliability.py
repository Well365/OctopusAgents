"""Reliability/failure-visibility tests for the iTerm/Terminal output monitor.

All tests exercise pure/extractable logic — no real terminal, no network. Where a
side effect is unavoidable, ``_send_tg`` is monkeypatched. The two import shims
load the hyphenated module files (``iterm-monitor.py`` / ``iterm-screenshot.py``)
without executing their argparse ``main``.
"""
from __future__ import annotations

import hashlib
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_DIR))


def _load_module(filename: str, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, _DIR / filename)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


mon = _load_module("iterm-monitor.py", "iterm_monitor_under_test")
shot = _load_module("iterm-screenshot.py", "iterm_screenshot_under_test")


# --- is_target_lost_error classifier --------------------------------------

@pytest.mark.parametrize(
    "text",
    [
        'execution error: Window not found (-1719)',
        "Tab not found",
        'error "No iTerm window open"',
        "No Terminal window open",
        "No iTerm running",
        "No Terminal running",
        "invalid window id: 'x'",
        "session not found for id ABC",
        "WINDOW NOT FOUND",  # case-insensitive
        "boom -1728 can't get object",
    ],
)
def test_target_lost_error_detected(text: str) -> None:
    assert mon.is_target_lost_error(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "",
        "osascript timed out",
        "Connection reset by peer",
        "some transient capture hiccup",
        "screencapture: cannot run",  # screenshot perm, not a lost target
    ],
)
def test_non_target_lost_error_ignored(text: str) -> None:
    assert mon.is_target_lost_error(text) is False


# --- sha1 mark round-trip / equality --------------------------------------

def test_sha1_token_is_stable_and_short() -> None:
    text = "a" * 2_000_000  # the marks used to persist ~1-2MB of raw text
    token = mon.sha1_token(text)
    assert token == hashlib.sha1(text.encode("utf-8")).hexdigest()
    assert len(token) == 40
    assert mon.sha1_token(text) == token  # deterministic


def test_sha1_token_distinguishes_changed_text() -> None:
    assert mon.sha1_token("hello") != mon.sha1_token("hello!")
    assert mon.sha1_token("") == mon.sha1_token("")


def test_state_mark_round_trip_is_equality_only(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # _write_state hashes; a re-read compares equal to sha1_token(same text)
    # and unequal once the capture changes.
    monkeypatch.setattr(mon, "_monitor_file", lambda kind: tmp_path / f"m.{kind}")
    capture = "line1\nCrunched for 3s\n" * 50
    mon._write_state(capture)
    stored = mon._read_state()
    assert stored == mon.sha1_token(capture)
    assert len(stored) == 40  # never the raw 1-2MB blob
    assert stored != mon.sha1_token(capture + "more")


def test_auto_default_mark_round_trip_is_equality_only(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(mon, "_monitor_file", lambda kind: tmp_path / f"m.{kind}")
    stable_key = "menu\n1. a\n2. b\n" * 20
    mon._write_auto_default_mark(stable_key)
    stored = mon._read_auto_default_mark()
    assert stored == mon.sha1_token(stable_key)
    assert len(stored) == 40
    # The loop compares the stored token against sha1_token(stable_key).
    assert stored == mon.sha1_token(stable_key)
    assert stored != mon.sha1_token(stable_key + "x")


# --- one-shot / dedup alert decision --------------------------------------

def test_should_alert_once_fires_only_when_unmarked() -> None:
    assert mon.should_alert_once(condition=True, already_marked=False) is True
    assert mon.should_alert_once(condition=True, already_marked=True) is False
    assert mon.should_alert_once(condition=False, already_marked=False) is False
    assert mon.should_alert_once(condition=False, already_marked=True) is False


def test_target_lost_alert_is_one_shot_per_episode(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(mon, "_monitor_file", lambda kind: tmp_path / f"m.{kind}")
    sent: list[str] = []
    monkeypatch.setattr(mon, "_send_tg", lambda text, fmt="html": (sent.append(text) or (0, "ok")))

    mon._alert_target_lost()
    mon._alert_target_lost()
    mon._alert_target_lost()
    assert sent == [mon._TARGET_LOST_ALERT]  # exactly one alert per outage

    # Recovery clears the mark; a new outage re-alerts.
    mon._clear_target_lost()
    mon._alert_target_lost()
    assert sent == [mon._TARGET_LOST_ALERT, mon._TARGET_LOST_ALERT]


def test_target_lost_alert_retries_when_send_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(mon, "_monitor_file", lambda kind: tmp_path / f"m.{kind}")
    calls: list[int] = []

    def _failing(text: str, fmt: str = "html") -> tuple[int, str]:
        calls.append(1)
        return 1, "telegram 500"

    monkeypatch.setattr(mon, "_send_tg", _failing)
    mon._alert_target_lost()  # send fails → mark NOT set
    assert mon._mark_is_set("target-lost-mark") is False
    mon._alert_target_lost()  # so the next poll may retry the single alert
    assert len(calls) == 2


def test_owner_alert_never_raises_on_send_crash(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(mon, "_monitor_file", lambda kind: tmp_path / f"m.{kind}")

    def _boom(text: str, fmt: str = "html") -> tuple[int, str]:
        raise RuntimeError("network exploded")

    monkeypatch.setattr(mon, "_send_tg", _boom)
    # Must swallow the exception and report failure rather than propagate.
    assert mon._send_owner_alert("hi") is False


def test_send_failed_alert_is_one_shot_and_clears(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(mon, "_monitor_file", lambda kind: tmp_path / f"m.{kind}")
    sent: list[str] = []
    monkeypatch.setattr(mon, "_send_tg", lambda text, fmt="html": (sent.append(text) or (0, "ok")))

    mon._alert_send_failed("telegram 502\nextra line")
    mon._alert_send_failed("telegram 502 again")
    assert len(sent) == 1
    assert sent[0].startswith("⚠️ 回传失败：")
    assert "telegram 502" in sent[0]
    # Newlines are flattened (not truncated) so an embedded hint survives.
    assert "extra line" in sent[0]
    assert "\n" not in sent[0]
    assert "（发 /status 排查）" in sent[0]

    mon._clear_send_failed()
    mon._alert_send_failed("new episode")
    assert len(sent) == 2


# --- screen-recording-denied detector (iterm-screenshot.py) ----------------

@pytest.mark.parametrize(
    "text",
    [
        "screencapture: cannot run",
        "could not create image from window 42",
        "Screen Recording permission is required",
        "not authorized to capture the screen",
        "error -25201",
        "kCGErrorCannotComplete",
        "process is not entitled",
    ],
)
def test_screen_recording_denied_detected(text: str) -> None:
    assert shot.is_screen_recording_denied(text) is True


@pytest.mark.parametrize(
    "text",
    ["", "screenshot sent", "tg-notify timed out", "No iTerm window open"],
)
def test_screen_recording_ok_not_flagged(text: str) -> None:
    assert shot.is_screen_recording_denied(text) is False


def test_with_screen_recording_hint_appends_once() -> None:
    raw = "could not create image"
    hinted = shot.with_screen_recording_hint(raw)
    assert shot.SCREEN_RECORDING_HINT in hinted
    # Idempotent: re-applying does not duplicate the hint.
    assert shot.with_screen_recording_hint(hinted) == hinted
    assert hinted.count(shot.SCREEN_RECORDING_HINT) == 1


def test_with_screen_recording_hint_noop_on_unrelated_error() -> None:
    raw = "tg-notify connection refused"
    assert shot.with_screen_recording_hint(raw) == raw


# --- double-failure surfaces the screenshot reason -------------------------

def test_double_failure_surfaces_screen_recording_hint(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(mon, "_monitor_file", lambda kind: tmp_path / f"m.{kind}")
    monkeypatch.setattr(mon, "_output_format", lambda: "screenshot")
    monkeypatch.setattr(mon, "extract_latest_reply", lambda cap: "the answer")
    monkeypatch.setattr(mon, "_read_last_sent", lambda: "")
    monkeypatch.setattr(mon, "new_content_since", lambda reply, last: reply)
    monkeypatch.setattr(mon, "is_duplicate", lambda cand, buf: False)
    monkeypatch.setattr(mon, "read_buffer", lambda p: [])

    # Screenshot fails with the screen-recording hint; the text fallback also fails.
    monkeypatch.setattr(
        mon, "_send_iterm_screenshot",
        lambda: (1, "screencapture failed\n" + shot.SCREEN_RECORDING_HINT),
    )
    alert_attempts: list[str] = []
    monkeypatch.setattr(
        mon, "_send_tg",
        lambda text, fmt="html": (alert_attempts.append(text) or (1, "telegram down")),
    )

    code, msg = mon._maybe_send_reply("ignored capture")
    assert code != 0
    assert shot.SCREEN_RECORDING_HINT in msg  # the reason is surfaced in the result
    # The owner-alert was attempted and carried the screen-recording reason.
    assert any("回传失败" in a and "屏幕录制" in a for a in alert_attempts)
    # The alert send itself failed here, so the mark stays unset (so a future
    # poll can retry the single alert) — matching the target-lost retry policy.
    assert mon._mark_is_set("send-fail-mark") is False


def test_successful_send_clears_failure_mark(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(mon, "_monitor_file", lambda kind: tmp_path / f"m.{kind}")
    monkeypatch.setattr(mon, "_output_format", lambda: "html")
    monkeypatch.setattr(mon, "extract_latest_reply", lambda cap: "the answer")
    monkeypatch.setattr(mon, "_read_last_sent", lambda: "")
    monkeypatch.setattr(mon, "new_content_since", lambda reply, last: reply)
    monkeypatch.setattr(mon, "is_duplicate", lambda cand, buf: False)
    monkeypatch.setattr(mon, "read_buffer", lambda p: [])
    monkeypatch.setattr(mon, "append_buffer", lambda p, t: None)
    monkeypatch.setattr(mon, "_write_last_sent", lambda t: None)
    monkeypatch.setattr(mon, "_write_last_sent_at", lambda t: None)
    monkeypatch.setattr(mon, "_send_tg", lambda text, fmt="html": (0, "ok"))

    mon._set_mark("send-fail-mark")  # a prior failure episode
    code, _ = mon._maybe_send_reply("ignored capture")
    assert code == 0
    assert mon._mark_is_set("send-fail-mark") is False  # cleared on success


# --- default bounded tail (finding #2) -------------------------------------

def test_default_tail_is_bounded() -> None:
    assert mon._DEFAULT_TAIL_LINES == 400
