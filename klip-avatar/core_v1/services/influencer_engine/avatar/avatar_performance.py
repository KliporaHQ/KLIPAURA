"""
Influencer Engine — Avatar Performance.

Tracks per-avatar: avg_views, avg_engagement, growth_rate, revenue.
Computes a single avatar_score for lifecycle decisions (scale / maintain / kill).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

# Import from analytics (sibling); avoid core
try:
    from ..analytics.performance_store import get_recent_metrics
except Exception:
    get_recent_metrics = lambda avatar_id, limit=50: []  # type: ignore


def _aggregate_metrics(records: List[Dict[str, Any]]) -> Dict[str, float]:
    """Compute avg_views, avg_engagement, growth_rate from performance records."""
    if not records:
        return {"avg_views": 0.0, "avg_engagement": 0.0, "growth_rate": 0.0, "revenue": 0.0}

    views_list: List[float] = []
    engagement_list: List[float] = []
    scores: List[float] = []

    for r in records:
        metrics = r.get("metrics") or {}
        v = metrics.get("views") or metrics.get("view_count") or 0
        views_list.append(float(v))
        eng = metrics.get("engagement_rate") or metrics.get("engagement") or 0
        engagement_list.append(float(eng))
        scores.append(float(r.get("score") or 0))

    n = len(records)
    avg_views = sum(views_list) / n if n else 0.0
    avg_engagement = sum(engagement_list) / n if n else 0.0
    avg_score = sum(scores) / n if n else 0.0

    # Simple growth: compare first half vs second half of window (if enough data)
    growth_rate = 0.0
    if n >= 4:
        mid = n // 2
        first_avg = sum(views_list[:mid]) / mid if mid else 0
        second_avg = sum(views_list[mid:]) / (n - mid) if (n - mid) else 0
        if first_avg > 0:
            growth_rate = (second_avg - first_avg) / first_avg

    # Revenue: sum from metrics.revenue if present
    revenue = 0.0
    for r in records:
        rev = (r.get("metrics") or {}).get("revenue")
        if rev is not None:
            revenue += float(rev)

    return {
        "avg_views": avg_views,
        "avg_engagement": avg_engagement,
        "growth_rate": growth_rate,
        "revenue": revenue,
        "avg_score": avg_score,
        "sample_count": n,
    }


def compute_avatar_score(avatar_id: str, limit: int = 50) -> float:
    """
    Compute a single score in [0, 1] for the avatar from recent performance.
    Used by lifecycle manager for scale / maintain / kill.
    - No data -> 0.5 (neutral, maintain).
    - Based on: avg normalized score, growth_rate, and sample count (confidence).
    """
    records = get_recent_metrics(avatar_id, limit=limit)
    agg = _aggregate_metrics(records)

    if agg["sample_count"] == 0:
        return 0.5

    # Primary signal: avg_score from performance_store (already 0–1)
    base = agg["avg_score"]
    # Boost for positive growth, penalize negative
    growth = agg["growth_rate"]
    if growth > 0:
        base = min(1.0, base + 0.1 * min(1.0, growth))
    elif growth < 0:
        base = max(0.0, base + 0.15 * max(-1.0, growth))
    return round(max(0.0, min(1.0, base)), 4)


def get_avatar_metrics(avatar_id: str, limit: int = 50) -> Dict[str, Any]:
    """Return full metrics dict for an avatar (avg_views, avg_engagement, growth_rate, revenue, avg_score, sample_count)."""
    records = get_recent_metrics(avatar_id, limit=limit)
    return _aggregate_metrics(records)
