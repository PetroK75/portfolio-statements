"""Parser registry. Each parser module exposes BANK, NAME, looks_like(text) and
parse(raw_bytes, file_name) -> ParsedStatement. All parsing is local code: no
statement content is sent to any external service."""
from __future__ import annotations

from typing import Optional

from core.parsers import ibkr

PARSERS = [ibkr]  # add saxo, hl, sq here as they're written


def detect(raw: bytes, file_name: str = "") -> Optional[object]:
    name = file_name.lower()
    if name.endswith((".csv", ".txt")):
        text = raw.decode("utf-8-sig", errors="replace")
        for p in PARSERS:
            if hasattr(p, "looks_like") and p.looks_like(text):
                return p
    return None


def by_bank(bank: str):
    return [p for p in PARSERS if p.BANK == bank]
