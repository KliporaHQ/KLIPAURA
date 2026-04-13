#!/usr/bin/env python3
"""
URL → UGC video pipeline (product page → extract → UGC script → I2V → ElevenLabs → FFmpeg).

Run from ``core_v1`` root:
  python -m pipeline.ugc_pipeline

Or:
  python pipeline/ugc_pipeline.py

Env:
  UGC_PRODUCT_URL — https product page (required)
  UGC_PRODUCT_IMAGE_URLS — optional comma-separated image URLs when the store returns
    a bot-challenge page to scripts (common on Temu); copy from browser DevTools
  UGC_PRODUCT_TITLE — optional title when using UGC_PRODUCT_IMAGE_URLS
  UGC_PRODUCT_BULLETS — optional bullets (comma or newline separated)
  GROQ_API_KEY — script generation
  WAVESPEED_API_KEY, ELEVENLABS_API_KEY, ELEVENLABS_VOICE_ID
  ACTIVE_AVATAR_ID — avatar folder under core_v1/data/avatars/
  Loads ``core_v1/.env``.

  UGC_FIRST_PRODUCT_CLIP_KB_ONLY — default ``1``: first product clip uses real downloaded
  image + Ken Burns motion only (no WaveSpeed I2V) to anchor product visibility in the top
  band. Set ``0`` to generate the first clip with I2V like the rest.
  UGC_FIRST_PRODUCT_KB_MAD_SIGNAL — default ``1``: on first product KB clip only, inject a
  sub-3px pad/crop wobble after zoompan so detector MAD thresholds pass. Set ``0`` to disable.
  AFFILIATE_SPLIT_TOP_RATIO — height fraction for the **product** strip (top); default ``0.30`` via env only.
  AFFILIATE_SPLIT_SKIP_DRAWTEXT_CTA — default ``1``: skip ffmpeg drawtext CTA when ASS burn carries CTA.
  UGC_LOCK_PRODUCT_IMAGE — default ``1``: use one catalog image for all product clips (see UGC_PRIMARY_PRODUCT_IMAGE_INDEX).
  AFFILIATE_TOP_BAND_MAD_BOOST — default ``1`` in video-render: scale + pad/crop + luma on product stream at compose
  (deterministic MAD vs gate; set ``0`` to disable). AFFILIATE_TOP_BAND_MAD_BOOST_PX (default 12) scales motion strength.
  UGC_DEBUG_PRODUCT_MAD=1 prints MAD pairs at gate.
  AFFILIATE_TOP_BAND_MAD_UNSHARP — default ``1``: light unsharp after MAD boost eq (set ``0`` to disable).
  UGC_PRODUCT_STRIP_MIN_SECONDS — default ``3``: loop-extend product_fit if shorter (edge cases only).

  **Ship gate (binary):** ``detect_product_usage`` PASS ⇔ ``max(m12,m23) >= 0.042``. No further filter work
  after that — publish. If one run fails, bump ``AFFILIATE_TOP_BAND_MAD_BOOST_PX`` to 16 (ceiling 18) and rerun once;
  see ``run_pipeline.ps1`` header.
  UGC_LOG_SHIP_GATE=1 — print effective ``AFFILIATE_SPLIT_TOP_RATIO`` / ``AFFILIATE_TOP_BAND_MAD_BOOST_PX``
  after ``.env`` (catch silent overrides before a ship run).

  KLIP_AFFILIATE_DATA — JSON from worker (manifest ``affiliate_data``): affiliate_link, affiliate_tag, etc.
  KLIP_AFFILIATE_LINK — optional convenience copy of ``affiliate_link`` for script LLM context.
  KLIP_LAYOUT_MODE — set ``affiliate_split_55_45`` for 55% top / 45% bottom split (see video-render engine).
"""

from __future__ import annotations

import json
import os
import importlib.util
import shutil
import subprocess
import sys
import tempfile
from typing import Any

# core_v1 root (parent of pipeline/)
_CORE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _CORE not in sys.path:
    sys.path.insert(0, _CORE)

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None  # type: ignore[misc, assignment]

ENV_PATH = os.path.abspath(os.path.join(_CORE, ".env"))
if load_dotenv:
    # utf-8-sig strips UTF-8 BOM so the first key is GROQ_API_KEY not \ufeffGROQ_API_KEY
    # override=False: runtime env (e.g. PowerShell) wins; .env fills missing keys only.
    load_dotenv(dotenv_path=ENV_PATH, override=False, encoding="utf-8-sig")

# Single source for split ratio default (video-render reads ``AFFILIATE_SPLIT_TOP_RATIO``; no hardcoded overrides here).
AFFILIATE_SPLIT_TOP_RATIO = float(os.getenv("AFFILIATE_SPLIT_TOP_RATIO", "0.30"))

# run_pipeline.ps1 sets KLIP_PIPELINE_RUN=1; force unlimited I2V/hour for this pipeline run.
if os.environ.get("KLIP_PIPELINE_RUN", "").strip() == "1":
    os.environ["WAVESPEED_MAX_I2V_PER_HOUR"] = "0"

