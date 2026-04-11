"""
Influencer Engine — Publish controller.

Publish ONLY when execution_mode == "production" AND config["auto_publish"] == True.
Optional gates: min_quality_score, min_strategy_confidence, cost_limit_per_video.
Default: SAFE (no auto-publish unless explicitly enabled).
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

# Safety limits (Phase 10); can be overridden by config or live_ops.yaml
DEFAULT_SAFETY = {
    "auto_publish": False,
    "max_daily_spend": 10.0,
    "max_videos_per_day": 20,
}


def _load_live_ops_yaml() -> Dict[str, Any]:
    """Load config/live_ops.yaml if present."""
    try:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(base, "config", "live_ops.yaml")
        if os.path.isfile(path):
            import yaml
            with open(path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
    except Exception:
        pass
    return {}


def get_safety_limits(config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Return effective safety limits from config, live_ops.yaml, or defaults."""
    out = dict(DEFAULT_SAFETY)
    file_cfg = _load_live_ops_yaml()
    for k in ("auto_publish", "max_daily_spend", "max_videos_per_day"):
        if k in file_cfg and file_cfg[k] is not None:
            if k == "auto_publish":
                out[k] = bool(file_cfg[k])
            elif k == "max_daily_spend":
                try:
                    out[k] = float(file_cfg[k])
                except (TypeError, ValueError):
                    pass
            elif k == "max_videos_per_day":
                try:
                    out[k] = int(file_cfg[k])
                except (TypeError, ValueError):
                    pass
    if not config:
        return out
    if "auto_publish" in config:
        out["auto_publish"] = bool(config.get("auto_publish"))
    if "max_daily_spend" in config and config["max_daily_spend"] is not None:
        try:
            out["max_daily_spend"] = float(config["max_daily_spend"])
        except (TypeError, ValueError):
            pass
    if "max_videos_per_day" in config and config["max_videos_per_day"] is not None:
        try:
            out["max_videos_per_day"] = int(config["max_videos_per_day"])
        except (TypeError, ValueError):
            pass
    return out


def should_publish(
    config: Dict[str, Any],
    strategy: Optional[Dict[str, Any]] = None,
    metrics: Optional[Dict[str, Any]] = None,
) -> bool:
    """
    Return True only if all publish conditions are met.

    Required:
        - execution_mode == "production"
        - config["auto_publish"] == True

    Optional (when provided in config):
        - quality_score >= config["min_quality_score"] (default 0.0)
        - strategy_confidence >= config["min_strategy_confidence"] (default 0.0)
        - cost_per_video <= config["cost_limit_per_video"] (default no limit)
    """
    strategy = strategy or {}
    metrics = metrics or {}
    limits = get_safety_limits(config)

    if not limits.get("auto_publish"):
        return False
    execution_mode = (config.get("execution_mode") or "").strip().lower()
    if execution_mode != "production":
        return False

    min_quality = config.get("min_quality_score")
    if min_quality is not None:
        try:
            min_quality = float(min_quality)
            quality = float(metrics.get("quality_score", metrics.get("performance_score", 0)) or 0)
            if quality < min_quality:
                return False
        except (TypeError, ValueError):
            pass

    min_confidence = config.get("min_strategy_confidence")
    if min_confidence is not None:
        try:
            min_confidence = float(min_confidence)
            confidence = float(strategy.get("confidence", 0) or 0)
            if confidence < min_confidence:
                return False
        except (TypeError, ValueError):
            pass

    cost_limit = config.get("cost_limit_per_video")
    if cost_limit is not None:
        try:
            cost_limit = float(cost_limit)
            cost = float(metrics.get("cost_per_video", metrics.get("total_usd", 0)) or 0)
            if cost_limit > 0 and cost > cost_limit:
                return False
        except (TypeError, ValueError):
            pass

    return True
