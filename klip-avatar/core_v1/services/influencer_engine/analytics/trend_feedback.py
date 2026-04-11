"""
Influencer Engine — Trend feedback.

Feeds performance back into trend discovery (topic/hook/platform success).
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

REDIS_PREFIX = "ie:trend_feedback:"
KEY_TOPIC = REDIS_PREFIX + "topic:{}"
KEY_PLATFORM = REDIS_PREFIX + "platform:{}"


def _redis():
    try:
        from klipaura_core.infrastructure.redis_client import get_redis_client
        return get_redis_client()
    except Exception:
        return None


class TrendFeedback:
    """Records which topics/platforms/hooks performed well for strategy tuning."""

    def record(
        self,
        topic: str,
        platform: str,
        avatar_id: str = "",
        score: float = 0.0,
        metrics: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Record one feedback event."""
        r = _redis()
        payload = {
            "topic": topic,
            "platform": platform,
            "avatar_id": avatar_id,
            "score": score,
            "metrics": metrics or {},
        }
        if r:
            try:
                key_t = KEY_TOPIC.format(topic)
                r.lpush(key_t, json.dumps(payload))
                r.ltrim(key_t, 0, 99)
                r.expire(key_t, 86400 * 7)
                key_p = KEY_PLATFORM.format(platform)
                r.lpush(key_p, json.dumps(payload))
                r.ltrim(key_p, 0, 99)
                r.expire(key_p, 86400 * 7)
            except Exception:
                pass

    def get_topic_feedback(self, topic: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Recent feedback for topic."""
        r = _redis()
        if not r:
            return []
        try:
            raw = r.lrange(KEY_TOPIC.format(topic), 0, limit - 1)
            out = []
            for b in raw or []:
                s = b.decode() if isinstance(b, bytes) else b
                out.append(json.loads(s))
            return out
        except Exception:
            return []


def record_trend_feedback(
    topic: str,
    platform: str,
    avatar_id: str = "",
    score: float = 0.0,
    metrics: Optional[Dict[str, Any]] = None,
) -> None:
    """Module-level helper."""
    TrendFeedback().record(topic, platform, avatar_id, score, metrics)
