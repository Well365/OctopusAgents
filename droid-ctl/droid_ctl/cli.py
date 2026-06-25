import argparse
import json
import os
import sys
from pathlib import Path

from droid_ctl.core.adb_path import resolve_adb_path
from droid_ctl.core.device import list_devices
from droid_ctl.core.exceptions import AdbkitError
from droid_ctl.core.tools import (
    install_platform_tools,
    is_installed,
    platform_tools_dir,
    repo_vendor_dir,
)
from droid_ctl.ops.device import Device


def _print_json(data) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def cmd_install_tools(args) -> int:
    path = install_platform_tools(
        force=args.force,
        force_download=args.download,
        update_vendor=args.update_vendor,
    )
    print(f"installed: {path}")
    if args.download:
        loc = "vendor/" if args.update_vendor else "~/.droid-ctl/"
        print(f"source: Google download → {loc}")
    else:
        vendor = repo_vendor_dir()
        if vendor:
            print(f"source: {vendor} (repo vendor, offline)")
    return 0


def cmd_which(args) -> int:
    path = resolve_adb_path(auto_install=not args.no_install)
    print(path)
    if args.json:
        vendor = repo_vendor_dir()
        _print_json({
            "adb": str(path),
            "repo_vendor": str(vendor) if vendor else "",
            "user_cache": str(platform_tools_dir()),
            "user_cache_installed": is_installed(),
        })
    return 0


def cmd_devices(args) -> int:
    devices = list_devices()
    if args.json:
        _print_json([d.__dict__ for d in devices])
        return 0
    print("List of devices attached")
    for d in devices:
        extras = " ".join(
            f"{k}:{v}" for k, v in (("product", d.product), ("model", d.model), ("device", d.device)) if v
        )
        print(f"{d.serial}\t{d.state}" + (f" {extras}" if extras else ""))
    return 0


def cmd_shot(args) -> int:
    dev = Device(serial=args.serial)
    path = dev.screenshot(args.output)
    if args.json:
        _print_json(dev.info_dict(path))
    else:
        print(f"screenshot: {path}")
        print(f"device: {dev.serial}")
    return 0


def cmd_analyze(args) -> int:
    dev = Device(serial=args.serial)
    data = dev.analyze(with_ui=args.ui)
    _print_json(data)
    return 0


def cmd_tap(args) -> int:
    Device(serial=args.serial).tap(args.x, args.y)
    print(f"tap: ({args.x}, {args.y})")
    return 0


def cmd_swipe(args) -> int:
    Device(serial=args.serial).swipe(args.x1, args.y1, args.x2, args.y2, args.duration)
    print(f"swipe: ({args.x1},{args.y1}) → ({args.x2},{args.y2}) {args.duration}ms")
    return 0


def cmd_input(args) -> int:
    Device(serial=args.serial).input_text(args.text)
    print(f"input: {args.text}")
    return 0


def cmd_key(args) -> int:
    Device(serial=args.serial).keyevent(args.key)
    print(f"keyevent: {args.key}")
    return 0


def cmd_unlock(args) -> int:
    pin = args.pin or os.environ.get("ANDROID_UNLOCK_PIN", "")
    if not pin:
        print("unlock: no PIN (pass --pin or set ANDROID_UNLOCK_PIN)", file=sys.stderr)
        return 1
    result = Device(serial=args.serial).unlock(pin)
    print(json.dumps(result, ensure_ascii=False))
    return 0


def cmd_shell(args) -> int:
    out = Device(serial=args.serial).shell(args.command)
    if out:
        print(out)
    return 0


def cmd_install(args) -> int:
    out = Device(serial=args.serial).install(args.apk)
    print(out.strip() or f"installed: {args.apk}")
    return 0


def cmd_start(args) -> int:
    out = Device(serial=args.serial).start_activity(args.component)
    print(out or f"started: {args.component}")
    return 0


