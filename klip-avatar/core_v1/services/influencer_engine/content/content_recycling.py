"""
Influencer Engine — Content Recycling.

Reuse high-performing scripts; auto-reformat for other platforms.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

def get_recyclable_scripts(avatar_id: str, min_score: float = 0.6, limit: int = 20) -> List[Dict[str, Any]]:
    """Return top-performing scripts that can be reused for other platforms."""
    try:
        from services.influencer_engine.analytics.performance_store import list_recent
        from services.influencer_engine.learning.strategy_memory import get_strategy
    except Exception:
        from ..analytics.performance_store import list_recent
        from ..learning.strategy_memory import get_strategy  # type: ignore
    perf = list_recent(avatar_id=avatar_id, limit=limit * 2)
    strategy = get_strategy(avatar_id)
    best_platform = (strategy.get("best_platform") or "youtube_shorts").strip()
    out = []
    for rec in perf:
        if (rec.get("score") or 0) < min_score:
            continue
        out.append({
            "topic": rec.get("topic"),
            "hook": rec.get("hook"),
            "platform": rec.get("platform"),
            "score": rec.get("score"),
            "video_id": rec.get("video_id"),
        })
        if len(out) >= limit:
            break
    return out


def suggest_platform_variants(script_topic: str, current_platform: str) -> List[str]:
    """Suggest other platforms to recycle this topic to."""
    all_platforms = ["youtube_shorts", "tiktok", "instagram_reels", "x"]
    return [p for p in all_platforms if p != current_platform]
