"""Chat-id allow-list for the Telegram relay.

Restricts who can drive the bot — session-control commands type into the
operator's live terminal, so only authorized chats should reach the handlers.

Resolution: `TG_RELAY_ALLOWED_CHAT_IDS` (comma/semicolon-separated) is the
allow-list; if unset it falls back to the owner's `TELEGRAM_CHAT_ID`. An empty
result means "not configured" → allow all (backward compat), and the relay
logs a warning at startup so the operator can lock it down.
"""
from __future__ import annotations


def parse_ids(raw: str) -> frozenset[int]:
    """Parse a comma/semicolon-separated list of chat ids; skip invalid tokens."""
    out: set[int] = set()
    for tok in (raw or "").replace(";", ",").split(","):
        tok = tok.strip()
        if not tok:
            continue
        try:
            out.add(int(tok))
        except ValueError:
            continue
    return frozenset(out)


def resolve_allowlist(allowed_raw: str, owner_chat: str = "") -> frozenset[int]:
    """Allowed chat ids: explicit list wins, else the owner's chat id."""
    ids = parse_ids(allowed_raw)
    if ids:
        return ids
    return parse_ids(owner_chat)


def is_allowed(chat_id: int | None, allowed: frozenset[int]) -> bool:
    """True when no allow-list is configured (empty) or chat_id is in it."""
    if not allowed:
        return True  # not configured → allow all
    return chat_id in allowed
