#!/usr/bin/env python3
"""Shared runtime output-format config.

The relay (writer, via Telegram /format) and the monitor (reader) are separate
processes, so they agree through a small state file instead of process env —
changes take effect immediately, no restart.

Precedence: state file > env TG_ITERM_FORMAT > "html".
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE_FILE = ROOT / "inbox" / "iterm-format"

VALID = ("html", "markdown", "plain", "screenshot")
_ALIASES = {
    "md": "markdown",
    "markdownv2": "markdown",
    "text": "plain",
    "none": "plain",
    "shot": "screenshot",
    "pic": "screenshot",
    "image": "screenshot",
    "png": "screenshot",
}


def normalize(value: str | None) -> str | None:
    """Canonical format name, or None if not recognized."""
    v = (value or "").strip().lower()
    v = _ALIASES.get(v, v)
    return v if v in VALID else None


def get_format() -> str:
    """Current output format: state file → env → 'html'."""
    try:
        if STATE_FILE.is_file():
            n = normalize(STATE_FILE.read_text(encoding="utf-8"))
            if n:
                return n
    except OSError:
        pass
    return normalize(os.environ.get("TG_ITERM_FORMAT")) or "html"


def set_format(value: str) -> str | None:
    """Persist a new format. Returns the canonical name, or None if invalid."""
    n = normalize(value)
    if n is None:
        return None
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(n, encoding="utf-8")
    return n
