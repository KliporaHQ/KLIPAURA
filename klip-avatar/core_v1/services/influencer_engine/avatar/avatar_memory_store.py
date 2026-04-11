"""
Influencer Engine — Avatar Memory & Learning Store.

Tracks engagement per content type, hook performance, retention, platform success.
Exposes get_avatar_insights(avatar_id) and update_avatar_learning(avatar_id, metrics).
Uses performance_store + strategy_memory; Redis or in-memory.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

REDIS_PREFIX = "ie:avatar:learning:"
KEY_INSIGHTS = REDIS_PREFIX + "{}"


def _redis():
    try:
        from klipaura_core.infrastructure.redis_client import get_redis_client
        return get_redis_client()
    except Exception:
        return None


def get_avatar_insights(avatar_id: str) -> Dict[str, Any]:
    """
    Return aggregated insights for avatar: engagement by content type, hook performance,
    retention, platform success, trend success %, suggested improvements.
    """
    out: Dict[str, Any] = {
        "avatar_id": avatar_id,
        "total_views": 0,
        "avg_engagement_rate": 0.0,
        "conversion_rate": 0.0,
        "trend_success_pct": 0.0,
        "by_platform": {},
        "by_content_type": {},
        "top_hooks": [],
        "what_worked": [],
        "what_failed": [],
        "suggested_improvements": [],
    }
    try:
        from services.influencer_engine.analytics.performance_store import list_recent
        from services.influencer_engine.learning.strategy_memory import get_strategy
    except Exception:
        from ..analytics.performance_store import list_recent
        from ..learning.strategy_memory import get_strategy

    perf_list = list_recent(avatar_id=avatar_id, limit=100)
    if not perf_list:
        return out

    total_score = 0.0
    total_views = 0
    engagement_sum = 0.0
    engagement_count = 0
    by_platform: Dict[str, Dict[str, Any]] = {}
    hook_scores: Dict[str, List[float]] = {}

    for rec in perf_list:
        metrics = rec.get("metrics") or {}
        views = int(metrics.get("views") or 0)
        score = float(rec.get("score") or 0)
        platform = (rec.get("platform") or "unknown").strip()
        hook = (rec.get("hook") or "").strip() or "(none)"
        topic = (rec.get("topic") or "").strip()

        total_views += views
        total_score += score
        eng = metrics.get("engagement_rate")
        if eng is not None:
            engagement_sum += float(eng)
            engagement_count += 1

        if platform not in by_platform:
            by_platform[platform] = {"views": 0, "score_sum": 0.0, "count": 0}
        by_platform[platform]["views"] += views
        by_platform[platform]["score_sum"] += score
        by_platform[platform]["count"] += 1

        if hook not in hook_scores:
            hook_scores[hook] = []
        hook_scores[hook].append(score)

    out["total_views"] = total_views
    out["avg_engagement_rate"] = round(engagement_sum / engagement_count, 4) if engagement_count else 0.0
    n = len(perf_list)
    out["avg_score"] = round(total_score / n, 4) if n else 0.0
    out["trend_success_pct"] = round((sum(1 for r in perf_list if (r.get("score") or 0) >= 0.5) / n) * 100, 1) if n else 0.0
    out["by_platform"] = {
        p: {"views": d["views"], "avg_score": round(d["score_sum"] / d["count"], 4) if d["count"] else 0}
        for p, d in by_platform.items()
    }

    top_hooks = sorted(
        [(h, sum(s) / len(s)) for h, s in hook_scores.items() if s],
        key=lambda x: -x[1],
    )[:10]
    out["top_hooks"] = [{"hook": h, "avg_score": round(s, 4)} for h, s in top_hooks]

    strategy = get_strategy(avatar_id)
    out["what_worked"] = (strategy.get("best_topics") or [])[:5] + (strategy.get("best_hooks") or [])[:3]
    out["what_failed"] = []
    out["suggested_improvements"] = [
        f"Focus on best platform: {strategy.get('best_platform', 'youtube_shorts')}",
        "Increase posting on top-performing topics",
    ]

    r = _redis()
    if r:
        try:
            import json
            cached = r.get(KEY_INSIGHTS.format(avatar_id))
            if cached:
                prev = json.loads(cached)
                out["last_updated"] = prev.get("last_updated")
        except Exception:
            pass

    return out


def update_avatar_learning(avatar_id: str, metrics: Dict[str, Any]) -> None:
    """
    Update learning store with new metrics (e.g. after analytics).
    Persists aggregated insights to Redis for dashboard.
    """
    insights = get_avatar_insights(avatar_id)
    insights["last_updated"] = __import__("time").time()
    r = _redis()
    if r:
        try:
            import json
            r.set(KEY_INSIGHTS.format(avatar_id), json.dumps(insights))
            r.expire(KEY_INSIGHTS.format(avatar_id), 86400 * 30)
        except Exception:
            pass
