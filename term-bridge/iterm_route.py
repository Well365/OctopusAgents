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

from iterm_target import ItermTarget, resolve_target  # noqa: E402
from target_default import read_default  # noqa: E402
from term_backend import resolve_backend  # noqa: E402

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
    session_id: str | None = None  # stable id: iTerm GUID / Terminal tty

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


def _list_targets_for_backend() -> tuple[int, list[dict]]:
    """Enumerate tabs of whichever terminal TG_TERM_BACKEND selects."""
    if resolve_backend() == "terminal":
        from terminal_tabs import list_targets
    else:
        from iterm_tabs import list_targets
    return list_targets()


def list_tabs() -> tuple[int, list[TabInfo]]:
    code, rows = _list_targets_for_backend()
    if code != 0:
        return code, []
    return 0, [
        TabInfo(r["window"], r["tab"], r["name"], r.get("sessions", 1), r.get("session_id"))
        for r in rows
    ]


def _parse_aliases() -> dict[str, ItermTarget]:
    """TG_ITERM_ALIASES=work:1:7,mobile:1:11,task:1:4  (alias:window:tab)"""
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
    """Persistent /tab default, anchored on the stable session_id when present.

    When the default carries a session_id, we look it up in the live tab list and
    return that session's *current* window/tab — so reordering or closing other
    tabs can't misroute. If the session_id is gone (tab closed), fall back to the
    .env default. Without a session_id (legacy state), keep the old positional
    presence check.
    """
    d = read_default()
    if d is None:
        return resolve_target()

    if d.session_id:
        code, tabs = list_tabs()
        if code != 0 or not tabs:
            return d  # can't enumerate (e.g. non-macOS test); trust stored target
        for t in tabs:
            if t.session_id and t.session_id == d.session_id:
                return ItermTarget(window=t.window, tab=t.tab, session_id=t.session_id)
        return resolve_target()  # the anchored session is gone

    if d.window is not None:
        code, tabs = list_tabs()
        if code == 0 and tabs and not any(t.window == d.window and t.tab == d.tab for t in tabs):
            return resolve_target()
    return d


@dataclass(frozen=True)
class RouteResult:
    """Outcome of parsing a routing prefix.

    `unmatched_prefix` is the raw prefix key the user typed, set ONLY when a
    routing prefix was present, tabs were enumerable, and no tab matched. It is
    None when no prefix was present, the prefix matched a tab, or tabs could not
    be enumerated (e.g. non-macOS/test) — in those cases we genuinely cannot
    tell the prefix was wrong, so we must not falsely reject it.
    """

    target: ItermTarget
    body: str
    hit: TabInfo | None
    unmatched_prefix: str | None


def _route_for_tab(
    tabs: list[TabInfo], key: str, rest: str, default: ItermTarget
) -> RouteResult:
    """Resolve a numeric tab prefix against an already-enumerated tab list."""
    try:
        n = int(key)
    except ValueError:
        return RouteResult(default, rest, None, key)
    hit = _find_by_tab_number(tabs, n, window=default.window)
    if hit:
        target = ItermTarget(window=hit.window, tab=hit.tab, session_id=hit.session_id)
        return RouteResult(target, rest, hit, None)
    return RouteResult(default, rest, None, key)


def _route_for_alias(
    tabs: list[TabInfo], key: str, rest: str, default: ItermTarget
) -> RouteResult:
    """Resolve an alias/key prefix against an already-enumerated tab list.

    The `[word]` / `@word:` pattern also matches ordinary bracketed text like
    `[TODO]`, `[x]`, `[1]`, so an unmatched key is only treated as a *routing
    miss* (refused) when it's a CONFIGURED alias (TG_ITERM_ALIASES) whose tab
    is gone. A generic unmatched word is not routing intent → inject the body
    into the default tab (unmatched_prefix stays None), matching legacy behavior.
    """
    hit = _find_by_key(tabs, key)
    if hit:
        target = ItermTarget(window=hit.window, tab=hit.tab, session_id=hit.session_id)
        return RouteResult(target, rest, hit, None)
    is_configured_alias = key.strip().lower() in _parse_aliases()
    return RouteResult(default, rest, None, key if is_configured_alias else None)


def route_message(text: str) -> RouteResult:
    """
    Parse routing prefix from message into a RouteResult.

    Falls back to resolve_target() from .env when no prefix. Sets
    unmatched_prefix to the typed key only when a prefix was present, the tab
    list was enumerable, and nothing matched — letting callers distinguish
    "no prefix → default" from "prefix given but unmatched".
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
            return RouteResult(default, body, None, None)

        code, tabs = list_tabs()
        if code != 0 or not tabs:
            # Can't enumerate tabs; we genuinely can't tell the prefix is wrong.
            return RouteResult(default, rest, None, None)

        if kind == "tab":
            return _route_for_tab(tabs, key, rest, default)
        return _route_for_alias(tabs, key, rest, default)

    return RouteResult(default, body, None, None)


def parse_routed_message(text: str) -> tuple[ItermTarget, str, TabInfo | None]:
    """
    Parse routing prefix from message.
    Returns (target, body_without_prefix, matched_tab_info_or_none).
    Falls back to resolve_target() from .env when no prefix.

    Thin wrapper over route_message; behavior is byte-for-byte identical for
    existing callers. Use route_message when you need unmatched_prefix.
    """
    r = route_message(text)
    return r.target, r.body, r.hit


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
