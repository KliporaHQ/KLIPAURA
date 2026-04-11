"""Fetch real stock video/image clips (Pexels primary, Pixabay fallback) for cinematic scenes."""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .settings import PEXELS_API_KEY, PIXABAY_API_KEY

logger = logging.getLogger(__name__)

MIN_SHORT_SIDE_PX = 720


def _run(cmd: list[str], timeout: int = 120) -> tuple[bool, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode == 0, (r.stderr or r.stdout or "")
    except Exception as e:
        return False, str(e)


def _ffprobe_stream_size(path: str, ffprobe_path: str) -> tuple[int, int, float]:
    cmd = [
        ffprobe_path,
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height,duration",
        "-of",
        "json",
        path,
    ]
    ok, out = _run(cmd, 60)
    if not ok:
        return 0, 0, 0.0
    try:
        data = json.loads(out or "{}")
        streams = data.get("streams") or []
        if not streams:
            return 0, 0, 0.0
        s0 = streams[0]
        w = int(s0.get("width") or 0)
        h = int(s0.get("height") or 0)
        dur = float(s0.get("duration") or 0.0)
        if dur <= 0:
            fmt = (data.get("format") or {}) if isinstance(data, dict) else {}
            dur = float(fmt.get("duration") or 0.0)
        return w, h, dur
    except (ValueError, TypeError, KeyError):
        return 0, 0, 0.0


def _ffprobe_image_size(path: str, ffprobe_path: str) -> tuple[int, int]:
    w, h, _ = _ffprobe_stream_size(path, ffprobe_path)
    if w > 0 and h > 0:
        return w, h
    cmd = [
        ffprobe_path,
        "-v",
        "error",
        "-show_entries",
        "stream=width,height",
        "-of",
        "json",
        path,
    ]
    ok, out = _run(cmd, 60)
    if not ok:
        return 0, 0
    try:
        data = json.loads(out or "{}")
        for s in data.get("streams") or []:
            if isinstance(s, dict):
                w = int(s.get("width") or 0)
                h = int(s.get("height") or 0)
                if w > 0 and h > 0:
                    return w, h
    except (ValueError, TypeError):
        pass
    return 0, 0


def _acceptable_resolution(w: int, h: int) -> bool:
    if w <= 0 or h <= 0:
        return False
    return min(w, h) >= MIN_SHORT_SIDE_PX


def _build_search_query(scene: dict[str, Any]) -> str:
    parts: list[str] = []
    if isinstance(scene.get("keywords"), list):
        for k in scene["keywords"][:6]:
            if isinstance(k, str) and k.strip():
                parts.append(k.strip())
    vi = str(scene.get("visual_intent") or "").strip()
    if vi:
        parts.append(vi.replace("_", " "))
    em = str(scene.get("emotion") or "").strip()
    if em:
        parts.append(em)
    q = " ".join(parts) if parts else str(scene.get("text") or "vertical lifestyle")
    q = re.sub(r"\s+", " ", q).strip()[:120]
    return q or "vertical video"


def _download_url(url: str, dest: str, headers: dict[str, str] | None = None) -> bool:
    try:
        req = urllib.request.Request(url, headers=headers or {"User-Agent": "KLIP-AVATAR-CinematicV2/1.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = resp.read()
        if len(data) < 500:
            return False
        with open(dest, "wb") as f:
            f.write(data)
        return True
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        logger.debug("clip_fetcher download failed: %s", e)
        return False


def _pexels_headers() -> dict[str, str]:
    h = {"User-Agent": "KLIP-AVATAR-CinematicV2/1.0"}
    if PEXELS_API_KEY:
        h["Authorization"] = PEXELS_API_KEY
    return h


def _fetch_pexels_video_candidates(query: str) -> list[dict[str, Any]]:
    if not PEXELS_API_KEY:
        return []
    q = urllib.parse.quote(query)
    url = f"https://api.pexels.com/videos/search?query={q}&per_page=12&orientation=portrait&size=large"
    try:
        req = urllib.request.Request(url, headers=_pexels_headers())
        with urllib.request.urlopen(req, timeout=5) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        data = json.loads(raw)
    except Exception as e:
        logger.debug("pexels video search failed: %s", e)
        return []
    out: list[dict[str, Any]] = []
    for v in data.get("videos") or []:
        if not isinstance(v, dict):
            continue
        files = v.get("video_files") or []
        best: dict[str, Any] | None = None
        for f in files:
            if not isinstance(f, dict):
                continue
            link = f.get("link")
            w = int(f.get("width") or 0)
            h = int(f.get("height") or 0)
            if not link or not _acceptable_resolution(w, h):
                continue
            qual = min(w, h)
            if best is None or qual > int(best.get("_q") or 0):
                best = {"link": link, "_q": qual, "width": w, "height": h}
        if best and best.get("link"):
            out.append({"url": str(best["link"]), "source": "pexels_video"})
    return out


def _fetch_pexels_photo_candidates(query: str) -> list[dict[str, Any]]:
    if not PEXELS_API_KEY:
        return []
    q = urllib.parse.quote(query)
    url = f"https://api.pexels.com/v1/search?query={q}&per_page=8&orientation=portrait&size=large"
    try:
        req = urllib.request.Request(url, headers=_pexels_headers())
        with urllib.request.urlopen(req, timeout=5) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        data = json.loads(raw)
    except Exception as e:
        logger.debug("pexels photo search failed: %s", e)
        return []
    out: list[dict[str, Any]] = []
    for p in data.get("photos") or []:
        if not isinstance(p, dict):
            continue
        src = p.get("src") or {}
        url_big = (src.get("large2x") or src.get("large") or src.get("original") or "") if isinstance(src, dict) else ""
        w = int(p.get("width") or 0)
        h = int(p.get("height") or 0)
        if url_big and _acceptable_resolution(w, h):
            out.append({"url": str(url_big), "source": "pexels_photo", "width": w, "height": h})
    return out


def _fetch_pixabay_video_candidates(query: str) -> list[dict[str, Any]]:
    if not PIXABAY_API_KEY:
        return []
    q = urllib.parse.quote(query)
    url = (
        f"https://pixabay.com/api/videos/?key={urllib.parse.quote(PIXABAY_API_KEY)}"
        f"&q={q}&per_page=12&video_type=all"
    )
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        data = json.loads(raw)
    except Exception as e:
        logger.debug("pixabay video search failed: %s", e)
        return []
    out: list[dict[str, Any]] = []
    for hit in data.get("hits") or []:
        if not isinstance(hit, dict):
            continue
        vids = hit.get("videos") or {}
        if not isinstance(vids, dict):
            continue
        chosen = None
        for qual in ("large", "medium", "small", "tiny"):
            c = vids.get(qual)
            if isinstance(c, dict) and c.get("url"):
                chosen = c
                break
        if not isinstance(chosen, dict):
            continue
        link = chosen.get("url")
        w = int(chosen.get("width") or 0)
        h = int(chosen.get("height") or 0)
        if not link:
            continue
        if w and h and not _acceptable_resolution(w, h):
            continue
        out.append({"url": str(link), "source": "pixabay_video"})
    return out


def _fetch_pixabay_photo_candidates(query: str) -> list[dict[str, Any]]:
    if not PIXABAY_API_KEY:
        return []
    q = urllib.parse.quote(query)
    url = (
        f"https://pixabay.com/api/?key={urllib.parse.quote(PIXABAY_API_KEY)}"
        f"&q={q}&per_page=8&image_type=photo&orientation=vertical"
    )
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        data = json.loads(raw)
    except Exception as e:
        logger.debug("pixabay photo search failed: %s", e)
        return []
    out: list[dict[str, Any]] = []
    for hit in data.get("hits") or []:
        if not isinstance(hit, dict):
            continue
        link = hit.get("largeImageURL") or hit.get("webformatURL")
        w = int(hit.get("imageWidth") or 0)
        h = int(hit.get("imageHeight") or 0)
        if link and _acceptable_resolution(w, h):
            out.append({"url": str(link), "source": "pixabay_photo", "width": w, "height": h})
    return out


def _generate_fallback_clips(plan: Any) -> list[dict[str, Any]]:
    clips: list[dict[str, Any]] = []
    blocks = plan if isinstance(plan, list) else []
    for block in blocks:
        for c in block.get("clips", []):
            clips.append(
                {
                    "source": "lavfi",
                    "type": "color",
                    "duration": c.get("duration", 2.0),
                    "color": "black",
                }
            )
    return clips


def fetch_clips_for_scene(
    scene: dict[str, Any],
    *,
    ffprobe_path: str,
    target_duration_sec: float = 6.0,
    temp_dir: str | None = None,
) -> list[dict[str, Any]]:
    """
    Download one usable vertical-friendly asset (video preferred, else image) for a scene.

    Returns [{"path": str, "duration": float}, ...] — empty if APIs disabled, no keys, or failure.
    """
    base_dir = temp_dir or tempfile.mkdtemp(prefix="cinematic_clips_")
    os.makedirs(base_dir, exist_ok=True)

    query = _build_search_query(scene if isinstance(scene, dict) else {})
    candidates: list[dict[str, Any]] = []
    candidates.extend(_fetch_pexels_video_candidates(query))
    if not candidates:
        candidates.extend(_fetch_pixabay_video_candidates(query))
    if not candidates:
        candidates.extend(_fetch_pexels_photo_candidates(query))
    if not candidates:
        candidates.extend(_fetch_pixabay_photo_candidates(query))

    if not candidates:
        logger.info("clip_fetcher: no candidates for query=%r", query)
        return []

    want_d = max(2.0, min(60.0, float(target_duration_sec or 6.0)))

    for i, cand in enumerate(candidates[:8]):
        url = cand.get("url")
        if not url:
            continue
        ext = ".mp4"
        src = str(cand.get("source") or "")
        if "photo" in src:
            ext = ".jpg"
        dest = os.path.join(base_dir, f"stock_{i}{ext}")
        hdrs = _pexels_headers() if "pexels" in src else {"User-Agent": "KLIP-AVATAR-CinematicV2/1.0"}
        if not _download_url(str(url), dest, headers=hdrs):
            continue

        if dest.endswith(".mp4"):
            w, h, dur = _ffprobe_stream_size(dest, ffprobe_path)
            if not _acceptable_resolution(w, h):
                try:
                    os.unlink(dest)
                except OSError:
                    pass
                continue
            use_d = min(want_d, dur) if dur > 0.2 else want_d
            use_d = max(1.0, use_d)
            return [{"path": os.path.abspath(dest), "duration": round(use_d, 2)}]

        w, h = _ffprobe_image_size(dest, ffprobe_path)
        if not _acceptable_resolution(w, h):
            try:
                os.unlink(dest)
            except OSError:
                pass
            continue
        return [{"path": os.path.abspath(dest), "duration": round(want_d, 2)}]

    logger.warning("clip_fetcher: all downloads failed for query=%r", query)
    return []
