"""
Influencer Engine — Metrics aggregator.

Aggregate metrics by avatar, topic, platform, experiment.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List, Optional

METRIC_KEYS = [
    "views",
    "likes",
    "shares",
    "comments",
    "watch_time",
    "subscriber_growth",
    "engagement_rate",
]


class MetricsAggregator:
    """Aggregates ingested metrics by dimension."""

    def __init__(self):
        self._by_avatar: Dict[str, Dict[str, Any]] = defaultdict(lambda: _empty_agg())
        self._by_topic: Dict[str, Dict[str, Any]] = defaultdict(lambda: _empty_agg())
        self._by_platform: Dict[str, Dict[str, Any]] = defaultdict(lambda: _empty_agg())
        self._by_experiment: Dict[str, Dict[str, Any]] = defaultdict(lambda: _empty_agg())

    def add(self, record: Dict[str, Any]) -> None:
        metrics = record.get("metrics") or {}
        avatar = record.get("avatar_id") or ""
        topic = record.get("topic") or ""
        platform = record.get("platform") or ""
        exp = record.get("experiment_id") or ""
        if avatar:
            _merge_agg(self._by_avatar[avatar], metrics)
        if topic:
            _merge_agg(self._by_topic[topic], metrics)
        if platform:
            _merge_agg(self._by_platform[platform], metrics)
        if exp:
            _merge_agg(self._by_experiment[exp], metrics)

    def by_avatar(self) -> Dict[str, Dict[str, Any]]:
        return dict(self._by_avatar)

    def by_topic(self) -> Dict[str, Dict[str, Any]]:
        return dict(self._by_topic)

    def by_platform(self) -> Dict[str, Dict[str, Any]]:
        return dict(self._by_platform)

    def by_experiment(self) -> Dict[str, Dict[str, Any]]:
        return dict(self._by_experiment)

    def summary(self) -> Dict[str, Any]:
        return {
            "by_avatar": self.by_avatar(),
            "by_topic": self.by_topic(),
            "by_platform": self.by_platform(),
            "by_experiment": self.by_experiment(),
        }


def _empty_agg() -> Dict[str, Any]:
    return {
        "count": 0,
        "views": 0,
        "likes": 0,
        "shares": 0,
        "comments": 0,
        "watch_time": 0,
        "subscriber_growth": 0,
        "engagement_rate_sum": 0.0,
    }


def _merge_agg(agg: Dict[str, Any], m: Dict[str, Any]) -> None:
    agg["count"] += 1
    for k in ("views", "likes", "shares", "comments", "watch_time", "subscriber_growth"):
        agg[k] = agg.get(k, 0) + (m.get(k) or 0)
    agg["engagement_rate_sum"] = agg.get("engagement_rate_sum", 0) + (m.get("engagement_rate") or 0)


def aggregate_metrics(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate a list of collected records."""
    ag = MetricsAggregator()
    for r in records:
        ag.add(r)
    return ag.summary()
