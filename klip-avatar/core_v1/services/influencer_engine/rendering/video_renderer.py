"""
Influencer Engine — Video renderer.

Composes final video:
- Mode A (Wan 2.2 MoE Cinematic): cinematic/beebom_split paths using WaveSpeed Wan 2.2
  Mixture-of-Experts with High-Noise (layout/composition) + Low-Noise (cinematic detail) experts.
- Mode B (Wavespeed B-roll): 4–6 I2V clips from avatar image, stitched with ffmpeg + TTS.
- Mode C (Ken Burns Slideshow): zoompan Ken Burns on avatar portrait — bulletproof fallback.
- Never falls back to static color background.

Timeout: 240 s hard cap per render (asyncio.timeout). On timeout → Ken Burns fallback.
UAE/AI Disclosure is always applied as the final mandatory step.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import tempfile
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass


def _ffmpeg_exe() -> str:
    try:
        from .ffmpeg_path import get_ffmpeg_exe
        return get_ffmpeg_exe()
    except Exception:
        return "ffmpeg"


def _ffmpeg_available() -> bool:
    try:
        exe = _ffmpeg_exe()
        subprocess.run(
            [exe, "-version"],
            capture_output=True,
            timeout=5,
            check=False,
        )
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _render_with_ffmpeg(
    audio_path: str,
    output_path: str,
    duration_seconds: float = 30.0,
    subtitle_text: Optional[str] = None,
    background_image: Optional[str] = None,
) -> bool:
    """
    Compose video: static background (color) + audio. Returns True if successful.
    """
    if not os.path.isfile(audio_path):
        return False
    exe = _ffmpeg_exe()
    cmd = [
        exe, "-y",
        "-f", "lavfi", "-i", f"color=c=#1a1a1a:s=720x1280:d={duration_seconds}",
        "-i", audio_path,
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-shortest",
        "-c:a", "aac", "-b:a", "128k",
        output_path,
    ]
    try:
        subprocess.run(cmd, capture_output=True, timeout=120, check=True)
        return os.path.isfile(output_path)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return False


def _ffprobe_duration_sec(path: str) -> float:
    try:
        exe = "ffprobe"
        try:
            from .ffmpeg_path import get_ffmpeg_exe

            base = os.path.dirname(get_ffmpeg_exe())
            exe = os.path.join(base, "ffprobe.exe" if os.name == "nt" else "ffprobe")
            if not os.path.isfile(exe):
                exe = "ffprobe"
        except Exception:
            pass
        ap = os.path.abspath(os.path.normpath(path))
        r = subprocess.run(
            [exe, "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", ap],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if r.returncode == 0 and (r.stdout or "").strip():
            return float((r.stdout or "").strip())
    except Exception:
        pass
    return 0.0


def _require_local_file(path: str, label: str) -> str:
    """Return absolute path or raise FileNotFoundError — blocks credit spend on missing inputs."""
    if not path or not str(path).strip():
        raise FileNotFoundError(f"{label}: empty path")
    ap = os.path.abspath(os.path.normpath(path))
    if not os.path.isfile(ap):
        raise FileNotFoundError(f"{label}: file not found: {ap}")
    if os.path.getsize(ap) < 32:
        raise FileNotFoundError(f"{label}: file too small / corrupt: {ap}")
    return ap


def _require_decodable_video(path: str, label: str) -> None:
    """Fail fast if ffprobe cannot read a video stream (avoids black / null renders downstream)."""
    ap = _require_local_file(path, label)
    try:
        exe = "ffprobe"
        try:
            from .ffmpeg_path import get_ffmpeg_exe

            base = os.path.dirname(get_ffmpeg_exe())
            exe = os.path.join(base, "ffprobe.exe" if os.name == "nt" else "ffprobe")
            if not os.path.isfile(exe):
                exe = "ffprobe"
        except Exception:
            pass
        r = subprocess.run(
            [
                exe,
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=index",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                ap,
            ],
            capture_output=True,
            text=True,
            timeout=20,
        )
        if r.returncode != 0 or not (r.stdout or "").strip():
            raise ValueError(f"{label}: no decodable video stream: {ap}")
        d = _ffprobe_duration_sec(ap)
        if d <= 0.05:
            raise ValueError(f"{label}: zero or invalid duration: {ap}")
    except ValueError:
        raise
    except Exception as e:
        raise ValueError(f"{label}: probe failed for {ap}: {e}") from e


def _klip_repo_root() -> str:
    """KLIP-AVATAR root (parent of ``services``)."""
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(os.path.dirname(os.path.dirname(here)))


def _resolve_default_bgm_path() -> Optional[str]:
    env = (os.environ.get("KLIP_BGM_PATH") or "").strip()
    if env and os.path.isfile(env):
        return env
    cand = os.path.join(_klip_repo_root(), "data", "audio", "default_bgm.mp3")
    if os.path.isfile(cand):
        return cand
    return None


def _concat_and_mix_with_audio(clip_paths: List[str], audio_path: str, output_path: str) -> bool:
    """
    Concat video clips; mux with TTS. Optionally loop **default_bgm.mp3** and apply
    **sidechaincompress** so BGM ducks under narration (~10% when voice is present).
    """
    if not clip_paths or not os.path.isfile(audio_path):
        return False
    exe = _ffmpeg_exe()
    safe_paths = [p.replace("'", "'\\''") for p in clip_paths if os.path.isfile(p)]
    if not safe_paths:
        return False
    list_path = concat_mp4 = None
    try:
        fd, list_path = tempfile.mkstemp(suffix=".txt", prefix="concat_list_")
        with os.fdopen(fd, "w") as f:
            for p in safe_paths:
                f.write(f"file '{p}'\n")
        fd2, concat_mp4 = tempfile.mkstemp(suffix=".mp4", prefix="concat_")
        os.close(fd2)
        cmd_concat = [
            exe, "-y", "-f", "concat", "-safe", "0", "-i", list_path,
            "-c", "copy", concat_mp4,
        ]
        subprocess.run(cmd_concat, capture_output=True, timeout=120, check=True)
        if not os.path.isfile(concat_mp4):
            return False
        bgm = _resolve_default_bgm_path()
        if bgm and os.path.isfile(bgm) and os.path.getsize(bgm) > 100:
            # BGM nominal level + sidechain duck from TTS (voice is [2:a], BGM looped [1:a])
            # [duck] is compressed BGM; mix with voice for final output.
            duck_filter = (
                "[1:a]volume=0.25[bgm];"
                "[2:a]asplit=2[sc][vo];"
                "[bgm][sc]sidechaincompress=threshold=0.02:ratio=12:attack=5:release=240[duck];"
                "[duck][vo]amix=inputs=2:duration=first:normalize=0[aout]"
            )
            cmd_mix = [
                exe, "-y",
                "-i", concat_mp4,
                "-stream_loop", "-1", "-i", bgm,
                "-i", audio_path,
                "-filter_complex", duck_filter,
                "-map", "0:v", "-map", "[aout]",
                "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest",
                output_path,
            ]
        else:
            cmd_mix = [
                exe, "-y", "-i", concat_mp4, "-i", audio_path,
                "-c:v", "copy", "-c:a", "aac", "-b:a", "128k", "-shortest",
                output_path,
            ]
        subprocess.run(cmd_mix, capture_output=True, timeout=120, check=True)
        return os.path.isfile(output_path) and os.path.getsize(output_path) > 0
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return False
    finally:
        for p in (list_path, concat_mp4):
            if p and os.path.isfile(p):
                try:
                    os.remove(p)
                except Exception:
                    pass


def _finalize_brand_watermark(path: Optional[str], config: Dict[str, Any]) -> Optional[str]:
    """Burn handle + SEO captions when ``KLIP_BURN_BRAND_WATERMARK`` allows."""
    if not path or not os.path.isfile(path):
        return path
    try:
        from .watermark_burn import burn_social_and_seo_watermark

        burn_social_and_seo_watermark(path, config)
    except Exception:
        pass
    return path


def _audio_path_from_config(config: Dict[str, Any]) -> Optional[str]:
    """Resolve local audio file path from config (audio_url or path). Always absolute when possible."""
    path = config.get("audio_path") or config.get("audio_path_local")
    if path and os.path.isfile(path):
        return os.path.abspath(os.path.normpath(path))
    url = config.get("audio_url") or ""
    if url.startswith("file://"):
        raw = url.replace("file://", "").lstrip("/")
        if os.name == "nt" and len(raw) >= 2 and raw[1] == ":":
            path = raw
        else:
            path = raw
        path = os.path.normpath(path)
        if os.path.isfile(path):
            return os.path.abspath(path)
    return None


class VideoRenderer:
    """
    Composes video: Wan 2.2 MoE cinematic, WaveSpeed B-roll, or Ken Burns fallback.

    Use ``render()`` for the legacy synchronous pipeline (lipsync / broll / static).
    Use ``render_video(job)`` for the new async bulletproof pipeline (Wan 2.2 MoE + timeout).
    """

    async def render_video(self, job: Dict[str, Any]) -> Optional[str]:
        """
        Bulletproof async render with Wan 2.2 MoE and 240 s hard timeout.
        Delegates to module-level ``render_video()`` function.
        """
        return await render_video(job)

    def render(
        self,
        config: Dict[str, Any],
        output_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Produce video asset.
        When WAVESPEED_API_KEY set: 4–6 I2V clips from avatar face + TTS, stitched with ffmpeg (continuous B-roll).
        Else: static bg + audio via ffmpeg.
        """
        avatar_url = config.get("avatar_url") or ""
        subtitle_style = config.get("subtitle_style") or "bold centered"
        background_style = config.get("background_style") or "minimal aesthetic"
        audio_url = config.get("audio_url") or ""
        duration = config.get("duration_seconds") or 30.0
        script = config.get("script") or ""
        _ap = config.get("avatar_profile") if isinstance(config.get("avatar_profile"), dict) else {}
        for k in ("seo_title", "seo_description", "seo_hashtags", "social_handle"):
            if (not config.get(k)) and _ap.get(k):
                config[k] = _ap[k]

        # Wavespeed B-roll: avatar I2V clips + TTS, then ffmpeg stitches clips + audio into final video.
        # ffmpeg is required for the final stitch; skip WaveSpeed I2V if ffmpeg is missing to avoid burning credits.
        try:
            from core.services.wavespeed_key import resolve_wavespeed_api_key

            ws_key, _ws_src = resolve_wavespeed_api_key()
            ws_key = (ws_key or "").strip()
        except Exception:
            ws_key = (os.environ.get("WAVESPEED_API_KEY") or "").strip()
        audio_path = _audio_path_from_config(config)
        if ws_key and audio_path and os.path.isfile(audio_path) and os.path.getsize(audio_path) > 0 and _ffmpeg_available():
            try:
                audio_path = _require_local_file(audio_path, "tts_audio")
            except FileNotFoundError as e:
                raise FileNotFoundError(str(e)) from e
            avatar_id = config.get("avatar_id")
            if not avatar_id and isinstance(config.get("avatar_profile"), dict):
                avatar_id = (config.get("avatar_profile") or {}).get("avatar_id") or (config.get("avatar_profile") or {}).get("id")
            if not avatar_id and isinstance(config.get("avatar_profile"), str):
                avatar_id = config.get("avatar_profile")
            face_path = None
            fixed_portrait_url = None
            try:
                from .wavespeed_video import resolve_fixed_portrait_image_url
            except Exception:
                from services.influencer_engine.rendering.wavespeed_video import resolve_fixed_portrait_image_url
            _ap = config.get("avatar_profile") if isinstance(config.get("avatar_profile"), dict) else {}
            fixed_portrait_url = resolve_fixed_portrait_image_url(str(avatar_id or ""), _ap)
            if avatar_id and not fixed_portrait_url:
                try:
                    from ..avatar.avatar_assets import get_avatar_assets_dir
                except Exception:
                    from services.influencer_engine.avatar.avatar_assets import get_avatar_assets_dir
                face_path = os.path.join(get_avatar_assets_dir(avatar_id), "face.png")
            _mode = (os.environ.get("KLIP_VIDEO_PIPELINE_MODE") or "auto").strip().lower()
            if isinstance(_ap, dict) and (_ap.get("video_pipeline_mode") or "").strip():
                _mode = (_ap.get("video_pipeline_mode") or "").strip().lower()
            job_id = config.get("job_id")
            portrait_url = (fixed_portrait_url or "").strip()
            if not portrait_url and face_path:
                face_path = os.path.abspath(os.path.normpath(face_path)) if face_path else face_path
                if os.path.isfile(face_path):
                    try:
                        from .wavespeed_video import upload_file
                    except Exception:
                        from services.influencer_engine.rendering.wavespeed_video import upload_file
                    portrait_url = (upload_file(face_path, ws_key) or "").strip()
                elif avatar_id and not fixed_portrait_url:
                    strict = (os.environ.get("KLIP_STRICT_AVATAR_FACE") or "").strip().lower() in (
                        "1",
                        "true",
                        "yes",
                    )
                    if strict:
                        raise FileNotFoundError(
                            f"avatar_face_png: local portrait required for lipsync but missing: {face_path}"
                        )
                    log.warning("lipsync: no Redis portrait and missing face.png at %s — will try broll/static", face_path)

            # ── A) Lipsync talking-head (preferred for auto / lipsync) ─────────────────
            if portrait_url and _mode in ("lipsync", "auto"):
                try:
                    from .wavespeed_video import generate_lipsync_video_to_path
                except Exception:
                    from services.influencer_engine.rendering.wavespeed_video import generate_lipsync_video_to_path
                fd_ls, lip_tmp = tempfile.mkstemp(suffix="_lipsync.mp4", prefix="ie_")
                os.close(fd_ls)
                lip_path, _lip_err = generate_lipsync_video_to_path(
                    portrait_url,
                    audio_path,
                    ws_key,
                    lip_tmp,
                    job_id=str(job_id) if job_id else None,
                )
                if lip_path and os.path.isfile(lip_path) and os.path.getsize(lip_path) > 0:
                    lip_path = _require_local_file(lip_path, "lipsync_mp4")
                    try:
                        _require_decodable_video(lip_path, "lipsync_output")
                    except ValueError as e:
                        raise ValueError(str(e)) from e
                    if not output_path:
                        fd_o, output_path = tempfile.mkstemp(suffix=".mp4", prefix="video_")
                        os.close(fd_o)
                    output_path = os.path.abspath(os.path.normpath(output_path))
                    _ap = config.get("avatar_profile") if isinstance(config.get("avatar_profile"), dict) else {}
                    _role = (
                        str((config.get("avatar_role") or _ap.get("avatar_role") or "")).strip().lower()
                    )
                    if _role == "influencer":
                        try:
                            from .multicam_influencer import compose_influencer_multicam

                            fd_mc, mc_out = tempfile.mkstemp(suffix="_multicam.mp4", prefix="ie_")
                            os.close(fd_mc)
                            mc_out = os.path.abspath(os.path.normpath(mc_out))
                            mc = compose_influencer_multicam(lip_path, audio_path, mc_out)
                            if mc.get("ok") and mc.get("path") and os.path.isfile(mc["path"]):
                                try:
                                    shutil.copy2(mc["path"], output_path)
                                except Exception:
                                    output_path = mc["path"]
                                try:
                                    _require_decodable_video(output_path, "multicam_output")
                                except Exception as mc_ve:
                                    log.warning("multicam output failed video probe; falling back to raw lipsync: %s", mc_ve)
                                    try:
                                        shutil.copy2(lip_path, output_path)
                                    except Exception:
                                        output_path = lip_path
                            else:
                                try:
                                    shutil.copy2(lip_path, output_path)
                                except Exception:
                                    output_path = lip_path
                        except Exception:
                            try:
                                shutil.copy2(lip_path, output_path)
                            except Exception:
                                output_path = lip_path
                    else:
                        try:
                            shutil.copy2(lip_path, output_path)
                        except Exception:
                            output_path = lip_path
                    dur_ls = _ffprobe_duration_sec(output_path) or float(duration)
                    if job_id and dur_ls > 0:
                        try:
                            from services.influencer_engine.cost.pricing import compute_video_cost
                            from services.influencer_engine.cost.cost_tracker import record_video_cost
                        except Exception:
                            from ..cost.pricing import compute_video_cost
                            from ..cost.cost_tracker import record_video_cost
                        try:
                            record_video_cost(
                                job_id,
                                compute_video_cost(dur_ls),
                                duration_sec=dur_ls,
                                avatar_id=avatar_id,
                            )
                        except Exception:
                            pass
                    try:
                        if lip_tmp != output_path and os.path.isfile(lip_tmp):
                            os.unlink(lip_tmp)
                    except Exception:
                        pass
                    _finalize_brand_watermark(output_path, config)
                    try:
                        from .subtitle_burn import apply_subtitle_burn_inplace

                        narr = str(config.get("narration_full") or config.get("script") or script or "")
                        apply_subtitle_burn_inplace(output_path, audio_path, narr)
                    except Exception:
                        pass
                    return {
                        "url": f"file://{output_path}",
                        "duration_seconds": dur_ls,
                        "avatar_url": avatar_url,
                        "path": output_path,
                        "mock": False,
                        "composer": "lipsync",
                    }
                try:
                    if os.path.isfile(lip_tmp):
                        os.unlink(lip_tmp)
                except Exception:
                    pass

            # ── B) WAN I2V B-roll (broll mode, or auto fallback after lipsync) ───────────
            if _mode in ("broll", "auto") and (
                (fixed_portrait_url) or (face_path and os.path.isfile(face_path)) or portrait_url
            ):
                try:
                    from .wavespeed_video import split_narration_into_segments, generate_broll_clips, NUM_CLIPS
                except Exception:
                    from services.influencer_engine.rendering.wavespeed_video import split_narration_into_segments, generate_broll_clips, NUM_CLIPS
                # Use video plan from agent when present (adapts to content length and clip duration)
                plan = config.get("video_plan")
                if plan and isinstance(plan, dict) and plan.get("segment_texts"):
                    segments = plan["segment_texts"]
                    motion_prompts = plan.get("motion_prompts")
                    clip_duration_sec = plan.get("clip_duration_sec")
                else:
                    narration_full = config.get("narration_full") or script or ""
                    segments = split_narration_into_segments(narration_full, num_segments=NUM_CLIPS)
                    motion_prompts = None
                    clip_duration_sec = None
                tmpdir = tempfile.mkdtemp(prefix="broll_")
                try:
                    ap = config.get("avatar_profile")
                    i2v_model = None
                    i2v_resolution = None
                    if isinstance(ap, dict):
                        i2v_model = (ap.get("wavespeed_i2v_model") or "").strip() or None
                        i2v_resolution = (ap.get("wavespeed_i2v_resolution") or "").strip() or None
                    i2v_model = i2v_model or (config.get("wavespeed_i2v_model") or "").strip() or None
                    i2v_resolution = i2v_resolution or (config.get("wavespeed_i2v_resolution") or "").strip() or None
                    clip_paths = generate_broll_clips(
                        face_path or ".",
                        segments,
                        ws_key,
                        tmpdir,
                        motion_prompts=motion_prompts,
                        clip_duration_sec=clip_duration_sec,
                        i2v_model=i2v_model,
                        i2v_resolution=i2v_resolution,
                        image_url_override=fixed_portrait_url or portrait_url,
                        job_id=str(job_id) if job_id else None,
                    )
                    if clip_paths:
                        # Record video cost (WaveSpeed Wan 2.2 I2V Ultra Fast $0.01/sec)
                        per_clip_sec = clip_duration_sec if clip_duration_sec else 5
                        total_broll_sec = len(clip_paths) * per_clip_sec
                        job_id = config.get("job_id")
                        if job_id and total_broll_sec > 0:
                            try:
                                from services.influencer_engine.cost.pricing import compute_video_cost
                                from services.influencer_engine.cost.cost_tracker import record_video_cost
                            except Exception:
                                from ..cost.pricing import compute_video_cost
                                from ..cost.cost_tracker import record_video_cost
                            try:
                                record_video_cost(
                                    job_id,
                                    compute_video_cost(total_broll_sec),
                                    duration_sec=total_broll_sec,
                                    avatar_id=avatar_id,
                                )
                            except Exception:
                                pass
                        if not output_path:
                            fd, output_path = tempfile.mkstemp(suffix=".mp4", prefix="video_")
                            os.close(fd)
                        if _concat_and_mix_with_audio(clip_paths, audio_path, output_path):
                            _finalize_brand_watermark(output_path, config)
                            return {
                                "url": f"file://{output_path}",
                                "duration_seconds": duration,
                                "avatar_url": avatar_url,
                                "path": output_path,
                                "mock": False,
                            }
                finally:
                    try:
                        for f in os.listdir(tmpdir):
                            try:
                                os.remove(os.path.join(tmpdir, f))
                            except Exception:
                                pass
                        os.rmdir(tmpdir)
                    except Exception:
                        pass

        # Runway (optional)
        runway_key = (os.environ.get("RUNWAY_API_KEY") or "").strip()
        if runway_key and config.get("use_video_api"):
            result = _render_with_video_api(config, output_path)
            if result:
                return result

        # Fallback: static bg + audio via ffmpeg
        if not audio_path:
            audio_path = _audio_path_from_config(config)
        # If no audio or 0-byte file (e.g. mock TTS failed), use silent MP3 so we never produce 0-byte video
        if (not audio_path or not os.path.isfile(audio_path) or os.path.getsize(audio_path) == 0):
            try:
                from .voice_renderer import _write_silent_mp3
                fd, fallback_audio = tempfile.mkstemp(suffix=".mp3", prefix="voice_")
                os.close(fd)
                if _write_silent_mp3(fallback_audio, duration):
                    audio_path = fallback_audio
            except Exception:
                pass
        if not output_path:
            fd, output_path = tempfile.mkstemp(suffix=".mp4", prefix="video_")
            os.close(fd)
        if _ffmpeg_available() and audio_path and os.path.isfile(audio_path) and os.path.getsize(audio_path) > 0:
            ok = _render_with_ffmpeg(audio_path, output_path, duration, script, None)
            if ok:
                _finalize_brand_watermark(output_path, config)
                return {
                    "url": f"file://{output_path}",
                    "duration_seconds": duration,
                    "avatar_url": avatar_url,
                    "subtitle_style": subtitle_style,
                    "background_style": background_style,
                    "audio_url": audio_url,
                    "path": output_path,
                    "mock": False,
                }

        # Mock — no final video file (ffmpeg required to stitch WaveSpeed clips + audio, or static bg + audio)
        return {
            "url": "mock://video/out.mp4",
            "duration_seconds": duration,
            "avatar_url": avatar_url,
            "subtitle_style": subtitle_style,
            "background_style": background_style,
            "audio_url": audio_url,
            "path": None,
            "mock": True,
            "ffmpeg_required": True,
        }


