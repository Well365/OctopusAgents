"""AppleScript builder for reading Terminal.app scrollback (no activate needed)."""
from __future__ import annotations


def _window_ref(window: int | None) -> str:
    return "front window" if window is None else f"window {int(window)}"


def build_read_script(*, window: int | None, tab: int) -> str:
    """AppleScript returning the full scrollback (`history`) of the target tab."""
    win = _window_ref(window)
    return (
        'if application "Terminal" is not running then error "No Terminal running"\n'
        'tell application "Terminal"\n'
        '    if (count of windows) is 0 then error "No Terminal window open"\n'
        f"    set targetWindow to {win}\n"
        f"    return history of tab {int(tab)} of targetWindow\n"
        "end tell"
    )
