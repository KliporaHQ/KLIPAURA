"""
Influencer Engine — Auto-scaling decision.

If performance ↑ and revenue ↑ → increase videos/day.
Else → reduce output.
"""

from __future__ import annotations

from typing import Any, Dict

DEFAULT_VIDEOS_PER_DAY = 10
MIN_VIDEOS_PER_DAY = 1
MAX_VIDEOS_PER_DAY = 50


def compute_recommended_videos_per_day(
    recent_performance: list,
    recent_revenue: float,
    current_videos_per_day: int,
    performance_trend: str = "stable",
    revenue_trend: str = "stable",
) -> int:
    """
    Recommend videos_per_day based on performance and revenue trends.
    performance_trend / revenue_trend: "up" | "down" | "stable".
    """
    out = current_videos_per_day or DEFAULT_VIDEOS_PER_DAY
    if performance_trend == "up" and revenue_trend == "up":
        out = min(MAX_VIDEOS_PER_DAY, out + 2)
    elif performance_trend == "up" or revenue_trend == "up":
        out = min(MAX_VIDEOS_PER_DAY, out + 1)
    elif performance_trend == "down" and revenue_trend == "down":
        out = max(MIN_VIDEOS_PER_DAY, out - 2)
    elif performance_trend == "down" or revenue_trend == "down":
        out = max(MIN_VIDEOS_PER_DAY, out - 1)
    return out


def trend_from_scores(scores: list) -> str:
    """Return 'up' | 'down' | 'stable' from list of recent scores."""
    if not scores or len(scores) < 2:
        return "stable"
    half = len(scores) // 2
    first_half_avg = sum(scores[:half]) / half if half else 0
    second_half_avg = sum(scores[half:]) / (len(scores) - half) if (len(scores) - half) else 0
    if second_half_avg > first_half_avg + 0.05:
        return "up"
    if second_half_avg < first_half_avg - 0.05:
        return "down"
    return "stable"
