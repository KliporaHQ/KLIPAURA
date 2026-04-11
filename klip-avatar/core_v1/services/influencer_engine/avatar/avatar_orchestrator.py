"""
Influencer Engine — Multi-Avatar Orchestration.

Run multiple avatars in parallel; assign niches; prevent content overlap.
Uses scheduler + merged avatars; no duplicate topics per time window.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

REDIS_TOPIC_LOCK = "ie:orchestrator:topic:{}"
REDIS_TOPIC_TTL = 3600


def _redis():
    try:
        from klipaura_core.infrastructure.redis_client import get_redis_client
        return get_redis_client()
    except Exception:
        return None


def get_active_avatars_by_niche() -> Dict[str, List[str]]:
    """Return { niche: [avatar_id, ...] } for active avatars. Prevents overlap by assigning niches."""
    try:
        from services.influencer_engine.scheduler.influencer_scheduler import _load_avatar_profiles
    except Exception:
        from ..scheduler.influencer_scheduler import _load_avatar_profiles
    profiles = _load_avatar_profiles()
    avatars = profiles.get("avatars") or {}
    by_niche: Dict[str, List[str]] = {}
    for aid, prof in avatars.items():
        if (prof.get("status") or {}).get("active", True) is False:
            continue
        niche = (prof.get("niche") or "general").strip()
        if niche not in by_niche:
            by_niche[niche] = []
        by_niche[niche].append(aid)
    return by_niche


def topic_in_use(topic: str, exclude_avatar_id: Optional[str] = None) -> bool:
    """Check if topic is currently locked (another avatar using it) to prevent overlap."""
    r = _redis()
    if not r:
        return False
    key = REDIS_TOPIC_LOCK.format(topic.strip().lower()[:64])
    try:
        val = r.get(key)
        if not val:
            return False
        return val.decode() if isinstance(val, bytes) else val != exclude_avatar_id
    except Exception:
        return False


def lock_topic(topic: str, avatar_id: str) -> None:
    """Mark topic as in use by avatar_id for TTL seconds."""
    r = _redis()
    if not r or not topic:
        return
    key = REDIS_TOPIC_LOCK.format(topic.strip().lower()[:64])
    try:
        r.set(key, avatar_id)
        r.expire(key, REDIS_TOPIC_TTL)
    except Exception:
        pass


def get_orchestration_plan(limit_per_niche: int = 2) -> List[Dict[str, Any]]:
    """
    Return plan: list of { avatar_id, niche, suggested_topics_count }.
    Used by scheduler to distribute work without overlap.
    """
    by_niche = get_active_avatars_by_niche()
    plan = []
    for niche, avatar_ids in by_niche.items():
        for aid in avatar_ids[:5]:
            plan.append({
                "avatar_id": aid,
                "niche": niche,
                "suggested_topics_count": limit_per_niche,
            })
    return plan
