"""AppleScript builder for injecting text into Terminal.app via clipboard + Cmd-V.

Terminal.app has no silent write-into-running-program API (unlike iTerm), so we
put the text on the clipboard, bring the target window to the front, paste with
System Events (needs Accessibility), optionally press Return, then restore the
prior frontmost app and the previous clipboard. The text is read from a file
named by the TERM_INJECT_FILE env var to avoid AppleScript string escaping.
"""
from __future__ import annotations


def _window_ref(window: int | None) -> str:
    return "front window" if window is None else f"window {int(window)}"


def build_inject_script(*, window: int | None, tab: int, submit_enter: bool) -> str:
    """Full `on run` AppleScript for one clipboard-paste injection."""
    win = _window_ref(window)
    submit = "    keystroke return\n" if submit_enter else ""
    # Best-effort focus of the requested window/tab; wrapped so a read-only
    # property or single-tab window never aborts the paste.
    focus_window = (
        f"        try\n"
        f"            set frontmost of {win} to true\n"
        f"        end try\n"
        f"        try\n"
        f"            set selected of tab {int(tab)} of {win} to true\n"
        f"        end try\n"
    )
    return (
        "on run\n"
        '    if application "Terminal" is not running then error "No Terminal running"\n'
        '    set msgPath to system attribute "TERM_INJECT_FILE"\n'
        '    set theText to read POSIX file msgPath as «class utf8»\n'
        "    if theText ends with (return) then\n"
        "        set theText to text 1 thru -2 of theText\n"
        "    end if\n"
        '    set savedClip to ""\n'
        "    try\n"
        "        set savedClip to (the clipboard as text)\n"
        "    end try\n"
        "    set the clipboard to theText\n"
        "    tell application \"System Events\"\n"
        "        set priorApp to name of first process whose frontmost is true\n"
        "    end tell\n"
        '    tell application "Terminal"\n'
        "        activate\n"
        f"{focus_window}"
        "    end tell\n"
        '    tell application "System Events"\n'
        '        keystroke "v" using command down\n'
        f"{submit}"
        "    end tell\n"
        "    try\n"
        "        tell application priorApp to activate\n"
        "    end try\n"
        "    try\n"
        "        set the clipboard to savedClip\n"
        "    end try\n"
        "end run\n"
    )
