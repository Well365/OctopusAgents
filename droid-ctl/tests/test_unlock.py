"""Tests for Device.unlock / is_locked / _screen_wh (numeric-PIN unlock)."""
import pytest

from droid_ctl.ops.device import Device


class _Result:
    def __init__(self, stdout=""):
        self.stdout = stdout
        self.stderr = ""
        self.returncode = 0


class FakeRunner:
    """Records shell() calls and replies by substring match."""

    def __init__(self, responses=None):
        self.responses = responses or {}
        self.calls = []

    def run(self, *args, **kwargs):
        # satisfy resolve_serial()'s device-list check
        return _Result("List of devices attached\nTESTSERIAL\tdevice\n")

    def shell(self, cmd):
        self.calls.append(cmd)
        for key, val in self.responses.items():
            if key in cmd:
                return val
        return ""


def _device(responses=None):
    return Device(serial="TESTSERIAL", runner=FakeRunner(responses or {}))


def test_screen_wh_parses_wm_size():
    d = _device({"wm size": "Physical size: 1440x3120"})
    assert d._screen_wh() == (1440, 3120)


def test_screen_wh_fallback_on_garbage():
    d = _device({"wm size": "nonsense"})
    assert d._screen_wh() == (1080, 2400)


def test_is_locked_true():
    d = _device({"dumpsys window": "mDreamingLockscreen=true mFoo=bar"})
    assert d.is_locked() is True


def test_is_locked_false():
    d = _device({"dumpsys window": "mDreamingLockscreen=false"})
    assert d.is_locked() is False


def test_unlock_sequence_and_pin_not_plaintext():
    d = _device({"wm size": "Physical size: 1080x2400", "dumpsys window": "x=false"})
    res = d.unlock("1234", settle=0)
    joined = " ".join(d.runner.calls)
    assert "keyevent 224" in joined           # wake
    assert "input swipe" in joined            # reveal keypad
    assert "keyevent 8" in joined             # digit 1 -> KEYCODE_0(7)+1
    assert "keyevent 11" in joined            # digit 4 -> 7+4
    assert "keyevent 66" in joined            # Enter
    assert "1234" not in joined               # PIN never sent as plaintext
    assert res["pin_digits"] == 4
    assert "pin" not in res                    # result never leaks the PIN


def test_unlock_rejects_empty_pin():
    d = _device({"wm size": "Physical size: 1080x2400"})
    with pytest.raises(ValueError):
        d.unlock("abc", settle=0)
