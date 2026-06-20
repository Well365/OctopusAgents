"""Tests for quick_prompts — saved reusable prompts for Telegram /p."""
from __future__ import annotations

import quick_prompts as qp


def test_load_missing_returns_empty(tmp_path):
    assert qp.load(tmp_path / "none.json") == {}


def test_load_corrupt_returns_empty(tmp_path):
    p = tmp_path / "qp.json"
    p.write_text("{ broken", encoding="utf-8")
    assert qp.load(p) == {}


def test_save_then_load_roundtrip(tmp_path):
    p = tmp_path / "qp.json"
    qp.save_prompt("test", "跑测试并修复失败", path=p)
    assert qp.load(p) == {"test": "跑测试并修复失败"}


def test_save_trims_and_overwrites(tmp_path):
    p = tmp_path / "qp.json"
    qp.save_prompt("  test  ", "  a  ", path=p)
    qp.save_prompt("test", "b", path=p)
    assert qp.load(p) == {"test": "b"}


def test_delete_prompt(tmp_path):
    p = tmp_path / "qp.json"
    qp.save_prompt("a", "x", path=p)
    qp.save_prompt("b", "y", path=p)
    qp.delete_prompt("a", path=p)
    assert qp.load(p) == {"b": "y"}


def test_resolve_command_lists_when_no_args():
    reply, inject = qp.resolve_p_command([], {"t": "跑测试"}, lambda n, x: None, lambda n: None)
    assert inject is None
    assert "t" in reply


def test_resolve_command_injects_saved_prompt():
    reply, inject = qp.resolve_p_command(["t"], {"t": "跑测试并修复"}, lambda n, x: None, lambda n: None)
    assert inject == "跑测试并修复"


def test_resolve_command_unknown_name():
    reply, inject = qp.resolve_p_command(["nope"], {"t": "x"}, lambda n, x: None, lambda n: None)
    assert inject is None
    assert "没有" in reply


def test_resolve_command_save():
    saved = {}
    reply, inject = qp.resolve_p_command(
        ["save", "build", "编译", "并", "修复"], {}, lambda n, x: saved.update({n: x}), lambda n: None
    )
    assert inject is None
    assert saved == {"build": "编译 并 修复"}
    assert "✓" in reply


def test_resolve_command_delete():
    deleted = []
    reply, inject = qp.resolve_p_command(
        ["del", "build"], {"build": "x"}, lambda n, x: None, lambda n: deleted.append(n)
    )
    assert deleted == ["build"]
    assert "✓" in reply
