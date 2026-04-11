"""Score products: commission 60% + trend 40% + optional market sentiment boost."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

_SENTIMENT_KEY = "klip:market:sentiment"


def _sentiment_boost() -> float:
    try:
        from infrastructure.redis_client import get_redis_client_optional

        r = get_redis_client_optional()
        if r is None:
            return 0.0
        raw = r.get(_SENTIMENT_KEY)
        if not raw:
            return 0.0
        obj = json.loads(raw)
        s = float(obj.get("score", 0.5))
        return max(0.0, min(0.1, (s - 0.5) * 0.2))
    except Exception:
        return 0.0


def score_row(row: Mapping[str, Any]) -> float:
    try:
        comm = float(row.get("commission_rate") or 0.0)
    except (TypeError, ValueError):
        comm = 0.0
    try:
        trend = float(row.get("trend_score") or 0.5)
    except (TypeError, ValueError):
        trend = 0.5
    trend = max(0.0, min(1.0, trend))
    comm = max(0.0, min(100.0, comm))
    base = 0.6 * (comm / 100.0) + 0.4 * trend
    return base + _sentiment_boost()


def rank_products(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    scored = []
    for r in rows:
        s = score_row(r)
        scored.append({**r, "_score": s})
    scored.sort(key=lambda x: x.get("_score", 0.0), reverse=True)
    return scored
