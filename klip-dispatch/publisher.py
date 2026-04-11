"""
HITL approve → publish: Zernio/GetLate API when configured, else manual revenue log.

Credentials: `klip-avatar/core_v1/data/avatars/{id}/social_config.json` only (no API keys in .env for publish).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List

DEFAULT_ZERNIO_API = "https://zernio.com/api"


def _core_v1_root() -> Path:
    here = Path(__file__).resolve()
    return here.parent.parent / "klip-avatar" / "core_v1"


def social_config_path(avatar_id: str) -> Path:
    return _core_v1_root() / "data" / "avatars" / avatar_id / "social_config.json"


def load_social_config(avatar_id: str) -> Dict[str, Any]:
    p = social_config_path(avatar_id)
    if not p.is_file():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def connector_platform_to_getlate_targets(platform_id: str) -> List[str]:
    pid = (platform_id or "").lower()
    if "youtube" in pid:
        return ["youtube"]
    if "tiktok" in pid:
        return ["tiktok"]
    if "instagram" in pid:
        return ["instagram"]
    if pid in ("x", "twitter"):
        return ["twitter"]
    return ["tiktok"]


def _post_zernio_create_post(
    api_key: str,
    base_url: str,
    body: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    url = base_url.rstrip("/") + "/v1/posts"
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw = resp.read().decode("utf-8", "replace")
            code = resp.status
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw) if raw else {"error": str(e)}
        except json.JSONDecodeError:
            return e.code, {"error": raw[:2000]}
    except Exception as e:
        return 0, {"error": str(e)[:2000]}

    try:
        return code, json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return code, {"raw": raw[:2000]}


def publish_job(
    avatar_id: str,
    job_id: str,
    video_url: str,
    title: str,
    description: str,
    *,
    final_video_path: str | None = None,
    product_url: str = "",
) -> dict[str, Any]:
    """
    Returns a result dict with ok, publish_mode (getlate|manual), and optional post_url.

    `video_url` should be a public HTTPS URL (e.g. R2). If empty, GetLate is skipped.
    """
    from revenue_tracker import log

    cfg = load_social_config(avatar_id)
    api_key = (
        (cfg.get("getlate_api_key") or cfg.get("zernio_api_key") or cfg.get("GETLATE_API_KEY") or "")
        .strip()
    )
    base_url = (cfg.get("getlate_base_url") or cfg.get("zernio_api_base") or DEFAULT_ZERNIO_API).strip().rstrip("/")
    tiktok_account_id = (cfg.get("tiktok_account_id") or cfg.get("tiktokAccountId") or "").strip()

    public_video = (video_url or "").strip()

    # No public URL → manual path (still log revenue intent)
    if not public_video or not public_video.startswith("http"):
        log(
            job_id,
            avatar_id,
            product_url,
            publish_status="manual_required",
            post_url=None,
            detail={
                "reason": "no_public_video_url",
                "final_video_path": final_video_path,
                "hint": "Set R2 env vars so worker uploads a public URL, or add video URL to manifest before approve.",
            },
        )
        return {
            "ok": True,
            "publish_mode": "manual",
            "reason": "no_public_https_video_url",
            "final_video_path": final_video_path,
            "product_url": product_url,
        }

    if not api_key or not tiktok_account_id:
        log(
            job_id,
            avatar_id,
            product_url,
            publish_status="manual_required",
            post_url=None,
            detail={
                "reason": "missing_getlate_credentials",
                "missing": ["getlate_api_key", "tiktok_account_id"] if not api_key else ["tiktok_account_id"],
            },
        )
        return {
            "ok": True,
            "publish_mode": "manual",
            "reason": "getlate_not_configured_in_social_config",
            "public_video_url": public_video,
        }

    body: dict[str, Any] = {
        "title": (title or "UGC")[:200],
        "content": (description or "")[:4000],
        "mediaItems": [{"type": "video", "url": public_video}],
        "platforms": [{"platform": "tiktok", "accountId": tiktok_account_id}],
        "publishNow": True,
    }
    code, resp = _post_zernio_create_post(api_key, base_url, body)
    post_url = None
    if isinstance(resp, dict):
        post_obj = resp.get("post") or resp
        if isinstance(post_obj, dict):
            plats = post_obj.get("platforms") or []
            if isinstance(plats, list) and plats:
                p0 = plats[0]
                if isinstance(p0, dict):
                    post_url = p0.get("platformPostUrl") or p0.get("url")

    ok = 200 <= code < 300
    log(
        job_id,
        avatar_id,
        product_url,
        publish_status="published" if ok else f"publish_failed_{code}",
        post_url=post_url,
        detail={"http_status": code, "response": resp},
    )
    return {
        "ok": ok,
        "publish_mode": "getlate",
        "http_status": code,
        "response": resp,
        "post_url": post_url,
    }


__all__ = [
    "connector_platform_to_getlate_targets",
    "load_social_config",
    "publish_job",
    "social_config_path",
]
