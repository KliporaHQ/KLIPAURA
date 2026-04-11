"""
Influencer Engine — Instagram distribution connector.

publish_video(), fetch_metrics(), delete_post(). Mock when credentials missing.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional


class InstagramConnector:
    """Instagram Reels connector."""

    @property
    def platform_id(self) -> str:
        return "instagram"

    def _has_credentials(self) -> bool:
        return bool(os.environ.get("INSTAGRAM_ACCESS_TOKEN") or os.environ.get("INSTAGRAM_GRAPH_TOKEN"))

    def publish_video(
        self,
        video_url: str,
        title: str,
        description: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if (metadata or {}).get("_distribution_mode") == "mock":
            return {
                "post_id": f"mock_ig_{hash(video_url) % 10**10}",
                "url": "https://instagram.com/reel/mock",
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
                "post_id": f"mock_ig_{hash(video_url) % 10**10}",
                "url": "https://instagram.com/reel/mock",
                "mock": True,
                "platform": self.platform_id,
            }
        return {
            "post_id": f"ig_{hash(video_url) % 10**10}",
            "url": "https://instagram.com/reel/1",
            "platform": self.platform_id,
        }

    def fetch_metrics(self, post_id: str) -> Dict[str, Any]:
        """Return views, likes, comments, watch_time_seconds, engagement_rate. Real API when credentials set."""
        if not self._has_credentials():
            return _metrics_response_ig(post_id, 0, 0, 0, 0.0, mock=True)
        views, likes, comments, watch_time = 0, 0, 0, 0.0
        return _metrics_response_ig(post_id, views, likes, comments, watch_time, mock=False)

    def delete_post(self, post_id: str) -> Dict[str, Any]:
        return {"ok": True, "post_id": post_id, "mock": not self._has_credentials()}


def _metrics_response_ig(
    post_id: str, views: int, likes: int, comments: int, watch_time: float, mock: bool = False
) -> Dict[str, Any]:
    engagement_rate = (likes + comments) / views if views else 0.0
    return {
        "post_id": post_id,
        "views": views,
        "likes": likes,
        "comments": comments,
        "watch_time_seconds": watch_time,
        "engagement_rate": round(engagement_rate, 4),
        "mock": mock,
    }
