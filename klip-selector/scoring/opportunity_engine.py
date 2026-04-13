"""Opportunity scoring engine for klip-selector.

Scoring formula (0–100):
    demand_score         * 0.30  (trend_score * 100)
    commission_rate_norm * 0.25  (commission_rate already 0–100)
    automation_score     * 0.25  (90=digital, 80=supported niche, 50=other)
    (100–competition)    * 0.10  (70=high_comp → 30pts, 40=low → 60pts)
    low_cost_score       * 0.10  (<AED50→80, <AED200→60, else→30)
    + market_sentiment_boost      (max +5 pts)
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

_MIN_SCORE = int(os.getenv("SELECTOR_MIN_SCORE", "65"))

_SENTIMENT_KEY = "klipaura:market:sentiment"

_HIGH_AUTOMATION_NICHES = {"beauty", "skincare", "wellness", "digital", "software", "health"}
_SUPPORTED_NICHES = {"home", "kitchen", "fitness", "electronics", "fashion", "lifestyle"}


def _sentiment_boost() -> float:
    """Return 0–5 bonus based on Redis market sentiment (non-fatal if Redis unavailable)."""
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
        # Map 0–1 sentiment → 0–5 bonus pts (neutral 0.5 → 0 bonus)
        return max(0.0, min(5.0, (s - 0.5) * 10.0))
    except Exception:
        return 0.0


def _automation_score(category: str) -> float:
    """90 for digital/high-automation, 80 for supported, 50 for anything else."""
    c = (category or "").strip().lower()
    if c in _HIGH_AUTOMATION_NICHES:
        return 90.0
    if c in _SUPPORTED_NICHES:
        return 80.0
    return 50.0


def _price_to_aed(price_str: str) -> float | None:
    """Extract numeric AED value from a price string like 'AED 1,299' or '49.99'."""
    if not price_str:
        return None
    import re
    nums = re.findall(r"[\d,\.]+", price_str.replace(",", ""))
    if not nums:
        return None
    try:
        return float(nums[0])
    except ValueError:
        return None


def _low_cost_score(price: str) -> float:
    """Impulse-buy likelihood: cheap products are easier conversions."""
    aed = _price_to_aed(price)
    if aed is None:
        return 50.0
    if aed < 50:
        return 80.0
    if aed < 200:
        return 60.0
    return 30.0


def score_product(
    *,
    commission_rate: float,
    trend_score: float,
    category: str,
    price: str = "",
    competition_score: float = 40.0,
) -> float:
    """Return 0–100 opportunity score.

    Args:
        commission_rate: 0–100 (percentage, e.g. 4.5 for 4.5%).
        trend_score: 0–1 normalised trend signal from data source.
        category: product niche/category string.
        price: human-readable price string (optional; used for impulse-buy scoring).
        competition_score: 0–100 (higher = more competitive).  Default 40 (low).
    """
    demand = min(100.0, max(0.0, trend_score * 100.0))
    comm = min(100.0, max(0.0, commission_rate))
    automation = _automation_score(category)
    competition_adj = max(0.0, min(100.0, 100.0 - competition_score))
    low_cost = _low_cost_score(price)

    raw = (
        demand * 0.30
        + comm * 0.25
        + automation * 0.25
        + competition_adj * 0.10
        + low_cost * 0.10
    )
    return min(100.0, raw + _sentiment_boost())


def score_adapter_product(product: Any) -> float:
    """Score an AdapterProduct instance."""
    return score_product(
        commission_rate=product.commission_rate,
        trend_score=product.trend_score,
        category=product.category,
        price=product.price,
    )


def is_above_threshold(score: float) -> bool:
    return score >= _MIN_SCORE
