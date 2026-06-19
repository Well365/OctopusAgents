"""Pure builders for spawning an agent session in a new Terminal.app tab.

`build_spawn_command` composes one chained shell line (mkdir -> cd ->
install-if-missing -> launch-with-prompt). `build_spawn_applescript` returns
the AppleScript that opens a new tab (or window if none) and runs that line via
a temp bash script, returning the new tab index for retargeting. No side
effects — all timestamp/file/IO work lives in the terminal-spawn.py CLI.
"""
from __future__ import annotations

from agent_cli import AgentSpec


def shell_quote(s: str) -> str:
    """POSIX single-quote a string for safe inclusion in a shell command."""
    return "'" + s.replace("'", "'\\''") + "'"


def build_spawn_command(*, dirname: str, agent: AgentSpec, prompt: str) -> str:
    """Chained shell line: make/enter dir, install if missing, launch agent."""
    workdir = f'"$HOME/fullStar/{dirname}"'
    install = f"(command -v {agent.check} >/dev/null 2>&1 || {agent.installer})"
    launch = agent.launch
    if prompt:
        launch = f"{launch} {shell_quote(prompt)}"
    return f"mkdir -p {workdir} && cd {workdir} && {install} && {launch}"


def build_spawn_applescript(*, script_path: str) -> str:
    """AppleScript: focus Terminal, new tab (or window), run the script, return tab index."""
    runner = f"bash '{script_path}' ; rm -f '{script_path}'"
    return (
        "on run\n"
        '    tell application "Terminal"\n'
        "        set hadWindow to (count of windows) > 0\n"
        "        activate\n"
        "    end tell\n"
        '    tell application "System Events"\n'
        '        set frontmost of process "Terminal" to true\n'
        "        repeat 40 times\n"
        '            if frontmost of process "Terminal" then exit repeat\n'
        "            delay 0.05\n"
        "        end repeat\n"
        "        if hadWindow then\n"
        '            keystroke "t" using command down\n'
        "            delay 0.3\n"
        "        end if\n"
        "    end tell\n"
        '    tell application "Terminal"\n'
        "        if (count of windows) is 0 then\n"
        f'            do script "{runner}"\n'
        "        else\n"
        f'            do script "{runner}" in front window\n'
        "        end if\n"
        "        set tabIdx to (count of tabs of front window)\n"
        "    end tell\n"
        "    return tabIdx\n"
        "end run\n"
    )
