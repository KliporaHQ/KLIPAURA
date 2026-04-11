"""
Influencer Engine — Platform optimizer.

Selects optimal platform from strategy memory.
"""

from __future__ import annotations

from typing import Any, Dict

try:
    from ..learning.strategy_memory import get_strategy
except Exception:
    get_strategy = lambda _: {}


class PlatformOptimizer:
    """Recommends platform for an avatar."""

    def optimize(
        self,
        avatar_id: str,
        strategy_memory: Dict[str, Any],
    ) -> str:
        """Return best platform (e.g. youtube_shorts, tiktok, x)."""
        s = strategy_memory or get_strategy(avatar_id)
        return (s.get("best_platform") or "youtube_shorts").strip() or "youtube_shorts"
