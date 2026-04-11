"""
Influencer Engine — Revenue per avatar (monetization layer).

Track revenue and conversion % per avatar; affiliate/CTA placeholder.
"""

from __future__ import annotations

from typing import Any, Dict

REDIS_PREFIX = "ie:revenue:avatar:"


def _redis():
    try:
        from klipaura_core.infrastructure.redis_client import get_redis_client
        return get_redis_client()
    except Exception:
        return None


def get_revenue_by_avatar(avatar_id: str) -> Dict[str, Any]:
    """Return revenue and conversion % for avatar. Placeholder: 0 until revenue events are recorded."""
    r = _redis()
    total = 0.0
    conversions = 0
    views = 0
    if r:
        try:
            import json
            raw = r.get(REDIS_PREFIX + avatar_id)
            if raw:
                data = json.loads(raw)
                total = float(data.get("total_revenue") or 0)
                conversions = int(data.get("conversions") or 0)
                views = int(data.get("views") or 0)
        except Exception:
            pass
    return {
        "avatar_id": avatar_id,
        "total_revenue": round(total, 2),
        "conversions": conversions,
        "views": views,
        "conversion_rate_pct": round((conversions / views) * 100, 2) if views else 0.0,
    }


def record_avatar_revenue(avatar_id: str, amount: float, conversions: int = 0, views: int = 0) -> None:
    """Record revenue event for avatar (call from revenue optimizer or affiliate layer)."""
    r = _redis()
    if not r:
        return
    try:
        import json
        key = REDIS_PREFIX + avatar_id
        raw = r.get(key)
        data = json.loads(raw) if raw else {"total_revenue": 0, "conversions": 0, "views": 0}
        data["total_revenue"] = float(data.get("total_revenue") or 0) + amount
        data["conversions"] = int(data.get("conversions") or 0) + conversions
        data["views"] = int(data.get("views") or 0) + views
        r.set(key, json.dumps(data))
        r.expire(key, 86400 * 365)
    except Exception:
        pass
