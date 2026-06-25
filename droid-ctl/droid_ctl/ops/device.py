"""High-level Android device operations."""

import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, Union

from droid_ctl.core.device import resolve_serial, screen_size
from droid_ctl.core.runner import AdbRunner

REMOTE_UI_DUMP = "/sdcard/window_dump.xml"

KEY_MAP = {
    "HOME": 3,
    "BACK": 4,
    "ENTER": 66,
    "RETURN": 66,
    "MENU": 82,
    "POWER": 26,
    "VOLUME_UP": 24,
    "VOLUME_DOWN": 25,
    "TAB": 61,
    "DEL": 67,
    "DELETE": 67,
}


def default_shot_dir() -> Path:
    path = Path(os.environ.get("ADB_SHOT_DIR", "/tmp/adbkit"))
    path.mkdir(parents=True, exist_ok=True)
    return path


class Device:
    """ADB device control for one serial (or auto-resolved)."""

    def __init__(self, serial: Optional[str] = None, runner: Optional[AdbRunner] = None):
        self.serial = resolve_serial(serial, runner)
        self.runner = runner or AdbRunner(serial=self.serial)

    def screenshot(self, output: Optional[Union[str, Path]] = None) -> Path:
        out = Path(output) if output else default_shot_dir() / f"{datetime.now():%Y%m%d_%H%M%S}.png"
        out.parent.mkdir(parents=True, exist_ok=True)
        data = self.runner.run_bytes("exec-out", "screencap", "-p")
        out.write_bytes(data)
        return out

    def tap(self, x: int, y: int) -> None:
        self.runner.shell(f"input tap {int(x)} {int(y)}")

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> None:
        self.runner.shell(
            f"input swipe {int(x1)} {int(y1)} {int(x2)} {int(y2)} {int(duration_ms)}"
        )

    def input_text(self, text: str) -> None:
        escaped = text.replace(" ", "%s")
        escaped = escaped.replace('"', '\\"')
        self.runner.shell(f'input text "{escaped}"')

    def keyevent(self, key: Union[int, str]) -> None:
        if isinstance(key, str):
            key = KEY_MAP.get(key.upper(), key)
        self.runner.shell(f"input keyevent {key}")

    def shell(self, command: str) -> str:
        return self.runner.shell(command)

    def _screen_wh(self) -> Tuple[int, int]:
        """(width, height) in px from `wm size`; falls back to a common default."""
        out = self.shell("wm size")
        m = re.search(r"(\d+)\s*x\s*(\d+)", out or "")
        if not m:
            return (1080, 2400)
        return int(m.group(1)), int(m.group(2))

    def is_locked(self) -> bool:
        """True if the keyguard / lockscreen is currently showing."""
        out = self.shell("dumpsys window") or ""
        for key in ("mDreamingLockscreen", "mShowingLockscreen", "isStatusBarKeyguard"):
            m = re.search(rf"{key}=(\w+)", out)
            if m and m.group(1) == "true":
                return True
        return False

    def unlock(self, pin: str, *, wake: bool = True, settle: float = 0.6) -> Dict[str, Any]:
        """Unlock a numeric-PIN lockscreen: wake -> swipe up -> type PIN -> Enter.

        Only digits are sent (via keyevents 7-16 = KEYCODE_0..9), so the PIN
        never appears as plaintext in `input text`. Pattern/biometric unlock is
        NOT supported. Returns a status dict (does NOT include the PIN).
        """
        digits = [c for c in pin if c.isdigit()]
        if not digits:
            raise ValueError("unlock PIN must contain digits")
        was_locked = self.is_locked()
        w, h = self._screen_wh()
        if wake:
            self.keyevent(224)  # KEYCODE_WAKEUP
            time.sleep(settle)
        # swipe up from lower-middle to upper-middle to reveal the PIN pad
        self.swipe(w // 2, int(h * 0.80), w // 2, int(h * 0.20), 250)
        time.sleep(settle)
        for d in digits:
            self.keyevent(7 + int(d))  # KEYCODE_0 == 7
        self.keyevent(66)  # ENTER submits
        time.sleep(settle)
        return {
            "ok": True,
            "device": self.serial,
            "was_locked": was_locked,
            "now_locked": self.is_locked(),
            "screen": f"{w}x{h}",
            "pin_digits": len(digits),
        }

    def install(self, apk_path: Union[str, Path]) -> str:
        result = self.runner.run(
            "install", "-r", str(apk_path), capture_output=True, text=True
        )
        return (result.stdout or "") + (result.stderr or "")

    def start_activity(self, component: str) -> str:
        result = self.runner.run(
            "shell", "am", "start", "-n", component, capture_output=True, text=True
        )
        return (result.stdout or "").strip()

    def pull(self, remote: str, local: Union[str, Path]) -> Path:
        local_path = Path(local)
        self.runner.run("pull", remote, str(local_path), check=True)
        return local_path

    def push(self, local: Union[str, Path], remote: str) -> None:
        self.runner.run("push", str(local), remote, check=True)

    def ui_dump(self, output: Optional[Union[str, Path]] = None) -> Path:
        out = Path(output) if output else default_shot_dir() / "ui_dump.xml"
        out.parent.mkdir(parents=True, exist_ok=True)
        self.runner.shell(f"uiautomator dump {REMOTE_UI_DUMP}")
        self.pull(REMOTE_UI_DUMP, out)
        return out

    def logcat(self, lines: int = 50, follow: bool = False) -> str:
        if follow:
            raise NotImplementedError("use runner.run interactively for follow mode")
        return self.runner.shell(f"logcat -d -t {int(lines)}")

    def info_dict(self, shot_path: Optional[Path] = None) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "ok": True,
            "device": self.serial,
            "screen": screen_size(self.runner),
        }
        if shot_path:
            data["path"] = str(shot_path.resolve())
            data["bytes"] = shot_path.stat().st_size
        return data

    def analyze(self, with_ui: bool = False) -> Dict[str, Any]:
        shot = self.screenshot()
        data = self.info_dict(shot)
        if with_ui:
            ui = self.ui_dump()
            data["ui_dump"] = str(ui.resolve())
        return data
