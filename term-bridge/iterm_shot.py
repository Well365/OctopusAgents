"""No-activate iTerm window capture.

Captures a specific iTerm2 window by its CoreGraphics window id via
`screencapture -l`, so the window is never brought to the front. The window id
comes straight from iTerm's own AppleScript dictionary (`id of window N`), which
equals the CGWindowID `screencapture -l` expects — this avoids both System
Events automation (the `-1743` permission wall) and `activate` (focus stealing).
"""
from __future__ import annotations

import subprocess
from pathlib import Path


def build_window_id_script(window: int | None, app: str = "iTerm") -> str:
    """AppleScript that returns the CGWindowID of the target window.

    `window` None → frontmost (current) window; otherwise the 1-based index.
    `app` is the scripting name ("iTerm" or "Terminal"); both expose `id of
    window` as the CGWindowID that `screencapture -l` expects.
    """
    if window is None:
        # iTerm's frontmost is "current window"; Terminal.app uses "front window".
        win_spec = "current window" if app == "iTerm" else "front window"
    else:
        win_spec = f"window {int(window)}"
    return (
        f'if application "{app}" is not running then error "No {app} running"\n'
        f'tell application "{app}"\n'
        f'    if (count of windows) is 0 then error "No {app} window open"\n'
        f"    return id of {win_spec}\n"
        "end tell"
    )


def build_select_tab_script(window: int | None, tab: int, app: str = "iTerm") -> str:
    """AppleScript that makes `tab` the front tab of the target window.

    `screencapture -l <window-id>` renders the window's *front* tab, so to
    capture a specific tab we select it first. Best-effort: wrapped in `try`
    so a single-tab window or a read-only property never aborts the capture.
    No `activate` — selecting a tab does not steal OS focus.
    """
    if window is None:
        win_spec = "current window" if app == "iTerm" else "front window"
    else:
        win_spec = f"window {int(window)}"
    if app == "iTerm":
        return (
            'tell application "iTerm"\n'
            "    try\n"
            f"        tell {win_spec}\n"
            f"            select tab {int(tab)}\n"
            "        end tell\n"
            "    end try\n"
            "end tell"
        )
    return (
        'tell application "Terminal"\n'
        "    try\n"
        f"        set selected of tab {int(tab)} of {win_spec} to true\n"
        "    end try\n"
        "end tell"
    )


def select_tab(window: int | None, tab: int, *, app: str = "iTerm", timeout: float = 10.0) -> None:
    """Best-effort: bring `tab` to the front of its window before capture."""
    try:
        subprocess.run(
            ["osascript", "-e", build_select_tab_script(window, tab, app=app)],
            capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL,
        )
    except (subprocess.TimeoutExpired, OSError):
        pass  # capture proceeds on whatever tab is front


def parse_window_id(raw: str) -> int:
    """Validate osascript output is a positive integer window id."""
    value = (raw or "").strip()
    if not value.lstrip("-").isdigit():
        raise ValueError(f"invalid window id: {raw!r}")
    wid = int(value)
    if wid <= 0:
        raise ValueError(f"window id must be positive: {wid}")
    return wid


def build_screencapture_cmd(window_id: int, path: str, no_shadow: bool = False) -> list[str]:
    """`screencapture -x -l <id> [-o] <path>` — silent, window-targeted capture."""
    cmd = ["screencapture", "-x"]
    if no_shadow:
        cmd.append("-o")
    cmd += ["-l", str(window_id), path]
    return cmd


def get_window_id(window: int | None, *, app: str = "iTerm", timeout: float = 15.0) -> int:
    """Resolve the target window's CGWindowID without activating it."""
    r = subprocess.run(
        ["osascript", "-e", build_window_id_script(window, app=app)],
        capture_output=True,
        text=True,
        timeout=timeout,
        stdin=subprocess.DEVNULL,
    )
    if r.returncode != 0:
        detail = (r.stderr or r.stdout or "osascript failed").strip()
        raise RuntimeError(f"could not read {app} window id: {detail}")
    return parse_window_id(r.stdout)


def capture_window_png(
    window: int | None,
    path: str | Path,
    *,
    app: str = "iTerm",
    no_shadow: bool = False,
    timeout: float = 30.0,
) -> Path:
    """Capture the target window to `path` (no activate). Returns the path."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    wid = get_window_id(window, app=app)
    cmd = build_screencapture_cmd(wid, str(out), no_shadow=no_shadow)
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    if r.returncode != 0:
        detail = (r.stderr or r.stdout or f"exit {r.returncode}").strip()
        raise RuntimeError(f"screencapture failed: {detail}")
    if not out.is_file() or out.stat().st_size == 0:
        raise RuntimeError(f"screenshot not created at {out}")
    return out
