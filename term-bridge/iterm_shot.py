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


def build_window_id_script(window: int | None) -> str:
    """AppleScript that returns the CGWindowID of the target iTerm window.

    `window` None → frontmost (current) window; otherwise the 1-based index.
    """
    if window is None:
        win_spec = "current window"
    else:
        win_spec = f"window {int(window)}"
    return (
        'tell application "iTerm"\n'
        '    if (count of windows) is 0 then error "No iTerm window open"\n'
        f"    return id of {win_spec}\n"
        "end tell"
    )


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


def get_window_id(window: int | None, *, timeout: float = 15.0) -> int:
    """Resolve the target iTerm window's CGWindowID without activating it."""
    r = subprocess.run(
        ["osascript", "-e", build_window_id_script(window)],
        capture_output=True,
        text=True,
        timeout=timeout,
        stdin=subprocess.DEVNULL,
    )
    if r.returncode != 0:
        detail = (r.stderr or r.stdout or "osascript failed").strip()
        raise RuntimeError(f"could not read iTerm window id: {detail}")
    return parse_window_id(r.stdout)


def capture_window_png(
    window: int | None,
    path: str | Path,
    *,
    no_shadow: bool = False,
    timeout: float = 30.0,
) -> Path:
    """Capture the target iTerm window to `path` (no activate). Returns the path."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    wid = get_window_id(window)
    cmd = build_screencapture_cmd(wid, str(out), no_shadow=no_shadow)
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    if r.returncode != 0:
        detail = (r.stderr or r.stdout or f"exit {r.returncode}").strip()
        raise RuntimeError(f"screencapture failed: {detail}")
    if not out.is_file() or out.stat().st_size == 0:
        raise RuntimeError(f"screenshot not created at {out}")
    return out
