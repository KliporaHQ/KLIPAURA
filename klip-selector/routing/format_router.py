"""Map product category/network → VideoFormat class name.

Format names must match FORMAT_REGISTRY keys in klip-avatar/core_v1/pipeline/format_engine.py (Phase 4).
"""

from __future__ import annotations

import os

_DEFAULT_FORMAT = os.getenv("VIDEO_FORMAT_DEFAULT", "SplitFormat")

# Category → VideoFormat name
_CATEGORY_MAP: dict[str, str] = {
    "beauty": "SplitFormat",
    "skincare": "SplitFormat",
    "wellness": "SplitFormat",
    "home": "SplitFormat",
    "kitchen": "SplitFormat",
    "electronics": "SplitFormat",
    "fashion": "FullscreenFormat",
    "fitness": "LipsyncFormat",
    "digital": "StaticNarrationFormat",
    "software": "StaticNarrationFormat",
    "health": "SplitFormat",
    "lifestyle": "SplitFormat",
    "budget": "SplitFormat",
}

# Network overrides (takes priority over category)
_NETWORK_MAP: dict[str, str] = {
    "temu": "SplitFormat",
    "amazon": "SplitFormat",
    "clickbank": "StaticNarrationFormat",
    "manual": "SplitFormat",
}


def format_for_product(category: str, network: str = "") -> str:
    """Return the VideoFormat class name for the given category/network.

    Priority: network override → category → VIDEO_FORMAT_DEFAULT env → 'SplitFormat'.
    """
    net = (network or "").strip().lower()
    cat = (category or "").strip().lower()

    if net and net in _NETWORK_MAP:
        return _NETWORK_MAP[net]
    if cat and cat in _CATEGORY_MAP:
        return _CATEGORY_MAP[cat]
    return _DEFAULT_FORMAT
