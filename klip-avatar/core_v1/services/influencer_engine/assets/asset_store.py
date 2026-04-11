"""
Influencer Engine — Asset store.

Manages scripts, audio, video, thumbnails, metadata.
Uses Infrastructure storage when available; fallback to filesystem.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any, Dict, List, Optional

ASSET_TYPES = ("script", "audio", "video", "thumbnail", "metadata")
REDIS_PREFIX = "ie:asset:"
REDIS_INDEX = "ie:assets:index"
FS_FALLBACK_DIR = "assets_store"
# Subdirs for generated content (scripts → JSON, audio → .mp3, video → .mp4, thumbnail → .png)
FS_SCRIPT_DIR = "scripts"
FS_AUDIO_DIR = "audio"
FS_VIDEO_DIR = "video"
FS_THUMBNAIL_DIR = "thumbnails"


def _project_root() -> str:
    base = os.path.dirname(os.path.abspath(__file__))
    # assets/ -> influencer_engine -> services -> root
    return os.path.dirname(os.path.dirname(os.path.dirname(base)))


def _fs_store_dir() -> str:
    root = _project_root()
    return os.path.join(root, "services", "influencer_engine", FS_FALLBACK_DIR)


def _redis():
    try:
        from klipaura_core.infrastructure.redis_client import get_redis_client
        return get_redis_client()
    except Exception:
        return None


def _ensure_fs_store() -> str:
    d = _fs_store_dir()
    os.makedirs(d, exist_ok=True)
    return d


def _ensure_asset_subdir(subdir: str) -> str:
    base = _fs_store_dir()
    path = os.path.join(base, subdir)
    os.makedirs(path, exist_ok=True)
    return path


def _asset_key(asset_id: str) -> str:
    return REDIS_PREFIX + asset_id


def _default_asset(
    asset_type: str,
    url: str = "",
    owner_avatar: str = "",
    pipeline_source: str = "",
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return {
        "asset_id": str(uuid.uuid4()),
        "asset_type": asset_type,
        "url": url or "",
        "creation_time": time.time(),
        "owner_avatar": owner_avatar or "",
        "pipeline_source": pipeline_source or "",
        **(extra or {}),
    }


def save_asset(
    asset_type: str,
    url: str = "",
    owner_avatar: str = "",
    pipeline_source: str = "",
    metadata: Optional[Dict[str, Any]] = None,
    asset_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Save an asset. Returns asset record with asset_id, asset_type, url, creation_time,
    owner_avatar, pipeline_source.
    """
    asset = _default_asset(asset_type, url, owner_avatar, pipeline_source, metadata)
    if asset_id:
        asset["asset_id"] = asset_id
    aid = asset["asset_id"]

    r = _redis()
    if r:
        try:
            key = _asset_key(aid)
            r.set(key, json.dumps(asset))
            r.expire(key, 86400 * 30)
            r.sadd(REDIS_INDEX, aid)
            return asset
        except Exception:
            pass

    store = _ensure_fs_store()
    path = os.path.join(store, f"{aid}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(asset, f, indent=2)
    return asset


def get_asset(asset_id: str) -> Optional[Dict[str, Any]]:
    """Retrieve asset by asset_id."""
    r = _redis()
    if r:
        raw = r.get(_asset_key(asset_id))
        if raw:
            try:
                return json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                pass

    store = _fs_store_dir()
    path = os.path.join(store, f"{asset_id}.json")
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def list_assets(
    asset_type: Optional[str] = None,
    owner_avatar: Optional[str] = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """List assets with optional filters."""
    out: List[Dict[str, Any]] = []
    r = _redis()
    if r:
        try:
            ids = list(r.smembers(REDIS_INDEX))[: limit * 2]
            for aid in ids:
                a = get_asset(aid.decode() if isinstance(aid, bytes) else aid)
                if not a:
                    continue
                if asset_type and a.get("asset_type") != asset_type:
                    continue
                if owner_avatar and a.get("owner_avatar") != owner_avatar:
                    continue
                out.append(a)
                if len(out) >= limit:
                    break
            return out
        except Exception:
            pass

    store = _fs_store_dir()
    if not os.path.isdir(store):
        return []
    for fname in os.listdir(store):
        if not fname.endswith(".json"):
            continue
        a = get_asset(fname[:-5])
        if not a:
            continue
        if asset_type and a.get("asset_type") != asset_type:
            continue
        if owner_avatar and a.get("owner_avatar") != owner_avatar:
            continue
        out.append(a)
        if len(out) >= limit:
            break
    return out


def delete_asset(asset_id: str) -> bool:
    """Remove asset by id. Returns True if deleted."""
    r = _redis()
    if r:
        try:
            r.delete(_asset_key(asset_id))
            r.srem(REDIS_INDEX, asset_id)
            return True
        except Exception:
            pass
    store = _fs_store_dir()
    path = os.path.join(store, f"{asset_id}.json")
    if os.path.isfile(path):
        os.remove(path)
        return True
    return False


def save_script_json(
    content: Dict[str, Any],
    owner_avatar: str = "",
    pipeline_source: str = "influencer_engine",
    asset_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Save script content as JSON file; store in asset store. Returns asset record with url = file path."""
    _ensure_asset_subdir(FS_SCRIPT_DIR)
    aid = asset_id or str(uuid.uuid4())
    store = _fs_store_dir()
    path = os.path.join(store, FS_SCRIPT_DIR, f"{aid}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(content, f, indent=2)
    return save_asset("script", url=path, owner_avatar=owner_avatar, pipeline_source=pipeline_source, metadata=content, asset_id=aid)


def save_audio_file(
    local_path: str,
    owner_avatar: str = "",
    pipeline_source: str = "influencer_engine",
    asset_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Register audio file (.mp3) in asset store. Copies to store if local_path is elsewhere (optional). Returns asset record."""
    aid = asset_id or str(uuid.uuid4())
    store = _ensure_asset_subdir(FS_AUDIO_DIR)
    dest = os.path.join(store, f"{aid}.mp3")
    if os.path.abspath(local_path) != os.path.abspath(dest) and os.path.isfile(local_path):
        import shutil
        shutil.copy2(local_path, dest)
        local_path = dest
    else:
        local_path = dest if os.path.isfile(dest) else local_path
    return save_asset("audio", url=local_path, owner_avatar=owner_avatar, pipeline_source=pipeline_source, metadata=metadata, asset_id=aid)


def save_video_file(
    local_path: str,
    owner_avatar: str = "",
    pipeline_source: str = "influencer_engine",
    asset_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Register video file (.mp4) in asset store. Returns asset record."""
    aid = asset_id or str(uuid.uuid4())
    store = _ensure_asset_subdir(FS_VIDEO_DIR)
    dest = os.path.join(store, f"{aid}.mp4")
    if os.path.isfile(local_path) and os.path.abspath(local_path) != os.path.abspath(dest):
        import shutil
        shutil.copy2(local_path, dest)
        local_path = dest
    # Store absolute path so the engine can serve the file regardless of cwd
    url = os.path.abspath(local_path) if os.path.isfile(local_path) else local_path
    return save_asset("video", url=url, owner_avatar=owner_avatar, pipeline_source=pipeline_source, metadata=metadata, asset_id=aid)


def save_thumbnail_file(
    local_path: str,
    owner_avatar: str = "",
    pipeline_source: str = "influencer_engine",
    asset_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Register thumbnail file (.png) in asset store. Returns asset record."""
    aid = asset_id or str(uuid.uuid4())
    store = _ensure_asset_subdir(FS_THUMBNAIL_DIR)
    dest = os.path.join(store, f"{aid}.png")
    if os.path.isfile(local_path) and os.path.abspath(local_path) != os.path.abspath(dest):
        import shutil
        shutil.copy2(local_path, dest)
        local_path = dest
    return save_asset("thumbnail", url=local_path, owner_avatar=owner_avatar, pipeline_source=pipeline_source, metadata=metadata, asset_id=aid)
