"""Tests for screenshot similarity dedup + target-tab selection script."""
from __future__ import annotations

import io

from PIL import Image

import iterm_shot
import screenshot_dedup as sd


def _png(gray: int, size=(120, 80)) -> bytes:
    buf = io.BytesIO()
    Image.new("L", size, gray).save(buf, format="PNG")
    return buf.getvalue()


# ── similarity math (no image needed) ──

def test_similarity_identical_is_one():
    fp = [10, 20, 30, 40]
    assert sd.similarity(fp, fp) == 1.0


def test_similarity_length_mismatch_is_zero():
    assert sd.similarity([1, 2, 3], [1, 2]) == 0.0


def test_similarity_empty_is_zero():
    assert sd.similarity([], [1]) == 0.0


def test_similarity_one_pixel_flip_stays_high():
    a = [100] * 100
    b = [100] * 100
    b[0] = 0  # a single pixel flips fully
    assert sd.similarity(a, b) >= 0.99


def test_similarity_full_invert_is_zero():
    assert sd.similarity([0] * 100, [255] * 100) == 0.0


def test_is_duplicate_threshold():
    assert sd.is_duplicate([100] * 100, [100] * 100, threshold=0.95) is True
    assert sd.is_duplicate([100] * 100, [0] * 100, threshold=0.95) is False


# ── fingerprint from real PNGs ──

def test_fingerprint_size_and_same_image_is_duplicate():
    f1 = sd.fingerprint(_png(128))
    f2 = sd.fingerprint(_png(128))
    assert len(f1) == 32 * 32
    assert sd.is_duplicate(f1, f2) is True


def test_fingerprint_different_images_not_duplicate():
    f_black = sd.fingerprint(_png(0))
    f_white = sd.fingerprint(_png(255))
    assert sd.is_duplicate(f_black, f_white) is False


def test_fingerprint_state_roundtrip(tmp_path):
    f = tmp_path / "fp"
    fp = sd.fingerprint(_png(100))
    assert sd.read_fingerprint(f) == []  # missing file
    sd.write_fingerprint(f, fp)
    assert sd.read_fingerprint(f) == fp


# ── tab selection script builder ──

def test_select_tab_terminal_sets_selected():
    s = iterm_shot.build_select_tab_script(1, 2, app="Terminal")
    assert "Terminal" in s
    assert "selected of tab 2" in s
    assert "window 1" in s


def test_select_tab_terminal_front_window_when_none():
    s = iterm_shot.build_select_tab_script(None, 3, app="Terminal")
    assert "front window" in s
    assert "selected of tab 3" in s


def test_select_tab_iterm_selects_tab():
    s = iterm_shot.build_select_tab_script(1, 3, app="iTerm")
    assert "iTerm" in s
    assert "select tab 3" in s
