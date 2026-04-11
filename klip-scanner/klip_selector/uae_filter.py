"""Lightweight UAE / brand-safety gate for product rows (keyword blocklist)."""

from __future__ import annotations

import re
from typing import Any, Mapping

# Expand over time; keep conservative for affiliate compliance.
_BLOCKED_SUBSTRINGS = (
    "casino",
    "gambling",
    "betting",
    "poker",
    "cbd ",
    "cannabis",
    "weapon",
    "firearm",
    "adult",
    "xxx",
    "escort",
)


def _text(row: Mapping[str, Any]) -> str:
    parts = [
        str(row.get("title") or ""),
        str(row.get("url") or ""),
        str(row.get("category") or ""),
    ]
    return " ".join(parts).lower()


def passes(row: Mapping[str, Any]) -> bool:
    t = _text(row)
    for w in _BLOCKED_SUBSTRINGS:
        if w in t:
            return False
    if re.search(r"\b(gun|rifle|ammo)\b", t):
        return False
    return True