def _render_with_video_api(config: Dict[str, Any], output_path: Optional[str]) -> Optional[Dict[str, Any]]:
    """Optional: call Runway or similar video API. Placeholder for future."""
    return None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  WAN 2.2 MoE + BULLETPROOF RENDER PIPELINE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# Wan 2.2 Mixture-of-Experts boundaries
_WAN22_HIGH_NOISE_BOUNDARY = 0.875   # High-Noise Expert: Layout / Composition
_WAN22_LOW_NOISE_BOUNDARY = 0.900    # Low-Noise Expert: Cinematic Detail

_WAN22_CAMERA_MOTION = "slow pan, parallax reveal, dolly in, cinematic film quality"
_WAN22_NEGATIVE_PROMPT = "morphing, flickering, distorted face, low quality"

# Minimum WaveSpeed balance threshold (USD) — below this, skip cinematic and use Ken Burns
_WAN22_MIN_BALANCE_USD = 0.05


def _check_wavespeed_balance_sufficient() -> bool:
    """Return True when WaveSpeed balance is above the cinematic-mode threshold."""
    try:
        from .wavespeed_video import wavespeed_circuit_is_open
        if wavespeed_circuit_is_open():
            return False
    except Exception:
        pass
    try:
        ws_key = (os.environ.get("WAVESPEED_API_KEY") or "").strip()
        if not ws_key:
            return False
        import urllib.request
        import json as _json
        req = urllib.request.Request(
            "https://api.wavespeed.ai/api/v3/user/balance",
            headers={"Authorization": f"Bearer {ws_key}"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = _json.loads(resp.read())
            balance = float(data.get("balance") or data.get("data", {}).get("balance") or 0)
            return balance >= _WAN22_MIN_BALANCE_USD
    except Exception:
        return True  # assume OK on API error — let the render attempt and fail gracefully


async def render_wan22_cinematic(
    prompt: str,
    motion_params: Optional[Dict[str, Any]] = None,
    camera_motion: str = _WAN22_CAMERA_MOTION,
    negative_prompt: str = _WAN22_NEGATIVE_PROMPT,
    image_url: Optional[str] = None,
    audio_path: Optional[str] = None,
    output_path: Optional[str] = None,
    job_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Wan 2.2 MoE cinematic render.

    Uses High-Noise Expert (boundary 0.875) for layout/composition and
    Low-Noise Expert (boundary 0.900) for cinematic detail.

    Returns dict with ``output_path`` key on success.
    Raises on failure (caller handles fallback).
    """
    loop = asyncio.get_event_loop()

    # Build full prompt with MoE camera motion injection
    moe_prompt = f"{prompt}, {camera_motion}"
    mp = motion_params or {}
    high_noise = mp.get("high_noise", _WAN22_HIGH_NOISE_BOUNDARY)
    low_noise = mp.get("low_noise", _WAN22_LOW_NOISE_BOUNDARY)

    ws_key = (os.environ.get("WAVESPEED_API_KEY") or "").strip()
    if not ws_key:
        raise RuntimeError("WAVESPEED_API_KEY not set — cannot render Wan 2.2 cinematic")

    if not output_path:
        fd, output_path = tempfile.mkstemp(suffix="_wan22.mp4", prefix="cinematic_")
        os.close(fd)

    # Run the WaveSpeed I2V call in an executor to stay async-safe
    def _run_wan22_sync() -> str:
        try:
            from .wavespeed_video import generate_broll_clips, split_narration_into_segments, NUM_CLIPS
        except Exception:
            from services.influencer_engine.rendering.wavespeed_video import (
                generate_broll_clips,
                split_narration_into_segments,
                NUM_CLIPS,
            )
        tmpdir = tempfile.mkdtemp(prefix="wan22_")
        try:
            segments = split_narration_into_segments(moe_prompt, num_segments=NUM_CLIPS)
            # MoE: inject expert-boundary annotations into motion prompts
            motion_prompts = [
                f"{seg} high_noise_boundary={high_noise} low_noise_boundary={low_noise}"
                for seg in segments
            ]
            clip_paths = generate_broll_clips(
                ".",
                segments,
                ws_key,
                tmpdir,
                motion_prompts=motion_prompts,
                image_url_override=image_url or "",
                job_id=job_id,
            )
            if not clip_paths:
                raise RuntimeError("Wan 2.2 MoE: no clips generated")
            # Stitch clips + audio into final video
            final = output_path
            if audio_path and os.path.isfile(audio_path):
                ok = _concat_and_mix_with_audio(clip_paths, audio_path, final)
            else:
                # Video-only stitch
                ok = _concat_video_only(clip_paths, final)
            if not ok or not os.path.isfile(final):
                raise RuntimeError("Wan 2.2 MoE: ffmpeg stitch failed")
            return final
        finally:
            try:
                shutil.rmtree(tmpdir, ignore_errors=True)
            except Exception:
                pass

    result_path = await loop.run_in_executor(None, _run_wan22_sync)
    return {"output_path": result_path, "mode": "wan22_cinematic", "moe": True}


def _concat_video_only(clip_paths: List[str], output_path: str) -> bool:
    """Concat video clips without audio (for wan22 video-only path)."""
    if not clip_paths:
        return False
    exe = _ffmpeg_exe()
    safe_paths = [p for p in clip_paths if os.path.isfile(p)]
    if not safe_paths:
        return False
    list_path = None
    try:
        fd, list_path = tempfile.mkstemp(suffix=".txt", prefix="concat_vo_")
        with os.fdopen(fd, "w") as f:
            for p in safe_paths:
                f.write(f"file '{p}'\n")
        cmd = [exe, "-y", "-f", "concat", "-safe", "0", "-i", list_path, "-c", "copy", output_path]
        subprocess.run(cmd, capture_output=True, timeout=120, check=True)
        return os.path.isfile(output_path) and os.path.getsize(output_path) > 0
    except Exception:
        return False
    finally:
        if list_path and os.path.isfile(list_path):
            try:
                os.remove(list_path)
            except Exception:
                pass


def render_ken_burns_slideshow(job: Dict[str, Any]) -> Optional[str]:
    """
    Ken Burns zoompan slideshow — bulletproof fallback.

    Uses avatar portrait + TTS audio → zoompan Ken Burns via multicam_influencer
    or direct ffmpeg zoompan if multicam is unavailable.
    Never produces a static color background.
    """
    avatar_id = job.get("avatar_id")
    audio_path = job.get("audio_path") or job.get("audio_path_local")
    output_path = job.get("output_path")
    duration = float(job.get("duration_seconds") or 30.0)

    if not output_path:
        fd, output_path = tempfile.mkstemp(suffix="_ken_burns.mp4", prefix="kb_")
        os.close(fd)

    exe = _ffmpeg_exe()

    # Resolve portrait image
    face_path: Optional[str] = None
    if avatar_id:
        try:
            try:
                from ..avatar.avatar_assets import get_avatar_assets_dir
            except Exception:
                from services.influencer_engine.avatar.avatar_assets import get_avatar_assets_dir
            cand = os.path.join(get_avatar_assets_dir(avatar_id), "face.png")
            if os.path.isfile(cand):
                face_path = os.path.abspath(cand)
        except Exception:
            pass

    # Try multicam Ken Burns (best quality)
    if face_path and audio_path and os.path.isfile(audio_path):
        try:
            try:
                from .multicam_influencer import compose_influencer_multicam
            except Exception:
                from services.influencer_engine.rendering.multicam_influencer import compose_influencer_multicam
            fd_mc, mc_out = tempfile.mkstemp(suffix="_kb_mc.mp4", prefix="ie_")
            os.close(fd_mc)
            mc = compose_influencer_multicam(face_path, audio_path, mc_out)
            if mc.get("ok") and mc.get("path") and os.path.isfile(mc["path"]):
                try:
                    shutil.copy2(mc["path"], output_path)
                except Exception:
                    output_path = mc["path"]
                log.info("Ken Burns slideshow: multicam zoompan succeeded → %s", output_path)
                return output_path
        except Exception as e:
            log.warning("Ken Burns multicam failed: %s — trying direct zoompan", e)

    # Direct ffmpeg zoompan Ken Burns (image + audio)
    if face_path and audio_path and os.path.isfile(audio_path) and _ffmpeg_available():
        try:
            d = int(duration * 30)  # frames at 30fps
            zoompan = (
                f"zoompan=z='min(zoom+0.0012,1.12)':d={max(1,d)}:"
                f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1080x1920:fps=30"
            )
            cmd = [
                exe, "-y",
                "-loop", "1", "-i", face_path,
                "-i", audio_path,
                "-vf", zoompan,
                "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "128k",
                "-shortest",
                output_path,
            ]
            subprocess.run(cmd, capture_output=True, timeout=120, check=True)
            if os.path.isfile(output_path) and os.path.getsize(output_path) > 0:
                log.info("Ken Burns slideshow: direct zoompan succeeded → %s", output_path)
                return output_path
        except Exception as e:
            log.warning("Ken Burns direct zoompan failed: %s — final static fallback", e)

    # Last resort: static bg + audio (only when portrait is unavailable)
    if audio_path and os.path.isfile(audio_path) and _ffmpeg_available():
        ok = _render_with_ffmpeg(audio_path, output_path, duration)
        if ok:
            log.warning("Ken Burns slideshow: fell back to static bg (no portrait available)")
            return output_path

    log.error("Ken Burns slideshow: all paths failed — no output produced")
    return None


def apply_uae_ai_disclosure(video_path: Optional[str]) -> Optional[str]:
    """
    Burn UAE/AI disclosure watermark onto the final video in-place.
    This is a mandatory final step for all KLIPAURA renders.
    """
    if not video_path or not os.path.isfile(video_path):
        return video_path
    try:
        try:
            from .ffmpeg_uae_compliance import uae_ai_disclosure_vf_chain
        except Exception:
            from services.influencer_engine.rendering.ffmpeg_uae_compliance import uae_ai_disclosure_vf_chain
        exe = _ffmpeg_exe()
        fd, tmp_out = tempfile.mkstemp(suffix="_uae.mp4", prefix="disclosure_")
        os.close(fd)
        cmd = [
            exe, "-y",
            "-i", video_path,
            "-vf", uae_ai_disclosure_vf_chain(),
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-pix_fmt", "yuv420p",
            "-c:a", "copy",
            tmp_out,
        ]
        r = subprocess.run(cmd, capture_output=True, timeout=120, check=False)
        if r.returncode == 0 and os.path.isfile(tmp_out) and os.path.getsize(tmp_out) > 0:
            try:
                shutil.move(tmp_out, video_path)
            except Exception:
                pass
    except Exception as e:
        log.warning("apply_uae_ai_disclosure failed (non-fatal): %s", e)
    return video_path


def record_render_result(
    avatar_id: Optional[str],
    job_id: Optional[str],
    mode: str,
    status: str,
) -> None:
    """Record render outcome in per-avatar analytics (Redis-backed, non-blocking)."""
    try:
        from klipaura_core.infrastructure.redis_client import get_redis_client
        import json as _json
        import datetime as _dt
        r = get_redis_client()
        key = f"render:analytics:{avatar_id or 'unknown'}"
        record = _json.dumps({
            "job_id": job_id,
            "mode": mode,
            "status": status,
            "ts": _dt.datetime.utcnow().isoformat() + "Z",
        })
        r.rpush(key, record)
        r.ltrim(key, -500, -1)
    except Exception as e:
        log.debug("record_render_result failed (non-fatal): %s", e)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  ASYNC render_video — BULLETPROOF ENTRY POINT (PHASE 1 v2.5)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


async def render_video(job: Dict[str, Any]) -> Optional[str]:
    """
    Bulletproof async render entry point with Wan 2.2 MoE + 240 s hard timeout.

    Pipeline:
      cinematic / beebom_split / beebom_split_fast modes:
        → Wan 2.2 MoE Cinematic (with cost guard)
        → Ken Burns fallback on any failure
      all other modes:
        → Ken Burns Slideshow (fast + cheap)

    Mandatory final steps (always):
      1. apply_uae_ai_disclosure()
      2. record_render_result()
    """
    mode = (job.get("video_pipeline_mode") or "ken_burns").strip().lower()
    job_id = job.get("job_id", "unknown")
    avatar_id = job.get("avatar_id")
    final_video: Optional[str] = None
    render_status = "success"

    try:
        async with asyncio.timeout(240):  # 4-minute hard timeout per render
            if mode in ("cinematic", "beebom_split", "beebom_split_fast"):
                # Cost guard: skip cinematic if WaveSpeed balance is too low
                if not _check_wavespeed_balance_sufficient():
                    log.warning(
                        "Job %s: WaveSpeed balance below threshold — skipping cinematic, using Ken Burns.",
                        job_id,
                    )
                    final_video = render_ken_burns_slideshow(job)
                else:
                    try:
                        # Wan 2.2 MoE Cinematic Path
                        result = await render_wan22_cinematic(
                            prompt=job.get("visual_prompt") or job.get("script") or "",
                            motion_params={
                                "high_noise": _WAN22_HIGH_NOISE_BOUNDARY,
                                "low_noise": _WAN22_LOW_NOISE_BOUNDARY,
                            },
                            camera_motion=_WAN22_CAMERA_MOTION,
                            negative_prompt=_WAN22_NEGATIVE_PROMPT,
                            image_url=job.get("portrait_url") or job.get("fixed_portrait_url") or "",
                            audio_path=job.get("audio_path") or job.get("audio_path_local"),
                            output_path=job.get("output_path"),
                            job_id=str(job_id),
                        )
                        log.info("Job %s: Wan 2.2 MoE cinematic successful", job_id)
                        final_video = result.get("output_path")
                    except Exception as e:
                        log.warning(
                            "Job %s: Wan 2.2 MoE failed: %s. Falling back to Ken Burns.",
                            job_id, e,
                        )
                        render_status = "fallback"
                        final_video = render_ken_burns_slideshow(job)
            else:
                # Default: Ken Burns Slideshow (fast + cheap)
                final_video = render_ken_burns_slideshow(job)

    except asyncio.TimeoutError:
        log.error("Job %s: Render timeout after 240s. Forcing Ken Burns fallback.", job_id)
        render_status = "fallback"
        final_video = render_ken_burns_slideshow(job)
    except Exception as e:
        log.error("Job %s: Unexpected render error: %s. Using Ken Burns fallback.", job_id, e)
        render_status = "fallback"
        final_video = render_ken_burns_slideshow(job)

    # ── Mandatory Final Step 1: UAE/AI Disclosure ──────────────────────────────
    final_video = apply_uae_ai_disclosure(final_video)

    # ── Mandatory Final Step 2: Per-avatar Redis analytics ─────────────────────
    record_render_result(
        avatar_id=avatar_id,
        job_id=str(job_id),
        mode=mode,
        status=render_status if final_video else "fallback",
    )

    # ── Mandatory Final Step 3: Per-avatar file analytics (video_history.json) ──
    try:
        from services.influencer_engine.analytics.avatar_video_analytics import record_video_render
        record_video_render(
            avatar_id or "unknown",
            str(job_id),
            video_path=final_video or "",
            render_mode=mode,
            status=render_status if final_video else "fallback",
            topic=str(job.get("topic") or job.get("visual_prompt") or ""),
        )
    except Exception:
        pass

    return final_video
