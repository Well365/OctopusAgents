#!/usr/bin/env python3
"""Route Telegram / CLI input to a specific iTerm tab by number, alias, or directory name."""
from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "term-bridge"))

from iterm_tabs import list_targets  # noqa: E402
from iterm_target import ItermTarget, resolve_target  # noqa: E402
from target_default import read_default  # noqa: E402

# [tab:2] [t2] [mobile-agent] @2: @fz: #2
_PREFIX_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^\[tab[:\s]*(\d+)\]\s*", re.I), "tab"),
    (re.compile(r"^\[t(\d+)\]\s*", re.I), "tab"),
    (re.compile(r"^#(\d+)\s+"), "tab"),
    (re.compile(r"^@t?(\d+)\s*[:：]\s*", re.I), "tab"),
    (re.compile(r"^\[([^\]]+)\]\s*"), "alias"),
    (re.compile(r"^@([^\s:@\[\]]+)\s*[:：]\s*"), "alias"),
]


@dataclass(frozen=True)
class TabInfo:
    window: int
    tab: int
    name: str
    sessions: int = 1

    @property
    def label(self) -> str:
        return f"w{self.window}/t{self.tab}"

    def match_key(self, key: str) -> bool:
        k = key.strip().lower()
        if not k:
            return False
        name = self.name.lower()
        # directory fragment: ../mobile-agent (-zsh) -> mobile-agent
        if k in name:
            return True
        # basename style
        for part in re.split(r"[\s/\\()]+", name):
            if part and (part == k or k in part or part in k):
                return True
        return False


def list_tabs() -> tuple[int, list[TabInfo]]:
    code, rows = list_targets()
    if code != 0:
        return code, []
    return 0, [TabInfo(r["window"], r["tab"], r["name"], r.get("sessions", 1)) for r in rows]


def _parse_aliases() -> dict[str, ItermTarget]:
    """TG_ITERM_ALIASES=fz:1:7,mobile:1:11,texus:1:4  (alias:window:tab)"""
    raw = os.environ.get("TG_ITERM_ALIASES", "").strip()
    out: dict[str, ItermTarget] = {}
    if not raw:
        return out
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk or ":" not in chunk:
            continue
        parts = chunk.split(":")
        if len(parts) < 3:
            continue
        alias = parts[0].strip().lower()
        try:
            win, tab = int(parts[1]), int(parts[2])
            sess = int(parts[3]) if len(parts) > 3 else None
        except ValueError:
            continue
        out[alias] = ItermTarget(window=win, tab=tab, session=sess)
    return out


def _find_by_tab_number(tabs: list[TabInfo], n: int, window: int | None = None) -> TabInfo | None:
    if window is not None:
        for t in tabs:
            if t.window == window and t.tab == n:
                return t
    matches = [t for t in tabs if t.tab == n]
    if len(matches) == 1:
        return matches[0]
    # prefer window 1 if ambiguous
    for t in matches:
        if t.window == 1:
            return t
    return matches[0] if matches else None


def _find_by_key(tabs: list[TabInfo], key: str) -> TabInfo | None:
    key = key.strip().lower()
    if not key:
        return None
    aliases = _parse_aliases()
    if key in aliases:
        a = aliases[key]
        if a.window is not None:
            for t in tabs:
                if t.window == a.window and t.tab == a.tab:
                    return t
    hits = [t for t in tabs if t.match_key(key)]
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        # shortest name = most specific
        hits.sort(key=lambda t: len(t.name))
        return hits[0]
    return None


def _sticky_default() -> ItermTarget:
    """Persistent /tab default if set and still open, else the .env default."""
    d = read_default()
    if d is None:
        return resolve_target()
    if d.window is not None:
        code, tabs = list_tabs()
        if code == 0 and tabs and not any(t.window == d.window and t.tab == d.tab for t in tabs):
            return resolve_target()
    return d


def parse_routed_message(text: str) -> tuple[ItermTarget, str, TabInfo | None]:
    """
    Parse routing prefix from message.
    Returns (target, body_without_prefix, matched_tab_info_or_none).
    Falls back to resolve_target() from .env when no prefix.
    """
    body = text.strip()
    default = _sticky_default()

    for pat, kind in _PREFIX_PATTERNS:
        m = pat.match(body)
        if not m:
            continue
        key = m.group(1)
        rest = body[m.end() :].strip()
        if not rest:
            return default, body, None

        code, tabs = list_tabs()
        if code != 0 or not tabs:
            return default, rest, None

        if kind == "tab":
            try:
                n = int(key)
            except ValueError:
                return default, rest, None
            hit = _find_by_tab_number(tabs, n, window=default.window)
            if hit:
                return ItermTarget(window=hit.window, tab=hit.tab), rest, hit
            return default, rest, None

        hit = _find_by_key(tabs, key)
        if hit:
            return ItermTarget(window=hit.window, tab=hit.tab), rest, hit
        return default, rest, None

    return default, body, None


def format_tabs_message() -> str:
    code, tabs = list_tabs()
    if code != 0:
        return "无法读取 iTerm 标签列表"
    if not tabs:
        return "没有打开的 iTerm 窗口"

    lines = ["iTerm 标签（发消息时可加前缀区分）:\n"]
    for t in tabs:
        short = t.name.replace("\n", " ")[:60]
        lines.append(f"• tab {t.tab} ({t.label}): {short}")
        lines.append(f"  用法: [t{t.tab}] 或 [{_suggest_alias(t)}] 或 @t{t.tab}: ")
    lines.append("\n别名映射 (.env): TG_ITERM_ALIASES=fz:1:7,mobile:1:11")
    lines.append("示例: [mobile-agent] 查看 git 状态")
    return "\n".join(lines)


def _suggest_alias(t: TabInfo) -> str:
    name = t.name.lower()
    for token in re.findall(r"[a-zA-Z0-9][a-zA-Z0-9._-]+", name):
        if token in ("zsh", "bash", "ssh", "node", "python"):
            continue
        if len(token) >= 4:
            return token
    return f"tab{t.tab}"


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Test iTerm message routing")
    parser.add_argument("message", nargs="?", default="")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()
    if args.list:
        print(format_tabs_message())
        return 0
    if not args.message:
        print(format_tabs_message())
        return 0
    target, body, hit = parse_routed_message(args.message)
    print(f"target={target.label()}")
    print(f"body={body!r}")
    if hit:
        print(f"matched tab {hit.tab}: {hit.name!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