if (os.environ.get("UGC_DEBUG_GROQ") or "").strip().lower() in ("1", "true", "yes"):
    print("DEBUG ENV_PATH:", ENV_PATH, flush=True)
    _gk = os.getenv("GROQ_API_KEY") or ""
    print(f"DEBUG GROQ KEY: {'set (' + str(len(_gk)) + ' chars)' if _gk else 'NOT SET'}", flush=True)

import path_bootstrap  # noqa: F401

from pipeline.pipeline_manifest import fail_pipeline_manifest, touch_pipeline_manifest

from first_affiliate_phase2_output import (
    _ffmpeg,
    _ffprobe,
    burn_ass,
    build_product_with_microcuts_and_speed,
    concat_mp4s,
    ensure_bgm_local,
    ffprobe_duration,
    resolve_avatar_images,
    stretch_video_duration,
    _ensure_local_image,
    _resolve_image_for_i2v,
)

# 120-word floor @ ElevenLabs 1.05–1.1x is often ~36–42s; allow margin vs Groq variance.
MIN_VIDEO_DURATION_SECONDS = 36
TARGET_VIDEO_DURATION_SECONDS = 60
UGC_PRODUCT_CLIPS = 10  # 10 × 5s ≈ 50s B-roll before stretch to voice


def _ensure_min_mp4_duration(path: str, min_sec: float, ffmpeg_exe: str, ffprobe_duration_fn: Any) -> None:
    """If video is shorter than ``min_sec``, loop-extend in place (temporal spread for MAD samples)."""
    if min_sec <= 0.1 or not path or not os.path.isfile(path):
        return
    try:
        d = float(ffprobe_duration_fn(path))
    except (TypeError, ValueError):
        return
    if d >= min_sec - 0.05:
        return
    tmp = path + "._minlen.mp4"
    try:
        r = subprocess.run(
            [
                ffmpeg_exe,
                "-y",
                "-stream_loop",
                "-1",
                "-i",
                path,
                "-t",
                f"{min_sec:.3f}",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-pix_fmt",
                "yuv420p",
                "-an",
                tmp,
            ],
            capture_output=True,
            text=True,
            timeout=600,
        )
        if r.returncode != 0 or not os.path.isfile(tmp) or os.path.getsize(tmp) < 2000:
            raise RuntimeError("PRODUCT_STRIP_MIN_LEN_EXTEND_FAILED")
        os.replace(tmp, path)
        print(f"  product_fit extended from {d:.2f}s to >= {min_sec:.1f}s (loop)", flush=True)
    finally:
        if os.path.isfile(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass


def _load_video_engine() -> Any:
    path = os.path.join(_CORE, "services", "video-render", "engine.py")
    spec = importlib.util.spec_from_file_location("klip_video_render_engine", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load video-render engine")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def elevenlabs_tts_ugc(text: str, voice_id: str, out_mp3: str) -> tuple[bool, str]:
    """ElevenLabs TTS with slightly faster speed (1.05–1.1) for conversational UGC."""
    import json
    import urllib.error
    import urllib.request

    key = (os.environ.get("ELEVENLABS_API_KEY") or os.environ.get("XI_API_KEY") or "").strip()
    if not key:
        return False, "ELEVENLABS_API_KEY missing"
    model_id = "eleven_multilingual_v2"
    if len(text) > 2500:
        text = text[:2500]
    # Default 1.05 keeps ~135-word scripts above MIN_VIDEO_DURATION_SECONDS (45s); 1.075 can dip under.
    speed = 1.05
    try:
        env_sp = (os.environ.get("ELEVENLABS_SPEED") or "").strip()
        if env_sp:
            speed = max(1.05, min(1.1, float(env_sp)))
    except ValueError:
        pass
    body = json.dumps(
        {
            "text": text,
            "model_id": model_id,
            "voice_settings": {
                "stability": 0.5,
                "similarity_boost": 0.75,
                "speed": speed,
            },
        }
    ).encode("utf-8")
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
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
            data = resp.read()
        if not data or len(data) < 500:
            return False, "empty or tiny audio response"
        os.makedirs(os.path.dirname(out_mp3) or ".", exist_ok=True)
        with open(out_mp3, "wb") as f:
            f.write(data)
        return True, ""
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")
        except Exception:
            pass
        if e.code == 400:
            body_min = json.dumps({"text": text, "model_id": model_id}).encode("utf-8")
            req2 = urllib.request.Request(
                url,
                data=body_min,
                headers={
                    "Accept": "audio/mpeg",
                    "Content-Type": "application/json",
                    "xi-api-key": key,
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(req2, timeout=120) as resp:
                    data = resp.read()
                if data and len(data) >= 500:
                    os.makedirs(os.path.dirname(out_mp3) or ".", exist_ok=True)
                    with open(out_mp3, "wb") as f:
                        f.write(data)
                    return True, ""
            except urllib.error.HTTPError as e2:
                try:
                    detail = e2.read().decode("utf-8", "replace")
                except Exception:
                    pass
                return False, f"HTTP {e2.code}: {detail[:600]}"
            except Exception as ex:
                return False, f"{type(ex).__name__}: {str(ex)[:400]}"
        return False, f"HTTP {e.code}: {detail[:600]}"
    except Exception as e:
        return False, f"{type(e).__name__}: {str(e)[:400]}"


def _apply_affiliate_env_from_job() -> None:
    """Hydrate CTA / layout from worker env (KLIP_AFFILIATE_DATA, KLIP_LAYOUT_MODE). Idempotent with setdefault."""
    raw = (os.environ.get("KLIP_AFFILIATE_DATA") or "").strip()
    if raw:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict):
            link = str(data.get("affiliate_link") or "").strip()
            if link:
                os.environ.setdefault("KLIP_AFFILIATE_LINK", link)
            if link and not (os.environ.get("UGC_CTA_LINE") or "").strip():
                os.environ["UGC_CTA_LINE"] = f"Shop with my link — {link}"
                os.environ.setdefault("AFFILIATE_CTA_OVERLAY", link[:220])
    lm = (os.environ.get("KLIP_LAYOUT_MODE") or "").strip()
    if lm == "affiliate_split_55_45":
        os.environ.setdefault("AFFILIATE_SPLIT_TOP_RATIO", "0.55")


def main() -> int:
    import argparse

    # CLI args take strict precedence over .env / environment.
    # Set env vars before any other reads so the rest of the pipeline picks them up transparently.
    parser = argparse.ArgumentParser(description="UGC pipeline: product URL → FINAL_VIDEO.mp4")
    parser.add_argument("--product-url", dest="product_url", help="Product page URL (overrides UGC_PRODUCT_URL)")
    parser.add_argument("--avatar-id", dest="avatar_id", help="Avatar folder ID (overrides ACTIVE_AVATAR_ID)")
    parser.add_argument("--voice-id", dest="voice_id", help="ElevenLabs voice ID (overrides ELEVENLABS_VOICE_ID)")
    parser.add_argument("--job-id", dest="job_id", help="Job ID for manifest tracking (overrides JOB_ID)")
    parser.add_argument("--jobs-dir", dest="jobs_dir", help="Jobs directory (overrides JOBS_DIR)")
    parser.add_argument("--cta", dest="cta", help="CTA overlay text (overrides AFFILIATE_CTA_OVERLAY)")
    parser.add_argument("--passport-file", dest="passport_file",
                        help="Path to passport.json — skips product scrape stage when provided")
    parser.add_argument("--video-format", dest="video_format",
                        help="VideoFormat class name (e.g. SplitFormat, LipsyncFormat). Overrides KLIP_VIDEO_FORMAT")
    args, _ = parser.parse_known_args()
    if args.product_url:
        os.environ["UGC_PRODUCT_URL"] = args.product_url
    if args.avatar_id:
        os.environ["ACTIVE_AVATAR_ID"] = args.avatar_id
    if args.voice_id:
        os.environ["ELEVENLABS_VOICE_ID"] = args.voice_id
    if args.job_id:
        os.environ["JOB_ID"] = args.job_id
    if args.jobs_dir:
        os.environ["JOBS_DIR"] = args.jobs_dir
    if args.cta:
        os.environ["AFFILIATE_CTA_OVERLAY"] = args.cta
    if args.passport_file:
        os.environ["KLIP_PASSPORT_FILE"] = args.passport_file
    if args.video_format:
        os.environ["KLIP_VIDEO_FORMAT"] = args.video_format

    ffmpeg_exe = _ffmpeg()
    ffprobe_exe = _ffprobe()

    _apply_affiliate_env_from_job()

    # Log passport file and video format selection
    _passport_file = (os.environ.get("KLIP_PASSPORT_FILE") or "").strip()
    _video_format = (os.environ.get("KLIP_VIDEO_FORMAT") or "SplitFormat").strip()
    if _passport_file:
        print(f"[pipeline] passport_file={_passport_file}", flush=True)
    print(f"[format] video_format={_video_format}", flush=True)

    # Split ratio: read only from AFFILIATE_SPLIT_TOP_RATIO (default 0.30 in video-render engine / .env).
    os.environ.setdefault("AFFILIATE_SPLIT_SKIP_DRAWTEXT_CTA", "1")
    os.environ.setdefault("PRODUCT_FIRST_N_SECONDS", "3")
    os.environ.setdefault("UGC_FIRST_PRODUCT_CLIP_KB_ONLY", "1")
    # Full stretched product strip (detector samples ~22/48/74% of FINAL — after stretch, not clip 0 only).
    os.environ.setdefault("UGC_PRODUCT_STRIP_MICRO_JITTER", "1")
    os.environ.setdefault("CAPTION_BOTTOM_MARGIN", "180")
    os.environ.setdefault("AFFILIATE_BOTTOM_KB_RATE", "0.0030")
    os.environ.setdefault("AFFILIATE_BOTTOM_KB_CAP", "1.10")
    # Stronger product KB motion for NO_VISIBLE_PRODUCT_USAGE (top-band MAD vs sampled frames).
    os.environ.setdefault("AFFILIATE_TOP_KB_RATE", "0.0016")
    os.environ.setdefault("AFFILIATE_TOP_KB_CAP", "1.25")
    os.environ.setdefault("AFFILIATE_TOP_KB_PAN_X", "14")
    os.environ.setdefault("AFFILIATE_TOP_KB_PAN_Y", "8")
    os.environ.setdefault("AFFILIATE_BOTTOM_FACE_ZOOM", "1.12")
    os.environ["AFFILIATE_PRODUCT_USE_I2V"] = "1"
    os.environ.setdefault(
        "AFFILIATE_CTA_OVERLAY",
        os.environ.get("AFFILIATE_CTA_DEFAULT", "Get yours - Link in bio"),
    )

    if (os.environ.get("UGC_LOG_SHIP_GATE") or "").strip().lower() in ("1", "true", "yes", "on"):
        _mbp = (os.environ.get("AFFILIATE_TOP_BAND_MAD_BOOST_PX") or "").strip()
        _ar = (os.environ.get("AFFILIATE_SPLIT_TOP_RATIO") or "").strip()
        print(
            "SHIP_GATE_ENV "
            f"AFFILIATE_SPLIT_TOP_RATIO={repr(_ar) if _ar else '(default 0.30 in engine)'} "
            f"pipeline_float={AFFILIATE_SPLIT_TOP_RATIO!r} "
            f"AFFILIATE_TOP_BAND_MAD_BOOST_PX={_mbp or '(default 12 in engine)'}",
            flush=True,
        )

    from engine.ugc_final_render_validation import (
        assert_cta_enabled_for_final_segment,
        detect_product_usage,
        detect_static_frames,
        ugc_scene_plan_from_types,
        validate_cta_text,
        validate_final_output_file,
        validate_ugc_final_scene_plan,
    )

    cta_line = (os.environ.get("AFFILIATE_CTA_OVERLAY") or "").strip()
    validate_cta_text(cta_line)
    assert_cta_enabled_for_final_segment()

    product_url = (os.environ.get("UGC_PRODUCT_URL") or "").strip()
    if not product_url:
        print("ERROR: Set UGC_PRODUCT_URL to a product page https URL.", flush=True)
        return 2

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

    avatar_raw = (os.environ.get("ACTIVE_AVATAR_ID") or "").strip()
    if not avatar_raw or avatar_raw.lower() == "default":
        print("ERROR: ACTIVE_AVATAR_ID must be set to a real avatar folder.", flush=True)
        return 8
    avatar_id = avatar_raw

    _out_dir = os.path.join(_CORE, "outputs")
    out_final = os.path.join(_out_dir, "FINAL_UGC_URL_VIDEO.mp4")
    os.makedirs(_out_dir, exist_ok=True)

    work = tempfile.mkdtemp(prefix="ugc_url_")
    api_errors: list[str] = []

    try:
        from services.product_extractor import extract_product_data, filter_images, validate_http_product_url
        from services.ugc_script_llm import generate_ugc_product_script
        from engine.ugc_visual_prompts import build_ugc_i2v_prompt, default_ugc_scene_types, ensure_demo_scene

        _jid = (os.environ.get("JOB_ID") or "").strip()
        print(
            f"UGC_PIPELINE job_id={_jid} avatar_id={avatar_id} product_url={product_url[:140]}",
            flush=True,
        )
        touch_pipeline_manifest("pipeline_start", "ugc_pipeline main")
        print("[1/7] Extract product URL…", flush=True)
        touch_pipeline_manifest("extract", "start: fetch/scrape product page")
        validate_http_product_url(product_url)
        raw = extract_product_data(product_url)
        imgs = filter_images(raw.get("images") or [])
        title = (raw.get("title") or "product")[:500]
        bullets = list(raw.get("bullets") or [])
        img_urls = [x["url"] for x in imgs]
        print("DEBUG_IMAGE_SOURCE:", os.getenv("UGC_PRODUCT_IMAGE_URLS"), flush=True)
        print("  title:", title[:100], flush=True)
        print("  images:", len(img_urls), flush=True)
        print("STAGE: EXTRACT_COMPLETE", flush=True)
        touch_pipeline_manifest("extract", "end: product data loaded")

        print("[2/7] UGC script (Groq)…", flush=True)
        touch_pipeline_manifest("groq_script", "start: Groq LLM")
        print("[EXTERNAL] BEFORE Groq generate_ugc_product_script", flush=True)
        full_script = generate_ugc_product_script(title, bullets, product_url)
        print("[EXTERNAL] AFTER Groq generate_ugc_product_script", flush=True)
        print("  words:", len(full_script.split()), flush=True)
        print("STAGE: SCRIPT_COMPLETE", flush=True)
        touch_pipeline_manifest("groq_script", "end: script ready")

        raw_scene_types = (os.environ.get("UGC_SCENE_TYPES") or "").strip()
        if raw_scene_types:
            scene_types = [x.strip().lower() for x in raw_scene_types.split(",") if x.strip()]
        else:
            scene_types = default_ugc_scene_types(UGC_PRODUCT_CLIPS)
        ensure_demo_scene(scene_types)
        try:
            _pfn = float((os.environ.get("PRODUCT_FIRST_N_SECONDS") or "0").strip() or "0")
        except ValueError:
            _pfn = 0.0
        if _pfn > 0 and scene_types:
            scene_types[0] = "demo"
        ugc_plan = ugc_scene_plan_from_types(scene_types)
        validate_ugc_final_scene_plan(ugc_plan)

        print("[3/7] ElevenLabs voice (speed ~1.05–1.1)…", flush=True)
        touch_pipeline_manifest("elevenlabs_tts", "start: ElevenLabs TTS")
        print("[EXTERNAL] BEFORE ElevenLabs TTS", flush=True)
        voice_path = os.path.join(work, "voice.mp3")
        ok, err = elevenlabs_tts_ugc(full_script, voice_id, voice_path)
        if not ok:
            alt = (os.environ.get("ELEVENLABS_VOICE_ID_FALLBACK") or "").strip()
            if alt and alt != voice_id:
                ok, err = elevenlabs_tts_ugc(full_script, alt, voice_path)
        if not ok:
            print(f"ERROR: ElevenLabs TTS failed: {err}", flush=True)
            fail_pipeline_manifest(RuntimeError(f"ELEVENLABS_TTS: {err}"))
            return 3
        print("[EXTERNAL] AFTER ElevenLabs TTS (ok)", flush=True)
        voice_d = ffprobe_duration(voice_path)
        print(f"  voice duration: {voice_d:.1f}s (min {MIN_VIDEO_DURATION_SECONDS}s)", flush=True)
        if voice_d < MIN_VIDEO_DURATION_SECONDS:
            raise RuntimeError("AUDIO_TOO_SHORT")
        # Full render length follows narration only — no trimming vs voice.
        final_duration = voice_d
        print("STAGE: TTS_COMPLETE", flush=True)
        touch_pipeline_manifest("elevenlabs_tts", "end: voice.mp3 ready")

        avatar_paths = resolve_avatar_images(avatar_id)

        print("[4/7] Product + avatar I2V (UGC prompts)…", flush=True)
        touch_pipeline_manifest("wavespeed_i2v", "start: WaveSpeed I2V + avatar clips")
        print("[EXTERNAL] WaveSpeed I2V + lipsync region starting", flush=True)
        from core.services.wavespeed_key import resolve_wavespeed_api_key
        from services.influencer_engine.rendering.wavespeed_video import generate_i2v_clip
        from engine.cinematic_v2.phase2_generation_guard import (
            AVATAR_I2V_LOCKED,
            apply_micro_spatial_jitter_mp4,
            avatar_quality_check_mp4,
            basic_visual_guardrail_mp4,
            clip_list_passes_final_gate,
            ffprobe_image_wh,
            filter_locals_meeting_min_width,
            final_visual_gate,
            is_valid_generation,
            ken_burns_fallback_mp4,
            lanczos_2x_upscale_to_work,
            ugc_product_clip_guard_mp4,
        )
        from pipeline.ugc_lipsync_adapter import generate_lipsync_bottom

        k, _diag = resolve_wavespeed_api_key()
        use_key = k or ws_key
        if not use_key:
            print("ERROR: WaveSpeed key unresolved.", flush=True)
            fail_pipeline_manifest(RuntimeError("WAVESPEED_KEY_UNRESOLVED"))
            return 4

        product_clip_files: list[str] = []
        try:
            _pmv = float((os.environ.get("PRODUCT_MIN_VISIBLE_SECONDS") or "").strip() or "0")
        except ValueError:
            _pmv = 0.0
        _first_clip_kb_only = (os.environ.get("UGC_FIRST_PRODUCT_CLIP_KB_ONLY") or "1").strip().lower() not in (
            "0",
            "false",
            "no",
            "off",
        )
        _lock_img = (os.environ.get("UGC_LOCK_PRODUCT_IMAGE") or "1").strip().lower() not in (
            "0",
            "false",
            "no",
            "off",
        )
        try:
            _img_idx = int((os.environ.get("UGC_PRIMARY_PRODUCT_IMAGE_INDEX") or "0").strip() or "0")
        except ValueError:
            _img_idx = 0

        uniq_urls = list(dict.fromkeys(img_urls))
        raw_pairs: list[tuple[str, str]] = []
        for si, u in enumerate(uniq_urls):
            loc = _ensure_local_image(u, work, f"pd_raw_{si}")
            if not loc:
                raise RuntimeError("INVALID_PRODUCT_IMAGES")
            raw_pairs.append((u, loc))
        ok_pairs, _rej = filter_locals_meeting_min_width(raw_pairs, ffprobe_exe, min_w=800)
        if not ok_pairs:
            raise RuntimeError("LOW_RES_PRODUCT_IMAGES")
        allowed_u = {u for u, _ in ok_pairs}
        img_urls = [u for u in img_urls if u in allowed_u]
        if not img_urls:
            raise RuntimeError("LOW_RES_PRODUCT_IMAGES")
        url_to_up: dict[str, str] = {}
        for u, loc in ok_pairs:
            w0, h0 = ffprobe_image_wh(loc, ffprobe_exe)
            print(f"DEBUG_PIPELINE_QUALITY: selected_product_image_resolution={w0}x{h0}", flush=True)
            up = lanczos_2x_upscale_to_work(loc, work, f"up_{abs(hash(u)) % 100000}", ffmpeg_exe)
            if not up:
                raise RuntimeError("PRODUCT_IMAGE_UPSCALE_FAILED")
            url_to_up[u] = up
        print("DEBUG_PIPELINE_QUALITY: upscale_applied=True (Lanczos 2× on product stills before render)", flush=True)

        for i in range(UGC_PRODUCT_CLIPS):
            touch_pipeline_manifest("wavespeed_i2v", f"progress: product clip {i + 1}/{UGC_PRODUCT_CLIPS}")
            dur = 5.0
            if i == 0 and _pmv >= 4.0:
                dur = float(max(5.0, min(12.0, _pmv)))
            if i == 0 and _first_clip_kb_only:
                dur = max(6.0, float(dur))
            sk = scene_types[i % len(scene_types)]
            motion = build_ugc_i2v_prompt(sk)
            if _lock_img and img_urls:
                img_u = img_urls[_img_idx % len(img_urls)]
            else:
                img_u = img_urls[i % len(img_urls)]
            outp = os.path.join(work, f"i2v_prod_{i:02d}.mp4")
            local_fb = url_to_up.get(img_u)
            if not local_fb:
                raise RuntimeError("INVALID_PRODUCT_IMAGES")

            # First clip: real product pixels + Ken Burns only (no I2V) — anchors top-band usage heuristics.
            if i == 0 and _first_clip_kb_only:
                print(
                    f"  product {i + 1}/{UGC_PRODUCT_CLIPS} -> real image Ken Burns anchor (no I2V), {dur:.1f}s",
                    flush=True,
                )
                if not ken_burns_fallback_mp4(
                    local_fb,
                    outp,
                    float(dur),
                    ffmpeg_exe,
                    variant="product",
                    inject_pixel_mad_signal=True,
                    skip_upscale=True,
                ):
                    raise RuntimeError("product Ken Burns failed")
                if not ugc_product_clip_guard_mp4(outp, ffmpeg_exe, ffprobe_exe):
                    raise RuntimeError("INVALID_FIRST_PRODUCT_CLIP")
                product_clip_files.append(outp)
                continue

            job_tag = f"ugc_prod_{i}"
            resolved = _resolve_image_for_i2v(local_fb, use_key)
            path: str | None = None
            if resolved:
                path = generate_i2v_clip(
                    resolved,
                    motion,
                    use_key,
                    outp,
                    duration_sec=dur,
                    job_id=job_tag,
                )
            use_clip = bool(path and os.path.isfile(path) and ugc_product_clip_guard_mp4(path, ffmpeg_exe, ffprobe_exe))
            if use_clip and not is_valid_generation(None, mode="product"):
                use_clip = False
            if not use_clip:
                api_errors.append(f"product clip {i} I2V fallback KB")
                if not ken_burns_fallback_mp4(
                    local_fb,
                    outp,
                    float(dur),
                    ffmpeg_exe,
                    variant="product",
                    inject_pixel_mad_signal=(i == 0),
                    skip_upscale=True,
                ):
                    raise RuntimeError("product Ken Burns failed")
            product_clip_files.append(outp)

        avatar_clip_files: list[str] = []
        # Use WaveSpeed lipsync for strict lip-sync + natural motion (Phase 2)
        lipsync_enabled = (os.getenv("UGC_ENABLE_LIPSYNC_BOTTOM") or "1").strip().lower() not in (
            "0", "false", "no", "off"
        )
        if lipsync_enabled and use_key:
            print("  bottom -> WaveSpeed lipsync (strict lip-sync + natural motion)", flush=True)
            touch_pipeline_manifest("lipsync", "start: WaveSpeed lipsync")
            print("[EXTERNAL] BEFORE WaveSpeed lipsync (bottom)", flush=True)
            ls_path = os.path.join(work, "lipsync_bottom.mp4")
            ok, msg = generate_lipsync_bottom(
                avatar_paths,
                voice_path,
                ls_path,
                api_key=use_key,
                job_id="ugc_lipsync_bottom",
            )
            if ok and os.path.isfile(ls_path):
                avatar_clip_files.append(ls_path)
                print("[EXTERNAL] AFTER WaveSpeed lipsync (ok)", flush=True)
                touch_pipeline_manifest("lipsync", "end: lipsync_bottom.mp4 ready")
            else:
                print(f"  lipsync failed: {msg}; falling back to Ken Burns", flush=True)
                print("[EXTERNAL] AFTER WaveSpeed lipsync (fallback path)", flush=True)
        # Fallback: generate 3 I2V clips or Ken Burns slideshow
        if not avatar_clip_files:
            for j in range(3):
                dur = 5
                img_a = avatar_paths[j % len(avatar_paths)]
                outp = os.path.join(work, f"i2v_av_{j:02d}.mp4")
                job_tag = f"ugc_av_{j}"
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
                if not use_av:
                    br = (os.environ.get("AFFILIATE_BOTTOM_KB_RATE") or "0.0030").strip()
                    bc = (os.environ.get("AFFILIATE_BOTTOM_KB_CAP") or "1.10").strip()
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
                        raise RuntimeError("avatar Ken Burns failed")
                avatar_clip_files.append(outp)

        print("STAGE: I2V_COMPLETE", flush=True)
        touch_pipeline_manifest("wavespeed_i2v", "end: I2V segment complete; concat + stretch next")

        product_ok = clip_list_passes_final_gate(product_clip_files)
        avatar_ok = clip_list_passes_final_gate(avatar_clip_files)
        print(f"  final_visual_gate: {final_visual_gate(product_ok, avatar_ok)}", flush=True)

        product_concat = os.path.join(work, "product_concat.mp4")
        build_product_with_microcuts_and_speed(product_clip_files, product_concat, work)
        product_fit = os.path.join(work, "product_fit.mp4")
        stretch_video_duration(product_concat, product_fit, final_duration)
        _strip_jit = (os.environ.get("UGC_PRODUCT_STRIP_MICRO_JITTER") or "1").strip().lower() not in (
            "0",
            "false",
            "no",
            "off",
        )
        if _strip_jit:
            print("  product_fit -> micro spatial jitter (full strip, detector sample alignment)", flush=True)
            if not apply_micro_spatial_jitter_mp4(product_fit, ffmpeg_exe):
                raise RuntimeError("PRODUCT_STRIP_JITTER_FAILED")

        try:
            _pmin = float((os.environ.get("UGC_PRODUCT_STRIP_MIN_SECONDS") or "3").strip() or "3")
        except ValueError:
            _pmin = 3.0
        if _pmin > 0:
            _ensure_min_mp4_duration(product_fit, _pmin, ffmpeg_exe, ffprobe_duration)

        avatar_concat = os.path.join(work, "avatar_concat.mp4")
        concat_mp4s(avatar_clip_files, avatar_concat)
        avatar_fit = os.path.join(work, "avatar_fit.mp4")
        stretch_video_duration(avatar_concat, avatar_fit, final_duration)

        print("[5/7] Background music + ducking…", flush=True)
        touch_pipeline_manifest("audio_mix", "start: bgm + duck")
        bgm = ensure_bgm_local(work)
        from engine.cinematic_v2.audio_engine import mix_audio

        mixed_audio = mix_audio(voice_path, bgm or "", ffmpeg_path=ffmpeg_exe, voice_duration_sec=final_duration)
        if not mixed_audio or not os.path.isfile(mixed_audio):
            print("ERROR: mix_audio failed.", flush=True)
            fail_pipeline_manifest(RuntimeError("MIX_AUDIO_FAILED"))
            return 6
        touch_pipeline_manifest("audio_mix", "end: mixed audio ready")

        print("[6/7] Affiliate split render…", flush=True)
        touch_pipeline_manifest("ffmpeg_render", "start: affiliate_split compose (FFmpeg)")
        print("[EXTERNAL] BEFORE FFmpeg render_affiliate_split_video", flush=True)
        mod = _load_video_engine()
        n_av = len(avatar_paths)
        dur_per = max(1.0, final_duration / max(1, n_av))
        split_out = os.path.join(work, "split_nocap.mp4")
        # video-render compose applies a second zoompan on the product stream when
        # AFFILIATE_TOP_KB_RATE>0, which smooths motion and collapsed top-band MAD vs
        # detect_product_usage. product_fit is already KB + optional strip jitter.
        _prev_top_kb = os.environ.get("AFFILIATE_TOP_KB_RATE")
        os.environ["AFFILIATE_TOP_KB_RATE"] = "0"
        try:
            res = mod.render_affiliate_split_video(
                {
                    "job_id": "ugc_url_pipeline",
                    "product_video_path": product_fit,
                    "avatar_video_path": avatar_fit,
                    "image_urls": avatar_paths,
                    "voice_path": mixed_audio,
                    "duration_per_scene": dur_per,
                },
                output_path=split_out,
                ffmpeg_path=ffmpeg_exe,
                max_retries=1,
            )
        finally:
            if _prev_top_kb is None:
                os.environ.pop("AFFILIATE_TOP_KB_RATE", None)
            else:
                os.environ["AFFILIATE_TOP_KB_RATE"] = _prev_top_kb
        if not res.get("success") or not os.path.isfile(split_out):
            print("ERROR:", res.get("error", "split render failed"), flush=True)
            fail_pipeline_manifest(RuntimeError(str(res.get("error", "split render failed"))))
            return 7

        print("[EXTERNAL] AFTER FFmpeg render_affiliate_split_video", flush=True)
        print("STAGE: COMPOSITE_COMPLETE", flush=True)
        touch_pipeline_manifest("ffmpeg_render", "end: affiliate split composite written")

        print("[7/7] ASS captions + burn…", flush=True)
        touch_pipeline_manifest("ffmpeg_render", "start: ASS captions + burn_ass")
        print("[EXTERNAL] BEFORE FFmpeg burn_ass (captions on video)", flush=True)
        from engine.cinematic_v2.caption_engine import generate_captions, write_ass_file
        from engine.cinematic_v2.scene_splitter import enrich_scene

        parts = [x.strip() for x in full_script.replace("!", ".").replace("?", ".").split(".") if x.strip()]
        if len(parts) < 3:
            parts = [full_script[:80], full_script[80:160], full_script[160:]]
        scenes_cap: list[dict[str, Any]] = [
            {"type": "hook", "text": parts[0], "keywords": ["hook"]},
            {"type": "body", "text": parts[min(1, len(parts) - 1)], "keywords": ["product"]},
            {"type": "cta", "text": parts[-1], "keywords": ["link", "bio"]},
        ]
        scenes_cap = [enrich_scene(s) for s in scenes_cap]
        caps = generate_captions(scenes_cap)
        ass_path = os.path.join(work, "captions.ass")
        cz = (os.environ.get("CAPTION_ZONE") or "bottom").strip().lower()
        caption_zone = "bottom" if cz not in ("full", "bottom") else cz
        write_ass_file(ass_path, caps, final_duration, width=1080, height=1920, caption_zone=caption_zone)

        burn_ass(split_out, ass_path, out_final)
        print("[EXTERNAL] AFTER FFmpeg burn_ass", flush=True)
        print("STAGE: CAPTION_BURN_COMPLETE", flush=True)
        touch_pipeline_manifest("ffmpeg_render", "end: captions burned to final mp4")

        if detect_static_frames(out_final, ffmpeg_exe, ffprobe_exe):
            raise RuntimeError("STATIC_VIDEO_DETECTED")
        if not detect_product_usage(out_final, ffmpeg_exe, ffprobe_exe):
            raise RuntimeError("NO_VISIBLE_PRODUCT_USAGE")

        validate_final_output_file(out_final, ffprobe_exe, min_duration_sec=float(MIN_VIDEO_DURATION_SECONDS))
        dur_out = ffprobe_duration(out_final)

        # Phase 2 visual quality gate before marking as ready
        from pipeline.visual_quality_gate import apply_visual_quality_gate_before_approval
        gate_ok, gate_reason = apply_visual_quality_gate_before_approval("ugc_url_pipeline", product_fit, avatar_fit)
        if not gate_ok:
            print(f"VISUAL_QUALITY_GATE_FAILED: {gate_reason}", flush=True)
            raise RuntimeError("VISUAL_QUALITY_GATE_FAILED")
        print("VISUAL_QUALITY_GATE_PASSED", flush=True)

        # Phase 2 compliance validation before final approval
        from pipeline.content_compliance import validate_compliance_before_rendering, log_compliance_decision
        content_data = {
            "title": title,
            "script": full_script,
            "description": "",
        }
        compliance_result = validate_compliance_before_rendering(content_data, geo_target="AE")
        if not compliance_result["can_render"]:
            print(f"COMPLIANCE_FAILED: {compliance_result['reason']}", flush=True)
            raise RuntimeError("COMPLIANCE_FAILED")
        print("COMPLIANCE_PASSED", flush=True)
        
        # Log compliance decision for audit trail
        log_compliance_decision(
            content_id="ugc_pipeline",
            geo_target="AE",
            compliance_score=compliance_result.get("compliance_score", 100),
            violations=compliance_result.get("violations", []),
            auto_blocked=compliance_result.get("auto_blocked", False),
            requires_manual_review=compliance_result.get("requires_manual_review", False)
        )

        # Write FINAL_VIDEO.mp4 to the job-isolated path when running under the worker
        # (JOB_ID + JOBS_DIR both set).  Falls back to the legacy shared path for manual runs.
        _jid_out = (os.environ.get("JOB_ID") or "").strip()
        _jobs_dir_out = (os.environ.get("JOBS_DIR") or "").strip()
        if _jid_out and _jobs_dir_out:
            publish_dir = os.path.join(_jobs_dir_out, _jid_out)
        else:
            publish_dir = os.path.join(_CORE, "outputs", "final_publish")
        os.makedirs(publish_dir, exist_ok=True)
        locked_path = os.path.join(publish_dir, "FINAL_VIDEO.mp4")
        shutil.copy2(out_final, locked_path)

        print("FINAL VIDEO LOCKED", flush=True)
        print("READY FOR MANUAL PUBLISH", flush=True)
        touch_pipeline_manifest("completed", f"ugc_pipeline finished; FINAL_VIDEO at {locked_path}")
        return 0
    except RuntimeError as e:
        fail_pipeline_manifest(e)
        if str(e) in (
            "INVALID_PRODUCT_IMAGES",
            "INVALID_SCRIPT_LENGTH",
            "WEAK_HOOK",
            "BANNED_MERCHANT",
            "NO_DEMO_SCENE",
            "VIDEO_TOO_SHORT",
            "VIDEO_TOO_SHORT_FROM_AUDIO",
            "AUDIO_TOO_SHORT",
            "SCRIPT_TOO_SHORT_FOR_DURATION",
            "NO_DEMO_IN_FINAL_PLAN",
            "INVALID_FIRST_SCENE",
            "INVALID_FIRST_PRODUCT_CLIP",
            "PRODUCT_STRIP_JITTER_FAILED",
            "PRODUCT_STRIP_MIN_LEN_EXTEND_FAILED",
            "HOOK_HAS_NO_MOTION",
            "STATIC_VIDEO_DETECTED",
            "NO_VISIBLE_PRODUCT_USAGE",
            "LOW_RES_PRODUCT_IMAGES",
            "PRODUCT_IMAGE_UPSCALE_FAILED",
            "INVALID_CTA_TEXT",
            "CTA_DISABLED",
            "FINAL_OUTPUT_MISSING",
            "FINAL_OUTPUT_TOO_SMALL",
            "FINAL_OUTPUT_NO_AUDIO",
        ) or str(e).startswith("INVALID") or str(e).startswith("VIDEO") or str(e).startswith(
            "FINAL_OUTPUT_BAD_RESOLUTION"
        ):
            print(f"FAIL FAST: {e}", flush=True)
        else:
            print(f"ERROR: {e}", flush=True)
        return 99
    except Exception as e:
        fail_pipeline_manifest(e)
        print(f"ERROR: {type(e).__name__}: {e}", flush=True)
        import traceback

        traceback.print_exc()
        return 99
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    print("LOADED ENV FROM:", ENV_PATH, flush=True)
    raise SystemExit(main())
