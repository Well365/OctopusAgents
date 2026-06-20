"""Tests for claude_hook_install — merge/remove the Claude TG hook in settings.json."""
from __future__ import annotations

import claude_hook_install as h

SCRIPT = "/repo/scripts/claude-tg-hook.sh"


def test_add_registers_stop_and_notification_and_env():
    s = h.add_hook({}, SCRIPT)
    assert s["hooks"]["Stop"][0]["hooks"][0]["command"] == SCRIPT
    assert s["hooks"]["Notification"][0]["hooks"][0]["command"] == SCRIPT
    assert s["env"]["CLAUDE_CODE_DISABLE_TERMINAL_TITLE"] == "1"


def test_add_is_idempotent():
    s = h.add_hook({}, SCRIPT)
    s2 = h.add_hook(s, SCRIPT)
    assert len(s2["hooks"]["Stop"]) == 1
    assert len(s2["hooks"]["Notification"]) == 1


def test_add_preserves_existing_settings_and_hooks():
    s = {
        "model": "opus",
        "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "/other.sh"}]}]},
    }
    out = h.add_hook(s, SCRIPT)
    assert out["model"] == "opus"
    cmds = [hh["command"] for e in out["hooks"]["Stop"] for hh in e["hooks"]]
    assert "/other.sh" in cmds and SCRIPT in cmds


def test_remove_drops_our_hooks_and_env():
    s = h.add_hook({"model": "opus"}, SCRIPT)
    out = h.remove_hook(s, SCRIPT)
    assert "hooks" not in out
    assert "env" not in out
    assert out["model"] == "opus"


def test_remove_preserves_other_hooks_in_same_event():
    s = {
        "hooks": {
            "Stop": [
                {"hooks": [{"type": "command", "command": "/other.sh"}]},
                {"hooks": [{"type": "command", "command": SCRIPT}]},
            ]
        }
    }
    out = h.remove_hook(s, SCRIPT)
    cmds = [hh["command"] for e in out["hooks"]["Stop"] for hh in e["hooks"]]
    assert cmds == ["/other.sh"]


def test_remove_keeps_other_env_vars():
    s = {"env": {"FOO": "1", "CLAUDE_CODE_DISABLE_TERMINAL_TITLE": "1"}}
    out = h.remove_hook(s, SCRIPT)
    assert out["env"] == {"FOO": "1"}


def test_remove_when_absent_is_noop():
    assert h.remove_hook({"model": "opus"}, SCRIPT) == {"model": "opus"}
