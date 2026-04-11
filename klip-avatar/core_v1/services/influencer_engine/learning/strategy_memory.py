"""
Influencer Engine — Strategy memory.

Stores and loads best-performing strategies per avatar (best_hooks, best_topics, best_platform).
Uses Redis when available; falls back to in-memory dict.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

REDIS_PREFIX = "ie:strategy:"
KEY_AVATAR = REDIS_PREFIX + "{}"  # avatar_id
KEY_EXPERIMENT = "ie:exp:{}"  # experiment_id
KEY_EXPERIMENT_TTL = 86400 * 7  # 7 days
_MAX_TOPICS = 20
_MAX_HOOKS = 15


def _redis():
    try:
        from klipaura_core.infrastructure.redis_client import get_redis_client
        return get_redis_client()
    except Exception:
        return None


_memory_fallback: Dict[str, Dict[str, Any]] = {}


def get_strategy(avatar_id: str) -> Dict[str, Any]:
    """
    Load strategy for avatar. Returns dict with best_hooks, best_topics, best_platform.
    """
    r = _redis()
    if r:
        key = KEY_AVATAR.format(avatar_id)
        raw = r.get(key)
        if raw:
            try:
                return json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                pass
    return _memory_fallback.get(avatar_id, _default_strategy(avatar_id))


def save_strategy(avatar_id: str, strategy: Dict[str, Any]) -> bool:
    """Persist strategy for avatar (Redis or in-memory)."""
    payload = {
        "avatar": avatar_id,
        "niche": strategy.get("niche", ""),
        "best_hooks": list(strategy.get("best_hooks") or [])[: _MAX_HOOKS],
        "best_topics": list(strategy.get("best_topics") or [])[: _MAX_TOPICS],
        "best_platform": str(strategy.get("best_platform") or "youtube_shorts"),
    }
    r = _redis()
    if r:
        key = KEY_AVATAR.format(avatar_id)
        try:
            r.set(key, json.dumps(payload))
            r.expire(key, 86400 * 90)  # 90 days
            return True
        except Exception:
            pass
    _memory_fallback[avatar_id] = payload
    return True


def _default_strategy(avatar_id: str) -> Dict[str, Any]:
    return {
        "avatar": avatar_id,
        "niche": "",
        "best_hooks": [],
        "best_topics": [],
        "best_platform": "youtube_shorts",
    }


def record_experiment_result(experiment_id: str, variant: str, score: float) -> Dict[str, Any] | None:
    """
    Record one variant's score for an experiment. If both A and B are recorded,
    returns { "winning_variant": "A"|"B", "score_a", "score_b" } and clears the key.
    Otherwise returns None.
    """
    r = _redis()
    if not r or not experiment_id:
        return None
    key = KEY_EXPERIMENT.format(experiment_id)
    raw = r.get(key)
    data = json.loads(raw) if raw else {}
    data[variant] = score
    r.set(key, json.dumps(data))
    r.expire(key, KEY_EXPERIMENT_TTL)
    if "A" in data and "B" in data:
        sa, sb = data.get("A", 0), data.get("B", 0)
        winner = "A" if sa >= sb else "B"
        r.delete(key)
        return {"winning_variant": winner, "score_a": sa, "score_b": sb, "score": max(sa, sb)}
    return None


def update_from_performance(
    avatar_id: str,
    niche: str,
    topic: str,
    hook: str,
    platform: str,
    performance_score: float,
    threshold: float = 0.5,
) -> None:
    """
    Update strategy memory when content performs above threshold.
    Appends topic/hook/platform to best_* lists if score >= threshold.
    """
    if performance_score < threshold:
        return
    strategy = get_strategy(avatar_id)
    strategy["niche"] = niche or strategy.get("niche", "")
    hooks = list(strategy.get("best_hooks") or [])
    if hook and hook not in hooks:
        hooks = [hook] + hooks[: _MAX_HOOKS - 1]
    strategy["best_hooks"] = hooks
    topics = list(strategy.get("best_topics") or [])
    if topic and topic not in topics:
        topics = [topic] + topics[: _MAX_TOPICS - 1]
    strategy["best_topics"] = topics
    if platform:
        strategy["best_platform"] = platform
    save_strategy(avatar_id, strategy)
