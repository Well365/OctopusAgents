"""Save Telegram media to disk and build the text injected to the agent.

Telegram supports sending photos/files; the relay downloads them under
inbox/attachments/ and injects the saved path (plus any caption) so the agent
(e.g. Claude Code) can read the image/file. Pure helpers here; the relay glue
does the async download.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def attachment_dir() -> Path:
    return ROOT / "inbox" / "attachments"


def dest_path(unique_id: str, ext: str, directory: Path | None = None) -> Path:
    d = directory or attachment_dir()
    safe = "".join(c for c in unique_id if c.isalnum() or c in "-_") or "file"
    ext = ext if ext.startswith(".") else "." + ext
    return d / f"{safe}{ext}"


def inject_text(path: str, caption: str = "") -> str:
    base = f"请查看附件文件: {path}"
    if caption.strip():
        base += f"\n说明: {caption.strip()}"
    return base
