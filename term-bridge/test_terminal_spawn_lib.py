"""Tests for terminal_spawn_lib — pure spawn command + AppleScript builders."""
from __future__ import annotations

import agent_cli
import terminal_spawn_lib as lib

CLAUDE = agent_cli.get_agent("claude")


def test_shell_quote_plain():
    assert lib.shell_quote("hello") == "'hello'"


def test_shell_quote_escapes_single_quote():
    assert lib.shell_quote("it's") == "'it'\\''s'"


def test_command_with_prompt():
    cmd = lib.build_spawn_command(dirname="2026-06-19-2230", agent=CLAUDE, prompt="fix the bug")
    assert 'mkdir -p "$HOME/fullStar/2026-06-19-2230"' in cmd
    assert 'cd "$HOME/fullStar/2026-06-19-2230"' in cmd
    assert "command -v claude >/dev/null 2>&1 || curl -fsSL https://claude.ai/install.sh | bash" in cmd
    assert cmd.rstrip().endswith("claude --permission-mode bypassPermissions 'fix the bug'")


def test_command_without_prompt_has_no_trailing_arg():
    cmd = lib.build_spawn_command(dirname="2026-06-19-2230", agent=CLAUDE, prompt="")
    assert cmd.rstrip().endswith("claude --permission-mode bypassPermissions")
    assert "''" not in cmd  # no empty-quote artifact


def test_command_prompt_quote_is_escaped():
    cmd = lib.build_spawn_command(dirname="d", agent=CLAUDE, prompt="it's")
    assert "'it'\\''s'" in cmd


def test_applescript_opens_tab_and_returns_tabcount():
    script = lib.build_spawn_applescript(script_path="/tmp/spawn-x.sh")
    assert "bash '/tmp/spawn-x.sh'" in script
    assert 'keystroke "t" using command down' in script
    assert "count of tabs of front window" in script
