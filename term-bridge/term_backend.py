"""Select the active terminal backend (iTerm vs system Terminal.app) + its scripts.

`TG_TERM_BACKEND=iterm|terminal` chooses the backend; default `iterm` keeps the
existing behavior. Invalid values fall back to `iterm` with a warning. Each
backend has a parallel set of capture/inject/screenshot scripts in this dir.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_DIR = Path(__file__).resolve().parent
_VALID = ("iterm", "terminal")

_SCRIPTS = {
    "iterm": {
        "capture": "iterm-capture.py",
        "screenshot": "iterm-screenshot.py",
        "inject": "iterm-inject.py",
    },
    "terminal": {
        "capture": "terminal-capture.py",
        "screenshot": "terminal-screenshot.py",
        "inject": "terminal-inject.py",
    },
}


def resolve_backend() -> str:
    """Return 'iterm' or 'terminal' from TG_TERM_BACKEND (default 'iterm')."""
    raw = os.environ.get("TG_TERM_BACKEND", "").strip().lower()
    if not raw:
        return "iterm"
    if raw not in _VALID:
        print(
            f"term_backend: unknown TG_TERM_BACKEND={raw!r}, falling back to 'iterm'",
            file=sys.stderr,
            flush=True,
        )
        return "iterm"
    return raw


def _script(kind: str) -> Path:
    return _DIR / _SCRIPTS[resolve_backend()][kind]


def capture_script() -> Path:
    return _script("capture")


def screenshot_script() -> Path:
    return _script("screenshot")


def inject_script() -> Path:
    return _script("inject")
