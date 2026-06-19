"""Tests for term_backend — selecting the active terminal backend + its scripts."""
from __future__ import annotations

from pathlib import Path

import pytest

import term_backend


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    monkeypatch.delenv("TG_TERM_BACKEND", raising=False)


# ── resolve_backend ──

def test_default_is_iterm():
    assert term_backend.resolve_backend() == "iterm"


def test_terminal_selected(monkeypatch):
    monkeypatch.setenv("TG_TERM_BACKEND", "terminal")
    assert term_backend.resolve_backend() == "terminal"


def test_case_insensitive(monkeypatch):
    monkeypatch.setenv("TG_TERM_BACKEND", "Terminal")
    assert term_backend.resolve_backend() == "terminal"


def test_whitespace_trimmed(monkeypatch):
    monkeypatch.setenv("TG_TERM_BACKEND", "  iterm  ")
    assert term_backend.resolve_backend() == "iterm"


def test_invalid_falls_back_to_iterm(monkeypatch, capsys):
    monkeypatch.setenv("TG_TERM_BACKEND", "kitty")
    assert term_backend.resolve_backend() == "iterm"
    assert "kitty" in (capsys.readouterr().err or "")


def test_empty_falls_back_to_iterm(monkeypatch):
    monkeypatch.setenv("TG_TERM_BACKEND", "")
    assert term_backend.resolve_backend() == "iterm"


# ── script resolvers ──

def test_iterm_scripts(monkeypatch):
    monkeypatch.setenv("TG_TERM_BACKEND", "iterm")
    assert term_backend.capture_script().name == "iterm-capture.py"
    assert term_backend.screenshot_script().name == "iterm-screenshot.py"
    assert term_backend.inject_script().name == "iterm-inject.py"


def test_terminal_scripts(monkeypatch):
    monkeypatch.setenv("TG_TERM_BACKEND", "terminal")
    assert term_backend.capture_script().name == "terminal-capture.py"
    assert term_backend.screenshot_script().name == "terminal-screenshot.py"
    assert term_backend.inject_script().name == "terminal-inject.py"


def test_scripts_are_absolute_existing_paths(monkeypatch):
    monkeypatch.setenv("TG_TERM_BACKEND", "iterm")
    for fn in (term_backend.capture_script, term_backend.screenshot_script, term_backend.inject_script):
        p = fn()
        assert isinstance(p, Path)
        assert p.is_absolute()
