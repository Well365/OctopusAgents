"""Tests for the --key (enter/esc) mode of the iTerm inject backend."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import importlib.util
import pytest

_CLI = Path(__file__).resolve().parent / "iterm-inject.py"
_spec = importlib.util.spec_from_file_location("iterm_inject_mod", _CLI)
iterm_inject_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(iterm_inject_mod)


def test_key_applescript_enter_writes_empty_line():
    s = iterm_inject_mod._key_applescript("enter")
    assert 'write text ""' in s


def test_key_applescript_esc_writes_escape_char():
    s = iterm_inject_mod._key_applescript("esc")
    assert "character id 27" in s


def test_key_applescript_unknown_key_raises():
    with pytest.raises(ValueError, match="unknown key"):
        iterm_inject_mod._key_applescript("tab")


def test_cli_dry_run_key_enter():
    r = subprocess.run(
        [sys.executable, str(_CLI), "--key", "enter", "--tab", "2", "--dry-run"],
        capture_output=True, text=True, timeout=30,
    )
    assert r.returncode == 0
    assert "would press enter" in r.stdout.lower()


def test_cli_dry_run_key_esc():
    r = subprocess.run(
        [sys.executable, str(_CLI), "--key", "esc", "--tab", "2", "--dry-run"],
        capture_output=True, text=True, timeout=30,
    )
    assert r.returncode == 0
    assert "would press esc" in r.stdout.lower()