def cmd_ui_dump(args) -> int:
    dev = Device(serial=args.serial)
    path = dev.ui_dump(args.output)
    if args.json:
        _print_json({"ok": True, "device": dev.serial, "path": str(path.resolve())})
    else:
        print(f"ui_dump: {path}")
    return 0


def cmd_pull(args) -> int:
    path = Device(serial=args.serial).pull(args.remote, args.local)
    print(f"pulled: {args.remote} → {path}")
    return 0


def cmd_push(args) -> int:
    Device(serial=args.serial).push(args.local, args.remote)
    print(f"pushed: {args.local} → {args.remote}")
    return 0


def cmd_logcat(args) -> int:
    out = Device(serial=args.serial).logcat(lines=args.lines)
    print(out)
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="droid-ctl", description="Android ADB toolkit")
    parser.add_argument("--serial", "-s", help="Device serial (or ADB_SERIAL)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("install-tools", help="Install platform-tools to ~/.adbkit")
    p.add_argument("--force", action="store_true", help="Reinstall even if present")
    p.add_argument(
        "--download",
        action="store_true",
        help="Download latest from Google (update; requires network)",
    )
    p.add_argument(
        "--update-vendor",
        action="store_true",
        help="With --download: write into repo vendor/ (maintainers)",
    )
    p.set_defaults(func=cmd_install_tools)

    p = sub.add_parser("which", help="Show resolved adb binary path")
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-install", action="store_true", help="Do not auto-download")
    p.set_defaults(func=cmd_which)

    p = sub.add_parser("devices", help="List connected devices")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_devices)

    p = sub.add_parser("shot", help="Capture screenshot")
    p.add_argument("-o", "--output", type=Path)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_shot)

    p = sub.add_parser("analyze", help="Screenshot (+ optional UI dump) for AI")
    p.add_argument("--ui", action="store_true")
    p.set_defaults(func=cmd_analyze)

    p = sub.add_parser("tap", help="Tap at coordinates")
    p.add_argument("x", type=int)
    p.add_argument("y", type=int)
    p.set_defaults(func=cmd_tap)

    p = sub.add_parser("swipe", help="Swipe gesture")
    p.add_argument("x1", type=int)
    p.add_argument("y1", type=int)
    p.add_argument("x2", type=int)
    p.add_argument("y2", type=int)
    p.add_argument("duration", type=int, nargs="?", default=300)
    p.set_defaults(func=cmd_swipe)

    p = sub.add_parser("input", help="Type text")
    p.add_argument("text")
    p.set_defaults(func=cmd_input)

    p = sub.add_parser("key", help="Send keyevent (HOME, BACK, or code)")
    p.add_argument("key")
    p.set_defaults(func=cmd_key)

    p = sub.add_parser("unlock", help="Unlock a numeric-PIN lockscreen (wake/swipe/PIN/Enter)")
    p.add_argument("--pin", default=None, help="PIN digits (or set ANDROID_UNLOCK_PIN)")
    p.set_defaults(func=cmd_unlock)

    p = sub.add_parser("shell", help="Run adb shell command")
    p.add_argument("command")
    p.set_defaults(func=cmd_shell)

    p = sub.add_parser("install", help="Install APK")
    p.add_argument("apk")
    p.set_defaults(func=cmd_install)

    p = sub.add_parser("start", help="Start activity COMPONENT")
    p.add_argument("component", help="package/.Activity")
    p.set_defaults(func=cmd_start)

    p = sub.add_parser("ui-dump", help="Dump UI hierarchy XML")
    p.add_argument("-o", "--output", type=Path)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_ui_dump)

    p = sub.add_parser("pull", help="Pull file from device")
    p.add_argument("remote")
    p.add_argument("local")
    p.set_defaults(func=cmd_pull)

    p = sub.add_parser("push", help="Push file to device")
    p.add_argument("local")
    p.add_argument("remote")
    p.set_defaults(func=cmd_push)

    p = sub.add_parser("logcat", help="Show recent logcat")
    p.add_argument("-n", "--lines", type=int, default=50)
    p.set_defaults(func=cmd_logcat)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except AdbkitError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
