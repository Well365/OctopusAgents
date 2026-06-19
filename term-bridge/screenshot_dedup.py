"""Similarity-based dedup for terminal screenshots sent to Telegram.

The monitor can capture every poll; without dedup it streams near-identical
images whenever the terminal churns (cursor blink, a redrawn line). Exact-hash
dedup misses those, so we compare a downscaled grayscale *fingerprint* and skip
a send when the new frame is >= 95% similar to the last one sent. The caller
writes the fingerprint only after a successful send, so a failed send retries.

Fingerprint = pixels of the image resized to 32x32 grayscale. Similarity = mean
per-pixel closeness (1 - |a-b|/255) over the 1024 pixels, in [0, 1].
"""
from __future__ import annotations

import io
from pathlib import Path

from PIL import Image

_THUMB = (32, 32)
DEFAULT_THRESHOLD = 0.95


def fingerprint(data: bytes) -> list[int]:
    """Downscaled grayscale pixel fingerprint of a PNG's bytes."""
    img = Image.open(io.BytesIO(data)).convert("L").resize(_THUMB)
    return list(img.tobytes())  # mode "L" → one byte per pixel, row-major


def similarity(a: list[int], b: list[int]) -> float:
    """Mean per-pixel closeness in [0,1]; 0 if shapes differ or either empty."""
    if not a or not b or len(a) != len(b):
        return 0.0
    total = sum(1.0 - abs(x - y) / 255.0 for x, y in zip(a, b))
    return total / len(a)


def is_duplicate(fp: list[int], last_fp: list[int], threshold: float = DEFAULT_THRESHOLD) -> bool:
    """True when the new fingerprint is >= threshold similar to the last one."""
    return similarity(fp, last_fp) >= threshold


def read_fingerprint(state_file: str | Path) -> list[int]:
    """Last-sent fingerprint, or [] if none/unreadable."""
    try:
        raw = Path(state_file).read_text(encoding="utf-8").strip()
    except OSError:
        return []
    if not raw:
        return []
    try:
        return [int(x) for x in raw.split(",")]
    except ValueError:
        return []


def write_fingerprint(state_file: str | Path, fp: list[int]) -> None:
    """Record the last-sent fingerprint (call only after a successful send)."""
    p = Path(state_file)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(",".join(str(v) for v in fp), encoding="utf-8")
