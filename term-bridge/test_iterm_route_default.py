# term-bridge/test_iterm_route_default.py
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import iterm_route as ir
from iterm_route import TabInfo
from iterm_target import ItermTarget


def _tabs(*pairs):
    return 0, [TabInfo(window=w, tab=t, name=f"dir{t}") for w, t in pairs]


def test_no_prefix_uses_sticky_default(monkeypatch):
    monkeypatch.setattr(ir, "read_default", lambda: ItermTarget(window=1, tab=3))
    monkeypatch.setattr(ir, "list_tabs", lambda: _tabs((1, 1), (1, 3)))
    target, body, hit = ir.parse_routed_message("现在项目状态如何")
    assert (target.window, target.tab) == (1, 3)
    assert body == "现在项目状态如何"


def test_prefix_overrides_sticky_default(monkeypatch):
    monkeypatch.setattr(ir, "read_default", lambda: ItermTarget(window=1, tab=3))
    monkeypatch.setattr(ir, "list_tabs", lambda: _tabs((1, 1), (1, 2), (1, 3)))
    target, body, hit = ir.parse_routed_message("[t2] 列目录")
    assert target.tab == 2
    assert body == "列目录"


def test_no_default_falls_back_to_env(monkeypatch):
    monkeypatch.setattr(ir, "read_default", lambda: None)
    monkeypatch.setattr(ir, "resolve_target", lambda: ItermTarget(window=1, tab=1))
    target, body, hit = ir.parse_routed_message("无前缀消息")
    assert target.tab == 1


def test_sticky_default_for_closed_tab_falls_back(monkeypatch):
    # sticky points at t9 but only t1 is open now
    monkeypatch.setattr(ir, "read_default", lambda: ItermTarget(window=1, tab=9))
    monkeypatch.setattr(ir, "list_tabs", lambda: _tabs((1, 1)))
    monkeypatch.setattr(ir, "resolve_target", lambda: ItermTarget(window=1, tab=1))
    target, body, hit = ir.parse_routed_message("无前缀消息")
    assert target.tab == 1
