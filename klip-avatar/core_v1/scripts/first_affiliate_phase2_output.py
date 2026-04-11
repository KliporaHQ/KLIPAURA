#!/usr/bin/env python3
"""
Phase 2 — First real affiliate output (split layout, I2V, ElevenLabs, ASS, BGM).

Run from core_v1:
  python scripts/first_affiliate_phase2_output.py

Environment: core_v1/.env (explicit path via load_dotenv(dotenv_path=..., override=False); runtime env wins).

Requires:
  WAVESPEED_API_KEY, ELEVENLABS_API_KEY, ELEVENLABS_VOICE_ID (or ELEVENLABS_VOICE_ID_ANIKA)
  data/avatars/<AVATAR_ID>/ with at least one .png/.jpg for bottom panel
  ffmpeg / ffprobe on PATH or FFMPEG_PATH

Optional:
  AFFILIATE_CTA_OVERLAY — final drawtext line (default in video-render: Get yours • Link in bio)
  CINEMATIC_BG_MUSIC_PATH — local MP3/WAV for ducking under voice
  AFFILIATE_BGM_URL — HTTP(S) to download BGM when CINEMATIC_BG_MUSIC_PATH unset
  ACTIVE_AVATAR_ID — required; must not be "default" (e.g. theanikaglow)
  AFFILIATE_PRODUCT_USE_I2V — default 0; only exact value 1 enables WaveSpeed product I2V (experimental)
  AFFILIATE_TOP_KB_RATE / AFFILIATE_TOP_KB_CAP — product motion in compose + KB fallback (defaults 0.00075 / 1.04)

TTS uses model_id eleven_multilingual_v2 (minimal payload; see elevenlabs_tts_to_file).
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
import json
import urllib.error
import urllib.request
from typing import Any

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None  # type: ignore[misc, assignment]

ENV_PATH = os.path.abspath(os.path.join(_REPO, ".env"))
if load_dotenv:
    load_dotenv(dotenv_path=ENV_PATH, override=False, encoding="utf-8-sig")

import path_bootstrap  # noqa: F401

AFFILIATE_SPLIT_TOP_RATIO = float(os.getenv("AFFILIATE_SPLIT_TOP_RATIO", "0.30"))

AFFILIATE_URL = "https://temu.to/k/eokb3326fg9"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Voice: HOOK (0–~3s) → BODY → CTA — syncs with 4 I2V beats (hook / body / body / CTA).
# Script lines are built in main() from detect_merchant(affiliate_url) so narration matches the link.

MERCHANT_BANNED: dict[str, list[str]] = {
    "Amazon": ["temu", "aliexpress", "shein", "wish"],
    "Temu": ["amazon", "aliexpress", "shein", "wish"],
    "Unknown": ["amazon", "temu", "aliexpress", "shein", "wish"],
}

REJECT_PRODUCT_IMAGE_PATTERNS = [
    r"\d+\s*(mm|cm|inch)\b",
    r"specification|dimension|size[_-]?chart",
    r"sale|off|%|discount",
    r"banner|text[_-]?heavy",
]


def detect_merchant(url: str) -> str:
    u = (url or "").lower()
    if "temu" in u:
        return "Temu"
    if "amazon" in u or "amzn." in u or "a.co" in u:
        return "Amazon"
    return "Unknown"


def validate_merchant_script(script: str, merchant: str) -> None:
    banned = MERCHANT_BANNED.get(merchant, MERCHANT_BANNED["Unknown"])
    low = (script or "").lower()
    for word in banned:
        if word in low:
            raise RuntimeError(f"Merchant mismatch: script contains '{word}' (locked to {merchant})")


def build_script_lines(merchant: str) -> list[str]:
    if merchant == "Temu":
        body = "I didn't expect this quality from Temu… it actually works way better than I thought."
    elif merchant == "Amazon":
        body = "I didn't expect this quality from Amazon… it actually works way better than I thought."
    else:
        body = "I didn't expect this quality… it actually works way better than I thought."
    return [
        "This product is blowing up right now…",
        body,
        "I'll leave the link in the description… you should try it before it sells out.",
    ]


def caption_keywords_for_merchant(merchant: str) -> list[str]:
    if merchant == "Temu":
        return ["quality", "works", "temu"]
    if merchant == "Amazon":
        return ["quality", "works", "amazon"]
    return ["quality", "works"]


def filter_product_image_urls(urls: list[str], page_title: str = "") -> list[str]:
    """Drop noisy URLs; if none pass, fail hard (do not fall back to rejected images)."""
    if not urls:
        raise RuntimeError("No product image URLs to filter.")
    title_l = (page_title or "").lower()
    kept: list[str] = []
    for u in urls:
        sample = f"{u.lower()} {title_l}"
        bad = any(re.search(p, sample) for p in REJECT_PRODUCT_IMAGE_PATTERNS)
        if not bad:
            kept.append(u)
    if not kept:
        raise RuntimeError(
            "No valid product images after filtering (specs/banners/discount-style URLs rejected). "
            "Bad input — skip product; do not publish."
        )
    return kept


# Product / avatar prompts are locked in ``phase2_generation_guard`` (single template each; no expansion).


def _load_video_engine():
    path = os.path.join(_REPO, "services", "video-render", "engine.py")
    spec = importlib.util.spec_from_file_location("klip_video_render_engine", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load video-render engine")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _ffmpeg() -> str:
    from services.influencer_engine.rendering.ffmpeg_path import get_ffmpeg_exe

    return get_ffmpeg_exe()


def _ffprobe() -> str:
    from services.influencer_engine.rendering.ffmpeg_path import get_ffprobe_exe

    return get_ffprobe_exe()


def ffprobe_duration(path: str) -> float:
    exe = _ffprobe()
    r = subprocess.run(
        [exe, "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if r.returncode != 0:
        return 0.0
    try:
        return float((r.stdout or "").strip().split()[0])
    except (ValueError, IndexError):
        return 0.0


def follow_temu_affiliate(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=25) as r:
        return r.geturl()


def _goods_detail_html(final_url: str) -> str:
    """Temu short links return a JS shell; fetch full kuiper goods-detail HTML using goods_id."""
    req = urllib.request.Request(final_url, headers={"User-Agent": UA, "Accept": "text/html,*/*"})
    with urllib.request.urlopen(req, timeout=40) as r:
        html = r.read().decode("utf-8", "replace")
    # Enough product image URLs only in the full SSR/CSR payload (typically >100KB).
    if len(html) >= 80000 and len(re.findall(r"https://img\.kwcdn\.com", html)) >= 3:
        return html
    m = re.search(r"goods_id=(\d+)", final_url)
    if not m:
        return html
    gid = m.group(1)
    full = (
        f"https://www.temu.com/kuiper/uk1.html?subj=goods-detail&goods_id={gid}&_p_rfs=1"
        "&g_lg=en&g_region=209"
    )
    req2 = urllib.request.Request(full, headers={"User-Agent": UA, "Accept": "text/html,*/*"})
    with urllib.request.urlopen(req2, timeout=50) as r:
        return r.read().decode("utf-8", "replace")


def extract_product_page(final_url: str) -> tuple[str, list[str]]:
    """Return (title, image_urls) from Temu goods-detail HTML."""
    manual = (os.environ.get("UGC_PRODUCT_IMAGE_URLS") or "").strip()
    if manual:
        urls = [u.strip() for u in manual.split(",") if u.strip()]
        if not urls:
            raise RuntimeError("UGC_PRODUCT_IMAGE_URLS set but empty")
        title = ((os.environ.get("UGC_PRODUCT_TITLE") or "").strip() or "Temu product")[:500]
        return title, urls[:6]
    html = _goods_detail_html(final_url)
    title_m = re.search(r'<meta\s+property="og:title"\s+content="([^"]+)"', html)
    title = (title_m.group(1) if title_m else "Temu product").replace("&amp;", "&")
    raw = re.findall(r"https://img\.kwcdn\.com[^\"'\\\s>]+", html)
    cleaned: list[str] = []
    for u in raw:
        u = u.split("&#x27;")[0].rstrip("\\").strip()
        if not u or u in cleaned:
            continue
        cleaned.append(u)
        if len(cleaned) >= 6:
            break
    if not cleaned:
        raise RuntimeError("No product images found in Temu HTML (page format may have changed).")
    return title, cleaned[:6]


def concat_mp4s(clip_paths: list[str], out_path: str) -> None:
    fd, list_path = tempfile.mkstemp(suffix=".txt")
    os.close(fd)
    try:
        with open(list_path, "w", encoding="utf-8") as f:
            for p in clip_paths:
                f.write(f"file '{p.replace(chr(92), '/')}'\n")
        subprocess.run(
            [_ffmpeg(), "-y", "-f", "concat", "-safe", "0", "-i", list_path, "-c", "copy", out_path],
            check=True,
            capture_output=True,
            text=True,
            timeout=300,
        )
    finally:
        try:
            os.unlink(list_path)
        except OSError:
            pass


def _segment_speed(clip_idx: int, segment_idx: int) -> float:
    """Hook (0) and CTA (3) stay ~1.0x; middle clips get slight speed-up for pacing."""
    if clip_idx in (0, 3):
        return 1.0
    if clip_idx == 1:
        return 1.15 if segment_idx == 0 else 1.2
    if clip_idx == 2:
        return 1.12 if segment_idx == 0 else 1.2
    return 1.0


def _microcut_split_sec(clip_idx: int, dur: float) -> float:
    """Deterministic cut every 1.5–2.5s within each 5s clip (two beats per I2V clip)."""
    h = int(hashlib.md5(f"aff_mc_{clip_idx}".encode()).hexdigest()[:8], 16)
    a = 1.5 + (h % 1000) / 1000.0 * 1.0
    return min(max(a, 1.5), max(1.6, dur - 0.85))


def build_product_with_microcuts_and_speed(clip_paths: list[str], out_path: str, work_dir: str) -> None:
    """
    Hard-cut each I2V clip into two segments (no full 5s static), apply speed ramps, concat.
    Transitions are quick cuts only (no long crossfades).
    """
    ffmpeg = _ffmpeg()
    seg_paths: list[str] = []
    for clip_idx, clip in enumerate(clip_paths):
        dur = ffprobe_duration(clip)
        if dur <= 0:
            raise RuntimeError(f"invalid duration for clip {clip_idx}: {clip}")
        cut = _microcut_split_sec(clip_idx, dur)
        spans = [(0.0, cut), (cut, dur)]
        for seg_i, (t0, t1) in enumerate(spans):
            if (t1 - t0) < 0.12:
                continue
            sp = _segment_speed(clip_idx, seg_i)
            vf = f"trim=start={t0}:end={t1},setpts=PTS/{sp:.4f},fps=30"
            seg_path = os.path.join(work_dir, f"mc_seg_{clip_idx}_{seg_i}.mp4")
            r = subprocess.run(
                [
                    ffmpeg,
                    "-y",
                    "-i",
                    clip,
                    "-vf",
                    vf,
                    "-vsync",
                    "cfr",
                    "-an",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "veryfast",
                    "-pix_fmt",
                    "yuv420p",
                    seg_path,
                ],
                capture_output=True,
                text=True,
                timeout=300,
            )
            if r.returncode != 0:
                raise RuntimeError(
                    f"ffmpeg microcut failed clip={clip_idx} seg={seg_i}: {(r.stderr or r.stdout)[:800]}"
                )
            seg_paths.append(seg_path)
    concat_mp4s(seg_paths, out_path)


def stretch_video_duration(src: str, dst: str, target_sec: float) -> None:
    """Loop or trim + setpts so video length ~= target_sec."""
    d = ffprobe_duration(src)
    if d <= 0:
        raise RuntimeError("Product video has zero duration")
    if abs(d - target_sec) < 0.05:
        shutil.copy2(src, dst)
        return
    if d < target_sec:
        subprocess.run(
            [
                _ffmpeg(),
                "-y",
                "-stream_loop",
                "-1",
                "-i",
                src,
                "-t",
                f"{target_sec:.4f}",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-pix_fmt",
                "yuv420p",
                "-an",
                dst,
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=600,
        )
        return
    subprocess.run(
        [_ffmpeg(), "-y", "-i", src, "-t", f"{target_sec:.4f}", "-c:v", "libx264", "-preset", "veryfast", "-an", dst],
        check=True,
        capture_output=True,
        text=True,
        timeout=600,
    )


def resolve_avatar_images(avatar_id: str) -> list[str]:
    base = os.path.join(_REPO, "data", "avatars", avatar_id)
    if not os.path.isdir(base):
        raise FileNotFoundError(f"Avatar folder missing: {base}")
    out: list[str] = []
    for name in ("portrait.png", "face.png", "avatar.png", "profile.png"):
        p = os.path.join(base, name)
        if os.path.isfile(p):
            out.append(os.path.abspath(p))
    if not out:
        for fn in sorted(os.listdir(base)):
            if fn.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                out.append(os.path.abspath(os.path.join(base, fn)))
    if not out:
        raise FileNotFoundError(f"No image files in {base}")
    return out[:3]


def _normalize_kwcdn_image_url(url: str) -> str:
    """
    kwcdn links often include ?imageView2/.../format/avif — that returns AVIF or unstable transforms.
    Strip the query to fetch the stable source asset (usually JPEG on path ending in .jpg).
    """
    u = (url or "").strip()
    if not u.startswith("http") or "?" not in u:
        return u
    low = u.lower()
    if "kwcdn.com" not in low:
        return u
    if "imageview2" in low or "format=avif" in low or "format=webp" in low or "/format/" in low:
        return u.split("?", 1)[0]
    return u


def _image_fetch_headers(image_url: str) -> dict[str, str]:
    """kwcdn / Temu CDN often require Referer + full browser Accept (hotlink protection)."""
    ref = (os.environ.get("UGC_PRODUCT_REFERER") or "").strip()
    if not ref:
        pu = (os.environ.get("UGC_PRODUCT_URL") or "").strip()
        if pu.startswith("http") and "temu.com" in pu.lower():
            ref = pu
        else:
            ref = "https://www.temu.com/"
    return {
        "User-Agent": UA,
        "Referer": ref,
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }


def _download_image_to_work(url: str, work: str, tag: str) -> str | None:
    _dbg = (os.environ.get("UGC_IMAGE_DOWNLOAD_DEBUG") or "").strip().lower() in ("1", "true", "yes")
    try:
        _before = (url or "").strip()
        url = _normalize_kwcdn_image_url(url)
        if _dbg and url != _before:
            print("DEBUG download: normalized kwcdn URL (dropped transform query)", flush=True)
        dest = os.path.join(work, f"{tag}_dl.jpg")
        hdrs = _image_fetch_headers(url)
        req = urllib.request.Request(url, headers=hdrs)
        with urllib.request.urlopen(req, timeout=45) as r:
            data = r.read()
            code = getattr(r, "status", 200) or 200
        if _dbg:
            print(f"DEBUG download: status={code} bytes={len(data)} url={url[:140]}", flush=True)
        # Reject HTML error bodies and empty payloads; require image magic bytes.
        if len(data) < 256:
            if _dbg:
                print(f"DEBUG download: reject tiny payload len={len(data)}", flush=True)
            return None
        if not (
            data[:3] == b"\xff\xd8\xff"
            or data[:8] == b"\x89PNG\r\n\x1a\n"
            or (len(data) > 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP")
        ):
            if _dbg:
                print("DEBUG download: not a JPEG/PNG/WEBP (likely HTML block page)", flush=True)
            return None
        with open(dest, "wb") as f:
            f.write(data)
        return dest
    except Exception as e:
        if _dbg:
            print(f"DEBUG download: FAIL {type(e).__name__}: {e!s} url={url[:120]}", flush=True)
        return None


def _ensure_local_image(path_or_url: str, work: str, tag: str) -> str | None:
    if os.path.isfile(path_or_url):
        return path_or_url
    if (path_or_url or "").strip().startswith("http"):
        return _download_image_to_work(path_or_url.strip(), work, tag)
    return None


def _resolve_image_for_i2v(path_or_url: str, api_key: str) -> str | None:
    """WaveSpeed JSON expects a URL; upload local files via media API."""
    p = (path_or_url or "").strip()
    if p.startswith("http"):
        return p
    if os.path.isfile(p):
        from services.influencer_engine.rendering.wavespeed_video import upload_file

        u = upload_file(p, api_key)
        return u if u else None
    return None


def ensure_bgm_local(work: str) -> str | None:
    p = (os.environ.get("CINEMATIC_BG_MUSIC_PATH") or "").strip()
    if p and os.path.isfile(p):
        return p
    url = (os.environ.get("AFFILIATE_BGM_URL") or "").strip()
    if url.startswith("http"):
        dest = os.path.join(work, "bgm_download.mp3")
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=60) as r:
            data = r.read()
        if len(data) < 5000:
            return None
        with open(dest, "wb") as f:
            f.write(data)
        return dest
    return None


def elevenlabs_tts_to_file(text: str, voice_id: str, out_mp3: str) -> tuple[bool, str]:
    """POST ElevenLabs TTS; minimal JSON body (text + model_id only). Returns (ok, error_message)."""
    key = (os.environ.get("ELEVENLABS_API_KEY") or os.environ.get("XI_API_KEY") or "").strip()
    if not key:
        return False, "ELEVENLABS_API_KEY missing"
    model_id = "eleven_multilingual_v2"
    if len(text) > 2500:
        text = text[:2500]
    body = json.dumps({"text": text, "model_id": model_id}).encode("utf-8")
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    print(f"[ElevenLabs TTS] voice_id={voice_id}", flush=True)
    print(f"[ElevenLabs TTS] model_id={model_id}", flush=True)
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Accept": "audio/mpeg",
            "Content-Type": "application/json",
            "xi-api-key": key,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            status = getattr(resp, "status", None) or resp.getcode()
            data = resp.read()
        print(f"[ElevenLabs TTS] status_code={status}", flush=True)
        if not data or len(data) < 500:
            return False, "empty or tiny audio response"
        os.makedirs(os.path.dirname(out_mp3) or ".", exist_ok=True)
        with open(out_mp3, "wb") as f:
            f.write(data)
        return True, ""
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode("utf-8", "replace")
        except Exception:
            detail = ""
        print(f"[ElevenLabs TTS] status_code={e.code}", flush=True)
        print(f"[ElevenLabs TTS] response.text={detail}", flush=True)
        return False, f"HTTP {e.code}: {detail[:600]}"
    except Exception as e:
        return False, f"{type(e).__name__}: {str(e)[:400]}"


def burn_ass(video_in: str, ass_path: str, video_out: str) -> None:
    from engine.cinematic_v2.renderer_v2 import _ffmpeg_escape_path

    sub = _ffmpeg_escape_path(ass_path)
    vf = f"subtitles='{sub}',scale=1080:1920,setsar=1"
    subprocess.run(
        [_ffmpeg(), "-y", "-i", video_in, "-vf", vf, "-c:a", "copy", "-movflags", "+faststart", video_out],
        check=True,
        capture_output=True,
        text=True,
        timeout=900,
    )


def main() -> int:
    # Split ratio: ``AFFILIATE_SPLIT_TOP_RATIO`` env only (default 0.30).
    os.environ.setdefault("AFFILIATE_BOTTOM_KB_RATE", "0.0030")
    os.environ.setdefault("AFFILIATE_BOTTOM_KB_CAP", "1.10")
    os.environ.setdefault("AFFILIATE_TOP_KB_RATE", "0.00075")
    os.environ.setdefault("AFFILIATE_TOP_KB_CAP", "1.04")
    os.environ.setdefault("AFFILIATE_BOTTOM_FACE_ZOOM", "1.12")
    os.environ.setdefault("AFFILIATE_PRODUCT_USE_I2V", "0")

    ws_key = (os.environ.get("WAVESPEED_API_KEY") or "").strip()
    el_key = (os.environ.get("ELEVENLABS_API_KEY") or os.environ.get("XI_API_KEY") or "").strip()
    voice_id = (
        (os.environ.get("ELEVENLABS_VOICE_ID") or "").strip()
        or (os.environ.get("ELEVENLABS_VOICE_ID_ANIKA") or "").strip()
    )
    if not ws_key:
        print("ERROR: WAVESPEED_API_KEY required.", flush=True)
        return 2
    if not el_key:
        print("ERROR: ELEVENLABS_API_KEY required.", flush=True)
        return 2
    if not voice_id:
        print("ERROR: ELEVENLABS_VOICE_ID or ELEVENLABS_VOICE_ID_ANIKA required.", flush=True)
        return 2

    try:
        from klipaura_core.infrastructure.redis_client import RedisConfigError, pre_flight_redis_connectivity
    except ImportError:
        RedisConfigError = RuntimeError
        def pre_flight_redis_connectivity(): pass

    print("[0/7] Redis (Upstash) preflight…", flush=True)
    try:
        pre_flight_redis_connectivity()
    except RedisConfigError as e:
        print(f"ERROR: Redis preflight failed: {e}", flush=True)
        return 7

    avatar_raw = (os.environ.get("ACTIVE_AVATAR_ID") or "").strip()
    if not avatar_raw or avatar_raw.lower() == "default":
        print(
            "ERROR: ACTIVE_AVATAR_ID must be set to a real avatar folder in core_v1/.env "
            '(e.g. ACTIVE_AVATAR_ID=theanikaglow). Literal "default" is not allowed.',
            flush=True,
        )
        return 8
    avatar_id = avatar_raw
    _out_dir = os.path.join(_REPO, "outputs")
    out_final = os.path.join(_out_dir, "FINAL_AFFILIATE_VIDEO.mp4")
    os.makedirs(_out_dir, exist_ok=True)

    work = tempfile.mkdtemp(prefix="phase2_aff_")
    api_errors: list[str] = []

    try:
        print("[1/7] Resolving Temu product…", flush=True)
        final_u = follow_temu_affiliate(AFFILIATE_URL)
        title, img_urls = extract_product_page(final_u)
        merchant = detect_merchant(final_u)
        img_urls = filter_product_image_urls(img_urls, title)
        print("DEBUG_IMAGE_SOURCE:", os.getenv("UGC_PRODUCT_IMAGE_URLS"), flush=True)
        script_lines = build_script_lines(merchant)
        full_script = " ".join(script_lines)
        validate_merchant_script(full_script, merchant)
        print("  merchant:", merchant, flush=True)
        print("  title:", title[:120], flush=True)
        print("  images:", len(img_urls), flush=True)

        print("[2/7] ElevenLabs voice…", flush=True)
        voice_path = os.path.join(work, "voice.mp3")
        ok, err = elevenlabs_tts_to_file(full_script, voice_id, voice_path)
        if not ok:
            alt = (os.environ.get("ELEVENLABS_VOICE_ID_FALLBACK") or "").strip()
            if alt and alt != voice_id:
                print(f"  primary voice failed ({err[:120]}…), trying ELEVENLABS_VOICE_ID_FALLBACK", flush=True)
                ok, err = elevenlabs_tts_to_file(full_script, alt, voice_path)
        if not ok:
            print(f"ERROR: ElevenLabs TTS failed: {err}", flush=True)
            return 3
        vp = voice_path
        if not os.path.isfile(vp):
            print("ERROR: No voice file from ElevenLabs.", flush=True)
            return 3
        voice_d = ffprobe_duration(vp)
        if voice_d < 3:
            voice_d = 18.0
        print(f"  voice duration: {voice_d:.2f}s", flush=True)

        avatar_paths = resolve_avatar_images(avatar_id)

        print("[3/7] Product (static-first) + avatar I2V; micro-cuts + speed ramps on product…", flush=True)
        from core.services.wavespeed_key import resolve_wavespeed_api_key
        from services.influencer_engine.rendering.wavespeed_video import generate_i2v_clip
        from engine.cinematic_v2.phase2_generation_guard import (
            AVATAR_I2V_LOCKED,
            PRODUCT_I2V_LOCKED,
            avatar_quality_check_mp4,
            basic_visual_guardrail_mp4,
            clip_list_passes_final_gate,
            ffprobe_image_wh,
            filter_locals_meeting_min_width,
            final_visual_gate,
            is_valid_generation,
            ken_burns_fallback_mp4,
            lanczos_2x_upscale_to_work,
            product_identity_check_mp4,
            product_sanity_check_mp4,
            strict_product_validation,
        )

        k, _diag = resolve_wavespeed_api_key()
        if not (k or ws_key):
            print("ERROR: WaveSpeed key unresolved.", flush=True)
            return 4
        use_key = k or ws_key
        ffmpeg_exe = _ffmpeg()
        ffprobe_exe = _ffprobe()

        uniq_u = list(dict.fromkeys(img_urls))
        raw_pairs: list[tuple[str, str]] = []
        for si, u in enumerate(uniq_u):
            loc = _ensure_local_image(u, work, f"pd_raw_{si}")
            if not loc:
                print(f"ERROR: could not download product image {si}", flush=True)
                return 5
            raw_pairs.append((u, loc))
        ok_pairs, _rej = filter_locals_meeting_min_width(raw_pairs, ffprobe_exe, min_w=800)
        if not ok_pairs:
            print("FAIL FAST: LOW_RES_PRODUCT_IMAGES", flush=True)
            return 99
        allowed_u = {u for u, _ in ok_pairs}
        img_urls = [u for u in img_urls if u in allowed_u]
        if not img_urls:
            print("FAIL FAST: LOW_RES_PRODUCT_IMAGES", flush=True)
            return 99
        url_to_up: dict[str, str] = {}
        for u, loc in ok_pairs:
            w0, h0 = ffprobe_image_wh(loc, ffprobe_exe)
            print(f"DEBUG_PIPELINE_QUALITY: selected_product_image_resolution={w0}x{h0}", flush=True)
            up = lanczos_2x_upscale_to_work(loc, work, f"up_{abs(hash(u)) % 100000}", ffmpeg_exe)
            if not up:
                print("ERROR: PRODUCT_IMAGE_UPSCALE_FAILED", flush=True)
                return 5
            url_to_up[u] = up
        print("DEBUG_PIPELINE_QUALITY: upscale_applied=True (Lanczos 2× on product stills before render)", flush=True)
        use_product_i2v_requested = (os.environ.get("AFFILIATE_PRODUCT_USE_I2V") or "").strip() == "1"
        use_product_i2v = use_product_i2v_requested
        product_meta: dict[str, Any] | None = None
        if use_product_i2v and not strict_product_validation(product_meta):
            use_product_i2v = False
            api_errors.append("product I2V disabled: strict_product_validation(meta)")

        product_any_i2v_fallback = False
        product_clip_files: list[str] = []
        for i in range(4):
            dur = 5
            img_u = img_urls[i % len(img_urls)]
            outp = os.path.join(work, f"i2v_prod_{i:02d}.mp4")
            local_fb = url_to_up.get(img_u)
            if not local_fb:
                print(f"ERROR: could not resolve local image for product clip {i}", flush=True)
                return 5
            if not use_product_i2v:
                print(
                    f"  product {i + 1}/4 → Ken Burns premium (static-first; set AFFILIATE_PRODUCT_USE_I2V=1 for I2V)",
                    flush=True,
                )
                if not ken_burns_fallback_mp4(
                    local_fb, outp, float(dur), ffmpeg_exe, variant="product", skip_upscale=True
                ):
                    print(f"ERROR: product Ken Burns failed clip {i}", flush=True)
                    return 5
                product_clip_files.append(outp)
                print(f"  product clip {i + 1}/4 OK", flush=True)
                continue

            job_tag = f"phase2_prod_{i}"
            resolved = _resolve_image_for_i2v(local_fb, use_key)
            path: str | None = None
            if resolved:
                path = generate_i2v_clip(
                    resolved,
                    PRODUCT_I2V_LOCKED,
                    use_key,
                    outp,
                    duration_sec=dur,
                    job_id=job_tag,
                )
            use_clip = bool(path and os.path.isfile(path) and basic_visual_guardrail_mp4(path, ffprobe_exe, ffmpeg_exe))
            if use_clip and not product_sanity_check_mp4(path, ffmpeg_exe, ffprobe_exe):
                use_clip = False
                api_errors.append(f"product clip {i} product_sanity_check failed → KB")
            if use_clip and not is_valid_generation(None, mode="product"):
                use_clip = False
            strict_id = (os.environ.get("AFFILIATE_PRODUCT_IDENTITY_CHECK") or "").strip() == "1"
            if use_clip and strict_id and not product_identity_check_mp4(path, ffmpeg_exe, ffprobe_exe):
                use_clip = False
                api_errors.append(f"product clip {i} identity check failed → KB")
            if not use_clip:
                product_any_i2v_fallback = True
                print(f"  product {i + 1}/4 → Ken Burns premium (I2V invalid or failed)", flush=True)
                if not ken_burns_fallback_mp4(
                    local_fb, outp, float(dur), ffmpeg_exe, variant="product", skip_upscale=True
                ):
                    print(f"ERROR: product Ken Burns fallback failed clip {i}", flush=True)
                    return 5
            product_clip_files.append(outp)
            print(f"  product clip {i + 1}/4 OK", flush=True)

        if use_product_i2v_requested and product_any_i2v_fallback:
            print("  product consistency lock: mixed I2V/KB → full strip Ken Burns", flush=True)
            api_errors.append("product consistency lock: all segments KB")
            product_clip_files = []
            for i in range(4):
                dur = 5
                img_u = img_urls[i % len(img_urls)]
                outp = os.path.join(work, f"i2v_prod_{i:02d}.mp4")
                local_fb = url_to_up.get(img_u)
                if not local_fb:
                    print(f"ERROR: product consistency lock image {i}", flush=True)
                    return 5
                if not ken_burns_fallback_mp4(
                    local_fb, outp, float(dur), ffmpeg_exe, variant="product", skip_upscale=True
                ):
                    print(f"ERROR: product consistency Ken Burns failed {i}", flush=True)
                    return 5
                product_clip_files.append(outp)

        avatar_segment_pure_i2v: list[bool] = []
        avatar_clip_files: list[str] = []
        for j in range(3):
            dur = 5
            img_a = avatar_paths[j % len(avatar_paths)]
            outp = os.path.join(work, f"i2v_av_{j:02d}.mp4")
            job_tag = f"phase2_av_{j}"
            resolved = _resolve_image_for_i2v(img_a, use_key)
            path_av: str | None = None
            if resolved:
                path_av = generate_i2v_clip(
                    resolved,
                    AVATAR_I2V_LOCKED,
                    use_key,
                    outp,
                    duration_sec=dur,
                    job_id=job_tag,
                )
            use_av = bool(path_av and os.path.isfile(path_av) and basic_visual_guardrail_mp4(path_av, ffprobe_exe, ffmpeg_exe))
            if use_av and not avatar_quality_check_mp4(path_av, ffmpeg_exe, ffprobe_exe):
                use_av = False
                api_errors.append(f"avatar clip {j} quality check failed → KB")
            if use_av and not is_valid_generation(None, mode="avatar"):
                use_av = False
            if not use_av:
                br = (os.environ.get("AFFILIATE_BOTTOM_KB_RATE") or "0.0030").strip()
                bc = (os.environ.get("AFFILIATE_BOTTOM_KB_CAP") or "1.10").strip()
                print(f"  avatar {j + 1}/3 → Ken Burns fallback (I2V rejected or failed)", flush=True)
                api_errors.append(f"avatar clip {j} I2V fallback")
                if not ken_burns_fallback_mp4(
                    img_a,
                    outp,
                    float(dur),
                    ffmpeg_exe,
                    kb_rate=br,
                    kb_cap=bc,
                    variant="avatar",
                    motion_variant=j,
                ):
                    print(f"ERROR: avatar Ken Burns fallback failed clip {j}", flush=True)
                    return 5
            avatar_segment_pure_i2v.append(bool(use_av))
            avatar_clip_files.append(outp)
            print(f"  avatar clip {j + 1}/3 OK", flush=True)

        product_ok = clip_list_passes_final_gate(product_clip_files)
        avatar_ok = clip_list_passes_final_gate(avatar_clip_files)
        print(f"  final_visual_gate: {final_visual_gate(product_ok, avatar_ok)}", flush=True)
        if not product_ok:
            api_errors.append("final gate: force product KB strip")
            first_img = url_to_up.get(img_urls[0])
            if not first_img:
                print("ERROR: final gate product image", flush=True)
                return 5
            product_clip_files = []
            for i in range(4):
                dur = 5
                outp = os.path.join(work, f"i2v_prod_{i:02d}.mp4")
                if not ken_burns_fallback_mp4(
                    first_img, outp, float(dur), ffmpeg_exe, variant="product", skip_upscale=True
                ):
                    print(f"ERROR: final gate product KB {i}", flush=True)
                    return 5
                product_clip_files.append(outp)
        if not clip_list_passes_final_gate(avatar_clip_files):
            api_errors.append("final gate: force avatar KB strip")
            first_av = avatar_paths[0]
            avatar_clip_files = []
            for j in range(3):
                dur = 5
                outp = os.path.join(work, f"i2v_av_{j:02d}.mp4")
                br = (os.environ.get("AFFILIATE_BOTTOM_KB_RATE") or "0.0030").strip()
                bc = (os.environ.get("AFFILIATE_BOTTOM_KB_CAP") or "1.10").strip()
                if not ken_burns_fallback_mp4(
                    first_av,
                    outp,
                    float(dur),
                    ffmpeg_exe,
                    kb_rate=br,
                    kb_cap=bc,
                    variant="avatar",
                    motion_variant=j,
                ):
                    print(f"ERROR: final gate avatar KB {j}", flush=True)
                    return 5
                avatar_clip_files.append(outp)

        product_concat = os.path.join(work, "product_concat.mp4")
        build_product_with_microcuts_and_speed(product_clip_files, product_concat, work)
        product_fit = os.path.join(work, "product_fit.mp4")
        stretch_video_duration(product_concat, product_fit, voice_d)

        avatar_concat = os.path.join(work, "avatar_concat.mp4")
        concat_mp4s(avatar_clip_files, avatar_concat)
        avatar_fit = os.path.join(work, "avatar_fit.mp4")
        stretch_video_duration(avatar_concat, avatar_fit, voice_d)

        print("[4/7] Background music + ducking…", flush=True)
        bgm = ensure_bgm_local(work)
        if not bgm:
            print("  WARN: No BGM (set CINEMATIC_BG_MUSIC_PATH, AFFILIATE_BGM_URL, or PIXABAY_API_KEY). Voice only.", flush=True)
        from engine.cinematic_v2.audio_engine import mix_audio

        mixed_audio = mix_audio(vp, bgm or "", ffmpeg_path=_ffmpeg(), voice_duration_sec=voice_d)
        if not mixed_audio or not os.path.isfile(mixed_audio):
            print("ERROR: mix_audio failed.", flush=True)
            return 6

        print("[5/7] Affiliate split render (~product top / avatar bottom)…", flush=True)
        print("USING AVATAR:", os.getenv("ACTIVE_AVATAR_ID"), flush=True)
        mod = _load_video_engine()
        n_av = len(avatar_paths)
        dur_per = max(1.0, voice_d / max(1, n_av))

        split_out = os.path.join(work, "split_nocap.mp4")
        res = mod.render_affiliate_split_video(
            {
                "job_id": "phase2_first_affiliate",
                "product_video_path": product_fit,
                "avatar_video_path": avatar_fit,
                "image_urls": avatar_paths,
                "voice_path": mixed_audio,
                "duration_per_scene": dur_per,
            },
            output_path=split_out,
            ffmpeg_path=_ffmpeg(),
            max_retries=1,
        )
        if not res.get("success") or not os.path.isfile(split_out):
            print("ERROR:", res.get("error", "split render failed"), flush=True)
            return 7

        print("[6/7] ASS captions…", flush=True)
        from engine.cinematic_v2.caption_engine import generate_captions, write_ass_file

        # Caption punch: BLOWING UP, QUALITY, WORKS, LINK (highlighted larger in ASS).
        kw2 = caption_keywords_for_merchant(merchant)
        scenes: list[dict[str, Any]] = [
            {
                "type": "hook",
                "text": script_lines[0],
                "keywords": ["blowing up"],
            },
            {
                "type": "point",
                "text": script_lines[1],
                "keywords": kw2,
            },
            {
                "type": "cta",
                "text": script_lines[2],
                "keywords": ["link", "description"],
            },
        ]
        from engine.cinematic_v2.scene_splitter import enrich_scene

        scenes = [enrich_scene(s) for s in scenes]
        caps = generate_captions(scenes)
        ass_path = os.path.join(work, "captions.ass")
        write_ass_file(ass_path, caps, voice_d, width=1080, height=1920, caption_zone="bottom")

        print("[7/7] Burn captions + finalize…", flush=True)
        burn_ass(split_out, ass_path, out_final)

        sz = os.path.getsize(out_final)
        dur = ffprobe_duration(out_final)
        if sz < 1_000_000:
            print(f"WARN: output small ({sz} bytes). Target >1MB.", flush=True)
        if dur < 2:
            print(f"WARN: duration {dur}s — check playback.", flush=True)

        print("", flush=True)
        print("=== DONE ===", flush=True)
        print("Final video:", os.path.abspath(out_final), flush=True)
        print(f"Size: {sz / 1024 / 1024:.2f} MB  Duration: {dur:.2f}s", flush=True)
        print(
            f"Aspect: 1080×1920 (9:16)  AFFILIATE_SPLIT_TOP_RATIO={AFFILIATE_SPLIT_TOP_RATIO} (from env)",
            flush=True,
        )
        print("Voice: ElevenLabs (synced to full narration length)", flush=True)
        print("Captions: burned ASS (hook / body / CTA)", flush=True)
        if api_errors:
            print("API warnings:", api_errors, flush=True)
        return 0
    except urllib.error.HTTPError as e:
        print(f"HTTP ERROR: {e.code} {e.reason}", flush=True)
        return 10
    except Exception as e:
        print(f"ERROR: {type(e).__name__}: {e}", flush=True)
        import traceback

        traceback.print_exc()
        return 99
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    print("LOADED ENV FROM:", ENV_PATH, flush=True)
    print("VOICE ID:", os.getenv("ELEVENLABS_VOICE_ID"), flush=True)
    raise SystemExit(main())
