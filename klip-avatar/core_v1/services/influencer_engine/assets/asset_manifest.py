"""
Influencer Engine — Asset manifest.

Builds and holds manifest of assets produced in a pipeline run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .asset_store import save_asset, get_asset


@dataclass
class AssetManifest:
    """Manifest of assets for one job/pipeline run."""

    job_id: str = ""
    avatar_id: str = ""
    pipeline_source: str = ""
    script_id: Optional[str] = None
    audio_id: Optional[str] = None
    video_id: Optional[str] = None
    thumbnail_id: Optional[str] = None
    metadata_id: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "job_id": self.job_id,
            "avatar_id": self.avatar_id,
            "pipeline_source": self.pipeline_source,
            "script_id": self.script_id,
            "audio_id": self.audio_id,
            "video_id": self.video_id,
            "thumbnail_id": self.thumbnail_id,
            "metadata_id": self.metadata_id,
            "extra": self.extra,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AssetManifest":
        return cls(
            job_id=data.get("job_id", ""),
            avatar_id=data.get("avatar_id", ""),
            pipeline_source=data.get("pipeline_source", ""),
            script_id=data.get("script_id"),
            audio_id=data.get("audio_id"),
            video_id=data.get("video_id"),
            thumbnail_id=data.get("thumbnail_id"),
            metadata_id=data.get("metadata_id"),
            extra=data.get("extra") or {},
        )

    def asset_ids(self) -> List[str]:
        out: List[str] = []
        for x in (self.script_id, self.audio_id, self.video_id, self.thumbnail_id, self.metadata_id):
            if x:
                out.append(x)
        return out

    def get_assets(self) -> Dict[str, Optional[Dict[str, Any]]]:
        """Load full asset records for all ids in manifest."""
        return {
            "script": get_asset(self.script_id) if self.script_id else None,
            "audio": get_asset(self.audio_id) if self.audio_id else None,
            "video": get_asset(self.video_id) if self.video_id else None,
            "thumbnail": get_asset(self.thumbnail_id) if self.thumbnail_id else None,
            "metadata": get_asset(self.metadata_id) if self.metadata_id else None,
        }


def build_manifest_from_context(context: Dict[str, Any], job_id: str = "") -> AssetManifest:
    """
    Build AssetManifest from pipeline context (payload.video_asset, script_asset, etc.).
    """
    payload = context.get("payload") or context
    config = payload.get("config") or {}
    avatar_id = config.get("avatar_profile") or ""
    video_asset = payload.get("video_asset") or context.get("video_asset") or {}
    script_asset = payload.get("script_asset") or context.get("script_asset") or {}
    audio_asset = payload.get("audio_asset") or context.get("audio_asset") or {}
    thumb_asset = payload.get("thumbnail_asset") or context.get("thumbnail_asset") or {}

    def _id(a: Any) -> Optional[str]:
        if isinstance(a, dict):
            return a.get("asset_id")
        return None

    return AssetManifest(
        job_id=job_id or payload.get("job_id") or "",
        avatar_id=avatar_id,
        pipeline_source=payload.get("pipeline_source") or "influencer_engine",
        script_id=_id(script_asset),
        audio_id=_id(audio_asset),
        video_id=_id(video_asset),
        thumbnail_id=_id(thumb_asset),
        metadata_id=_id(payload.get("metadata_asset")),
        extra={},
    )
