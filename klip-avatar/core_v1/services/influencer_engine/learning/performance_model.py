"""
Influencer Engine — Performance score model.

Normalizes raw metrics into a 0.0–1.0 performance score for strategy learning and A/B comparison.
"""

from __future__ import annotations

from typing import Any, Dict

# Weights for score (tune per platform if needed)
VIEWS_WEIGHT = 0.00001      # scale down raw views
ENGAGEMENT_WEIGHT = 2.0     # engagement_rate already 0–1
WATCH_TIME_WEIGHT = 0.001   # per second
FOLLOWER_GROWTH_WEIGHT = 0.01

# Normalization caps (above these count as 1.0 contribution)
MAX_VIEWS = 100_000
MAX_WATCH_TIME = 300.0
MAX_FOLLOWER_GROWTH = 1000


def calculate_performance_score(metrics: Dict[str, Any]) -> float:
    """
    Compute normalized performance score from metrics.

    Formula:
        score = views_weight * norm(views) +
                engagement_weight * engagement_rate +
                watch_time_weight * norm(watch_time) +
                follower_weight * norm(follower_growth)
    Then clamp to [0.0, 1.0].

    Returns:
        Float in [0.0, 1.0].
    """
    if not metrics:
        return 0.0

    views = float(metrics.get("views") or 0)
    engagement_rate = float(metrics.get("engagement_rate") or 0)
    watch_time = float(metrics.get("watch_time") or 0)
    follower_growth = int(metrics.get("follower_growth") or 0)

    norm_views = min(1.0, views / MAX_VIEWS) if MAX_VIEWS else 0.0
    norm_watch = min(1.0, watch_time / MAX_WATCH_TIME) if MAX_WATCH_TIME else 0.0
    norm_followers = min(1.0, follower_growth / MAX_FOLLOWER_GROWTH) if MAX_FOLLOWER_GROWTH else 0.0

    raw = (
        VIEWS_WEIGHT * views
        + ENGAGEMENT_WEIGHT * engagement_rate
        + WATCH_TIME_WEIGHT * watch_time
        + FOLLOWER_GROWTH_WEIGHT * follower_growth
    )
    # Normalize by weighted caps so max theoretical ≈ 1
    max_raw = (
        VIEWS_WEIGHT * MAX_VIEWS
        + ENGAGEMENT_WEIGHT * 1.0
        + WATCH_TIME_WEIGHT * MAX_WATCH_TIME
        + FOLLOWER_GROWTH_WEIGHT * MAX_FOLLOWER_GROWTH
    )
    score = raw / max_raw if max_raw else 0.0
    return max(0.0, min(1.0, score))
