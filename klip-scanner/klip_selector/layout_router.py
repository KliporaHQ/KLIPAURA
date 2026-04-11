"""Map product category → render layout hint (for future pipeline routing)."""

from __future__ import annotations

_DEFAULT = "ken_burns_slideshow_9x16"

_MAP = {
    "beauty": _DEFAULT,
    "skincare": _DEFAULT,
    "lifestyle": _DEFAULT,
    "home": _DEFAULT,
    "kitchen": _DEFAULT,
    "fitness": _DEFAULT,
    "electronics": "ken_burns_slideshow_9x16",
}


def layout_for_category(category: str) -> str:
    c = (category or "").strip().lower()
    return _MAP.get(c, _DEFAULT)
