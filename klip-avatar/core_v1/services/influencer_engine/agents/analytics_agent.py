"""
Influencer Engine — AnalyticsAgent.

Collects performance metrics after content distribution and computes a performance score.
Used by the pipeline's analyze_performance stage.
"""

from __future__ import annotations

from typing import Any, Dict

# Lazy import to avoid circular dependency and to work when loaded from pipeline or worker
_performance_model = None


def _get_performance_model():
    global _performance_model
    if _performance_model is None:
        try:
            from ..learning.performance_model import calculate_performance_score
            _performance_model = calculate_performance_score
        except Exception:
            try:
                from learning.performance_model import calculate_performance_score
                _performance_model = calculate_performance_score
            except Exception:
                def _noop(_):
                    return 0.0
                _performance_model = _noop
    return _performance_model


class AnalyticsAgent:
    """
    Collects performance metrics after distribution and returns metrics + normalized score.
    """

    def collect_performance(
        self,
        video_asset: Dict[str, Any],
        distribution_result: Dict[str, Any],
        avatar_profile: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Aggregate metrics from video_asset and distribution_result, then compute performance_score.

        Input:
            video_asset: { duration_seconds, ... }
            distribution_result: platform response (views, likes, shares, watch_time, etc.)
            avatar_profile: { avatar, niche, ... }

        Output:
            {
                "performance_metrics": { views, likes, shares, watch_time, engagement_rate, follower_growth },
                "performance_score": float in [0, 1]
            }
        """
        metrics = self._extract_metrics(video_asset, distribution_result)
        calc = _get_performance_model()
        score = calc(metrics) if callable(calc) else 0.0
        return {
            "performance_metrics": metrics,
            "performance_score": round(score, 4),
        }

    def _extract_metrics(
        self,
        video_asset: Dict[str, Any],
        distribution_result: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Build metrics dict from asset and distribution result."""
        dr = distribution_result or {}
        views = int(dr.get("views") or dr.get("view_count") or 0)
        likes = int(dr.get("likes") or dr.get("like_count") or 0)
        shares = int(dr.get("shares") or dr.get("share_count") or 0)
        watch_time = float(dr.get("watch_time") or dr.get("watch_time_seconds") or 0.0)
        duration = float(
            (video_asset or {}).get("duration_seconds")
            or (video_asset or {}).get("duration")
            or 1.0
        )
        engagement_rate = (likes + shares) / max(views, 1) if views else 0.0
        follower_growth = int(dr.get("follower_growth") or dr.get("followers_gained") or 0)

        return {
            "views": views,
            "likes": likes,
            "shares": shares,
            "watch_time": watch_time,
            "engagement_rate": min(1.0, engagement_rate),
            "follower_growth": follower_growth,
            "duration_seconds": duration,
        }
