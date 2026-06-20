"""Tests for attachments — save path + inject text for Telegram media."""
from __future__ import annotations

import attachments as at


def test_dest_path_sanitizes_and_adds_ext(tmp_path):
    p = at.dest_path("AgADbad/../id", "jpg", directory=tmp_path)
    assert p.parent == tmp_path
    assert p.name.endswith(".jpg")
    assert "/" not in p.name and ".." not in p.name


def test_dest_path_accepts_dotted_ext(tmp_path):
    p = at.dest_path("abc", ".png", directory=tmp_path)
    assert p.name == "abc.png"


def test_inject_text_includes_path():
    assert "/tmp/x.jpg" in at.inject_text("/tmp/x.jpg")


def test_inject_text_includes_caption_when_present():
    out = at.inject_text("/tmp/x.jpg", "修复这个报错")
    assert "/tmp/x.jpg" in out and "修复这个报错" in out


def test_inject_text_omits_caption_when_blank():
    out = at.inject_text("/tmp/x.jpg", "   ")
    assert "说明" not in out
