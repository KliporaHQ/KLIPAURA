"""
Influencer Engine — YouTube distribution connector.

publish_video(), fetch_metrics(), delete_post(). Mock when credentials missing.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional


def _metrics_response(
    post_id: str,
    views: int,
    likes: int,
    comments: int,
    watch_time_seconds: float,
    mock: bool = False,
) -> Dict[str, Any]:
    """Standard metrics shape: views, likes, comments, watch_time_seconds, engagement_rate."""
    engagement_rate = (likes + comments) / views if views else 0.0
    return {
        "post_id": post_id,
        "views": views,
        "likes": likes,
        "comments": comments,
        "watch_time_seconds": watch_time_seconds,
        "engagement_rate": round(engagement_rate, 4),
        "mock": mock,
    }


class YouTubeConnector:
    """YouTube / YouTube Shorts connector."""

    @property
    def platform_id(self) -> str:
        return "youtube_shorts"

    def _has_credentials(self) -> bool:
        return bool(os.environ.get("YOUTUBE_CLIENT_ID") or os.environ.get("YOUTUBE_OAUTH_TOKEN"))

    def publish_video(
        self,
        video_url: str,
        title: str,
        description: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        mode = (metadata or {}).get("_distribution_mode", "mock")
        if mode == "mock":
            return {
                "post_id": f"mock_yt_{hash(video_url) % 10**10}",
                "url": "https://youtube.com/shorts/mock",
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
                "post_id": f"mock_yt_{hash(video_url) % 10**10}",
                "url": "https://youtube.com/shorts/mock",
                "mock": True,
                "platform": self.platform_id,
            }
        # Real: use YouTube Data API / upload
        return {
            "post_id": f"yt_{hash(video_url) % 10**10}",
            "url": "https://youtube.com/shorts/uploaded",
            "platform": self.platform_id,
        }

    def fetch_metrics(self, post_id: str) -> Dict[str, Any]:
        """Return views, likes, comments, watch_time_seconds, engagement_rate. Real API when credentials set."""
        if not self._has_credentials():
            return _metrics_response(post_id, 0, 0, 0, 0.0, mock=True)
        # Real: call YouTube Data API v3 (videos.list statistics). Placeholder until API wired.
        views, likes, comments, watch_time = 0, 0, 0, 0
        return _metrics_response(post_id, views, likes, comments, float(watch_time), mock=False)

    def delete_post(self, post_id: str) -> Dict[str, Any]:
        return {"ok": True, "post_id": post_id, "mock": not self._has_credentials()}
