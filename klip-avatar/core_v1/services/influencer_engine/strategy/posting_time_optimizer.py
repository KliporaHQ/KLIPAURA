"""
Influencer Engine — Posting time optimizer.

Optimal posting time and video length from strategy/analytics.
get_best_posting_time(avatar, platform) for scheduler.
"""

from __future__ import annotations

from typing import Any, Dict

# Default windows per platform (UTC hour); can be overridden by strategy/analytics
DEFAULT_BEST_HOURS = {
    "youtube_shorts": (10, 14, 18),
    "youtube": (10, 14, 18),
    "tiktok": (12, 17, 21),
    "instagram": (11, 15, 19),
    "x": (8, 13, 17),
    "twitter": (8, 13, 17),
}

try:
    from ..learning.strategy_memory import get_strategy
except Exception:
    get_strategy = lambda _: {}


def get_best_posting_time(avatar_id: str, platform: str) -> str:
    """
    Return best posting time window for avatar and platform (e.g. "10:00" UTC).
    Scheduler should schedule jobs based on optimal time windows.
    """
    s = get_strategy(avatar_id)
    if s.get("best_posting_time"):
        return str(s["best_posting_time"])
    platform = (platform or "youtube_shorts").lower().replace(" ", "_")
    hours = DEFAULT_BEST_HOURS.get(platform) or DEFAULT_BEST_HOURS.get("youtube_shorts") or (10, 18)
    return f"{hours[0]:02d}:00"


class PostingTimeOptimizer:
    """Recommends posting time and video length."""

    def optimize(
        self,
        avatar_id: str,
        strategy_memory: Dict[str, Any],
    ) -> str:
        """Return recommended posting time (e.g. 10:00 UTC, 18:00 UTC)."""
        s = strategy_memory or get_strategy(avatar_id)
        return s.get("best_posting_time") or "10:00"

    def recommended_video_length(
        self,
        avatar_id: str,
        strategy_memory: Dict[str, Any],
    ) -> float:
        """Return recommended video length in seconds (e.g. 60 for Shorts)."""
        s = strategy_memory or get_strategy(avatar_id)
        return float(s.get("best_video_length_seconds") or 60.0)
