"""
Influencer Engine — Feedback ingestion loop.

Flow: fetch latest metrics → compute performance score → update strategy_memory → emit CONTENT_PERFORMANCE.
"""

from __future__ import annotations

from typing import Any, Dict, List

SERVICE_ID = "influencer_engine"


def _emit(event_type: str, payload: Dict[str, Any]) -> None:
    try:
        from core.service_manager.utils.service_utils import event_publish
        event_publish(event_type, payload)
    except Exception:
        pass
    try:
        from core.service_manager.utils.event_bus_publisher import get_publisher
        pub = get_publisher()
        if pub is not None:
            pub.publish(event_type, payload, source=SERVICE_ID)
    except Exception:
        pass


def _compute_performance_score(metrics: Dict[str, Any]) -> float:
    """Compute 0..1 score from views, likes, comments, engagement_rate."""
    views = int(metrics.get("views") or 0)
    likes = int(metrics.get("likes") or 0)
    comments = int(metrics.get("comments") or 0)
    engagement = float(metrics.get("engagement_rate") or 0)
    if views <= 0:
        return 0.0
    # Simple composite: normalize views (e.g. 10k = 1), weight engagement
    view_score = min(1.0, views / 10000.0)
    eng_score = min(1.0, engagement * 10)  # 10% engagement -> 1
    return round(0.5 * view_score + 0.5 * eng_score, 4)


def process_feedback(
    post_ids: List[str] | None = None,
) -> Dict[str, Any]:
    """
    Fetch latest metrics for tracked posts, compute performance score,
    update strategy_memory, emit CONTENT_PERFORMANCE.

    If post_ids is None, discover tracked posts from post_tracker (e.g. recent).
    """
    try:
        from ..analytics.post_tracker import get_post_meta, get_metrics_over_time, update_metrics
        from ..analytics.performance_store import save_performance, list_recent
        from ..learning.strategy_memory import update_from_performance, get_strategy
    except Exception:
        from services.influencer_engine.analytics.post_tracker import get_post_meta, get_metrics_over_time, update_metrics
        from services.influencer_engine.analytics.performance_store import save_performance, list_recent
        from services.influencer_engine.learning.strategy_memory import update_from_performance, get_strategy

    processed = 0
    events_emitted = 0
    if not post_ids:
        try:
            from ..analytics.post_tracker import list_tracked_post_ids
        except Exception:
            from services.influencer_engine.analytics.post_tracker import list_tracked_post_ids
        post_ids = list_tracked_post_ids(limit=100)

    for post_id in post_ids or []:
        meta = get_post_meta(post_id)
        if not meta:
            continue
        platform = meta.get("platform") or "youtube_shorts"
        latest = update_metrics(post_id, platform, {})
        score = _compute_performance_score(latest)
        video_id = meta.get("video_id") or post_id
        topic = meta.get("topic") or ""
        hook = meta.get("hook") or ""
        avatar_id = meta.get("avatar_id") or ""
        save_performance(
            video_id=video_id,
            topic=topic,
            hook=hook,
            platform=platform,
            metrics=latest,
            score=score,
            avatar_id=avatar_id,
        )
        update_from_performance(avatar_id, "", topic, hook, platform, score)
        _emit("CONTENT_PERFORMANCE", {
            "service_id": SERVICE_ID,
            "avatar": avatar_id,
            "post_id": post_id,
            "video_id": video_id,
            "platform": platform,
            "performance_metrics": latest,
            "performance_score": score,
        })
        processed += 1
        events_emitted += 1

    return {"processed": processed, "events_emitted": events_emitted}


def process_feedback_for_post(post_id: str, platform: str) -> Dict[str, Any]:
    """Convenience: run feedback for a single post (e.g. after publish)."""
    return process_feedback(post_ids=[post_id])
