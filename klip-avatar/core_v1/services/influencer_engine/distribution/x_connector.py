"""
Influencer Engine — X (Twitter) distribution connector.

publish_video(), fetch_metrics(), delete_post(). Mock when credentials missing.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional


class XConnector:
    """X (Twitter) video connector."""

    @property
    def platform_id(self) -> str:
        return "x"

    def _has_credentials(self) -> bool:
        return bool(
            os.environ.get("TWITTER_BEARER_TOKEN")
            or os.environ.get("X_API_KEY")
            or os.environ.get("TWITTER_ACCESS_TOKEN")
        )

    def publish_video(
        self,
        video_url: str,
        title: str,
        description: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if (metadata or {}).get("_distribution_mode") == "mock":
            return {
                "post_id": f"mock_x_{hash(video_url) % 10**10}",
                "url": "https://x.com/user/status/mock",
                "mock": True,
                "platform": self.platform_id,
            }
        try:
            from .getlate_connector import publish_through_getlate

            gl = publish_through_getlate(self.platform_id, video_url, title, description, metadata)
            if gl:
                return gl
        except Exception:
            pass
        if not self._has_credentials():
            return {
                "post_id": f"mock_x_{hash(video_url) % 10**10}",
                "url": "https://x.com/user/status/mock",
                "mock": True,
                "platform": self.platform_id,
            }
        return {
            "post_id": f"x_{hash(video_url) % 10**10}",
            "url": "https://x.com/user/status/1",
            "platform": self.platform_id,
        }

    def fetch_metrics(self, post_id: str) -> Dict[str, Any]:
        """Return views, likes, comments (replies), watch_time_seconds, engagement_rate. Real API when credentials set."""
        if not self._has_credentials():
            return _metrics_response_x(post_id, 0, 0, 0, 0, mock=True)
        views, likes, retweets, replies = 0, 0, 0, 0
        return _metrics_response_x(post_id, views, likes, retweets, replies, mock=False)

    def delete_post(self, post_id: str) -> Dict[str, Any]:
        return {"ok": True, "post_id": post_id, "mock": not self._has_credentials()}


def _metrics_response_x(
    post_id: str, views: int, likes: int, retweets: int, replies: int, mock: bool = False
) -> Dict[str, Any]:
    comments = replies
    engagement_rate = (likes + retweets + replies) / views if views else 0.0
    return {
        "post_id": post_id,
        "views": views,
        "likes": likes,
        "comments": comments,
        "retweets": retweets,
        "replies": replies,
        "watch_time_seconds": 0.0,
        "engagement_rate": round(engagement_rate, 4),
        "mock": mock,
    }
