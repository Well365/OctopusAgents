"""Tests for tg_format_config — shared runtime output-format selection."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import tg_format_config as cfg


def _tmp_state() -> Path:
    return Path(tempfile.mkdtemp()) / "iterm-format"


def test_normalize_canonical_and_aliases():
    assert cfg.normalize("HTML") == "html"
    assert cfg.normalize("md") == "markdown"
    assert cfg.normalize("MarkdownV2") == "markdown"
    assert cfg.normalize("text") == "plain"
    assert cfg.normalize("shot") == "screenshot"


def test_normalize_rejects_unknown():
    assert cfg.normalize("bogus") is None
    assert cfg.normalize("") is None
    assert cfg.normalize(None) is None


def test_set_then_get_roundtrip():
    cfg.STATE_FILE = _tmp_state()
    assert cfg.set_format("markdown") == "markdown"
    assert cfg.get_format() == "markdown"
    assert cfg.set_format("SCREENSHOT") == "screenshot"
    assert cfg.get_format() == "screenshot"


def test_set_invalid_returns_none_and_does_not_write():
    cfg.STATE_FILE = _tmp_state()
    assert cfg.set_format("weird") is None
    assert not cfg.STATE_FILE.exists()


def test_env_fallback_when_no_state_file():
    cfg.STATE_FILE = _tmp_state()  # does not exist yet
    os.environ["TG_ITERM_FORMAT"] = "plain"
    try:
        assert cfg.get_format() == "plain"
    finally:
        del os.environ["TG_ITERM_FORMAT"]


def test_default_is_html():
    cfg.STATE_FILE = _tmp_state()
    os.environ.pop("TG_ITERM_FORMAT", None)
    assert cfg.get_format() == "html"


def test_state_file_overrides_env():
    cfg.STATE_FILE = _tmp_state()
    os.environ["TG_ITERM_FORMAT"] = "plain"
    try:
        cfg.set_format("html")
        assert cfg.get_format() == "html"  # file wins over env
    finally:
        del os.environ["TG_ITERM_FORMAT"]


if __name__ == "__main__":
    import sys
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  ✓ {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  ✗ {fn.__name__}: {e!r}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
