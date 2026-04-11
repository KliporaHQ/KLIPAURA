"""Orchestrate Cinematic V2: scene split → clip plan → clip render → stitch → audio → captions → export."""

from __future__ import annotations

import hashlib
import logging
import os
from typing import Any
import shutil
import subprocess
import tempfile

from .audio_engine import mix_audio
from .caption_engine import generate_captions, write_ass_file
from .clip_planner import generate_clip_plan
from .retention_engine import compute_between_transitions, enhance_scenes
from .scene_splitter import enrich_scene, split_script_into_scenes
from .video_config import resolve_video_config
from .settings import (
    CINEMATIC_BG_MUSIC_PATH,
    CINEMATIC_FETCH_STOCK,
    CINEMATIC_MUSIC_BODY_PATH,
    CINEMATIC_MUSIC_CTA_PATH,
    CINEMATIC_MUSIC_HOOK_PATH,
    CINEMATIC_TRANSITION_SEC,
)
from .stitch_engine import get_transition_filter, probe_duration_seconds, stitch_clips

logger = logging.getLogger(__name__)


DEFAULT_OUTPUT_WIDTH = 1080
DEFAULT_OUTPUT_HEIGHT = 1920
OUTPUT_FPS = 30


def _run(cmd: list[str], timeout: int = 900) -> tuple[bool, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode == 0, (r.stderr or r.stdout or "")
    except Exception as e:
        return False, str(e)


def _ffmpeg_escape_path(p: str) -> str:
    p = os.path.abspath(p).replace("\\", "/")
    if len(p) > 1 and p[1] == ":":
        p = p[0] + "\\:" + p[2:]
    return p


def _escape_drawtext(s: str) -> str:
    return (
        str(s)
        .replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("'", "'\\''")
        .replace("%", "\\%")
    )


def _speed_factor(clip: dict, idx: int) -> float:
    h = int(hashlib.md5(f"{idx}:{clip.get('prompt')}".encode(), usedforsecurity=False).hexdigest(), 16)
    return 1.0 + (h % 6) / 100.0  # 1.00–1.05


def _voice_segment_ends(scenes: list, voice_d: float) -> tuple[float | None, float | None]:
    """Approximate hook end and body/CTA boundary (seconds) from scene text lengths."""
    if voice_d <= 0:
        return None, None
    clean = [s for s in scenes if isinstance(s, dict)]
    if not clean:
        return None, None
    lens = [max(1, len(str(s.get("text", "")))) for s in clean]
    tot = sum(lens)
    starts: list[float] = []
    t = 0.0
    for L in lens:
        starts.append(t)
        t += voice_d * (L / tot)
    hook_end: float | None = None
    cta_start: float | None = None
    for i, s in enumerate(clean):
        st = str(s.get("type") or "")
        seg = voice_d * (lens[i] / tot)
        if st == "hook":
            hook_end = starts[i] + seg
        if st == "cta":
            cta_start = starts[i]
            break
    if hook_end is None:
        hook_end = min(voice_d * 0.15, voice_d * 0.5)
    if cta_start is None:
        cta_start = voice_d * 0.82
    body_end = max(hook_end + 0.2, min(cta_start, voice_d - 0.1))
    return hook_end, body_end


def _apply_per_scene_voice_sync(plan: list[dict[str, Any]], scenes: list, voice_d: float) -> tuple[float, float]:
    """
    Scale clip durations within each scene to that scene's share of voice time.
    Skips scenes with any lock_duration clip (hook pacing). Mutates plan clip dicts in place.
    Returns (raw_sum_before, scale_factor) where scale_factor = voice_d / raw_sum (diagnostic).
    """
    raw_sum = 0.0
    for block in plan:
        for c in block.get("clips") or []:
            if isinstance(c, dict):
                raw_sum += float(c.get("duration") or 5.0)

    blocks = [b for b in plan if b.get("clips")]
    total_chars = 0
    for b in blocks:
        si = int(b.get("scene_index", -1))
        if 0 <= si < len(scenes) and isinstance(scenes[si], dict):
            total_chars += max(1, len(str(scenes[si].get("text") or "")))
        else:
            total_chars += 1
    if total_chars <= 0:
        total_chars = 1

    for b in blocks:
        scene_clips = [c for c in (b.get("clips") or []) if isinstance(c, dict)]
        if not scene_clips:
            continue
        si = int(b.get("scene_index", -1))
        chars = (
            max(1, len(str(scenes[si].get("text") or "")))
            if 0 <= si < len(scenes) and isinstance(scenes[si], dict)
            else 1
        )
        voice_scene = voice_d * (chars / total_chars)
        scene_total = sum(float(c.get("duration") or 0.0) for c in scene_clips)
        if not any(c.get("lock_duration") for c in scene_clips):
            factor = voice_scene / max(scene_total, 0.001)
            for c in scene_clips:
                base = float(c.get("duration") or 5.0)
                c["duration"] = round(max(0.5, min(20.0, base * factor)), 4)

    scale_factor = voice_d / max(raw_sum, 0.001)
    return raw_sum, scale_factor


def _zoom_expr(motion: str, duration_sec: float, ow: int, oh: int) -> str:
    frames = max(1, int(duration_sec * OUTPUT_FPS))
    m = (motion or "").lower()
    if "zoom_out" in m:
        rate = "-0.0018"
        cap = "1.08"
    elif "pan_left" in m:
        rate = "0.0012"
        cap = "1.1"
    elif "pan_right" in m:
        rate = "0.0014"
        cap = "1.1"
    else:
        rate = "0.0018"
        cap = "1.12"
    return (
        f"zoompan=z='min(zoom+{rate},{cap})':d={frames}:"
        f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        f"s={ow}x{oh}:fps={OUTPUT_FPS}"
    )


def _grade_eq_vf() -> str:
    return "eq=contrast=1.1:saturation=1.1"


def _normalize_stock_video(
    src: str,
    duration_sec: float,
    idx: int,
    clip: dict,
    work_dir: str,
    ffmpeg_path: str,
    motion: str,
    ow: int,
    oh: int,
) -> str:
    """Scale/crop to target aspect, color grade, subtle motion, slight speed variation (silent)."""
    out = os.path.join(work_dir, f"norm_{idx:04d}.mp4")
    z = _zoom_expr(motion, duration_sec, ow, oh)
    vf = (
        f"fps={OUTPUT_FPS},"
        f"scale={ow}:{oh}:force_original_aspect_ratio=increase,"
        f"crop={ow}:{oh},"
        f"{_grade_eq_vf()},{z},scale={ow}:{oh},setsar=1"
    )
    cmd = [
        ffmpeg_path,
        "-y",
        "-i",
        src,
        "-t",
        f"{max(0.4, duration_sec):.4f}",
        "-vf",
        vf,
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-pix_fmt",
        "yuv420p",
        "-r",
        str(OUTPUT_FPS),
        out,
    ]
    ok, err = _run(cmd, timeout=300)
    if not ok or not os.path.isfile(out) or os.path.getsize(out) < 200:
        raise RuntimeError(f"stock video normalize failed: {err[:500]}")
    return out


def _normalize_stock_image(
    src: str,
    duration_sec: float,
    idx: int,
    clip: dict,
    work_dir: str,
    ffmpeg_path: str,
    motion: str,
    ow: int,
    oh: int,
) -> str:
    out = os.path.join(work_dir, f"norm_{idx:04d}.mp4")
    z = _zoom_expr(motion, duration_sec, ow, oh)
    vf = (
        f"scale={ow}:{oh}:force_original_aspect_ratio=increase,"
        f"crop={ow}:{oh},{_grade_eq_vf()},{z},scale={ow}:{oh},setsar=1"
    )
    cmd = [
        ffmpeg_path,
        "-y",
        "-loop",
        "1",
        "-i",
        src,
        "-t",
        f"{max(0.4, duration_sec):.4f}",
        "-vf",
        vf,
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-pix_fmt",
        "yuv420p",
        "-r",
        str(OUTPUT_FPS),
        out,
    ]
    ok, err = _run(cmd, timeout=300)
    if not ok or not os.path.isfile(out) or os.path.getsize(out) < 200:
        raise RuntimeError(f"stock image normalize failed: {err[:500]}")
    return out


def _render_one_clip(
    clip: dict,
    duration_sec: float,
    idx: int,
    work_dir: str,
    ffmpeg_path: str,
    local_images: list[str],
    ow: int,
    oh: int,
) -> str:
    """Produce one silent MP4 for a planned clip."""
    motion = str(clip.get("motion_type") or "ken_burns_center")
    raw = str(clip.get("prompt") or "scene")
    line = raw.replace("Cinematic vertical 9:16 shot: ", "")[:90]
    out = os.path.join(work_dir, f"clip_{idx:04d}.mp4")
    z = _zoom_expr(motion, duration_sec, ow, oh)
    sp = _speed_factor(clip, idx)
    dt = _escape_drawtext(line)
    draw = (
        f"drawtext=fontcolor=white:fontsize=42:borderw=2:bordercolor=black@0.6:"
        f"text='{dt}':x=(w-text_w)/2:y=h*0.72:box=1:boxcolor=black@0.35"
    )

    if local_images:
        img = local_images[idx % len(local_images)]
        vf = (
            f"scale={ow}:{oh}:force_original_aspect_ratio=decrease,"
            f"pad={ow}:{oh}:(ow-iw)/2:(oh-ih)/2,{_grade_eq_vf()},setpts=PTS/{sp:.4f},{z},{draw},"
            f"scale={ow}:{oh},setsar=1"
        )
        cmd = [
            ffmpeg_path,
            "-y",
            "-loop",
            "1",
            "-i",
            img,
            "-t",
            f"{duration_sec:.4f}",
            "-vf",
            vf,
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-pix_fmt",
            "yuv420p",
            "-r",
            str(OUTPUT_FPS),
            out,
        ]
    else:
        # Solid backdrop + text (no external video API)
        vf = f"{_grade_eq_vf()},setpts=PTS/{sp:.4f},{z},{draw},scale={ow}:{oh},setsar=1"
        cmd = [
            ffmpeg_path,
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c=0x141428:s={ow}x{oh}:d={duration_sec:.4f}",
            "-vf",
            vf,
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-pix_fmt",
            "yuv420p",
            "-r",
            str(OUTPUT_FPS),
            out,
        ]
    ok, err = _run(cmd, timeout=300)
    if not ok or not os.path.isfile(out) or os.path.getsize(out) < 200:
        raise RuntimeError(f"clip render failed idx={idx}: {err[:600]}")
    return out


def _voice_timed_to_target(
    voice_path: str,
    target_sec: float,
    work_dir: str,
    ffmpeg_path: str,
    ffprobe_path: str,
) -> tuple[str, float]:
    """
    Trim long narration to target_sec; pad short narration with silence so duration matches tier.
    Global duration budget for stitch + mix.
    """
    out = os.path.join(work_dir, "voice_timed.m4a")
    ts = f"{max(0.5, min(30.0, float(target_sec))):.4f}"
    af = f"apad=pad_dur=120,atrim=0:{ts}"
    ok, err = _run(
        [ffmpeg_path, "-y", "-i", voice_path, "-af", af, "-c:a", "aac", "-b:a", "192k", out],
        timeout=180,
    )
    if not ok or not os.path.isfile(out) or os.path.getsize(out) < 80:
        raise RuntimeError(f"voice duration normalize failed: {err[:500]}")
    vd = probe_duration_seconds(out, ffprobe_path)
    if vd <= 0:
        vd = float(target_sec)
    return out, float(vd)


def render_video_v2(
    script: str,
    voice_path: str,
    music_path: str | None = None,
    output_path: str | None = None,
    ffmpeg_path: str | None = None,
    ffprobe_path: str | None = None,
    image_paths: list[str] | None = None,
    job_id: str = "job",
    aspect_ratio: str = "9:16",
    duration_tier: str = "SHORT",
) -> str:
    """
    Full V2 pipeline: split → plan → per-clip render → crossfade stitch → duck-mix audio → ASS captions → MP4.

    ``aspect_ratio`` / ``duration_tier`` map to pixel size and global duration via ``video_config``.
    Returns absolute path to final MP4.
    """
    print("\n[RENDER_V2] START", flush=True)
    if not script or not str(script).strip():
        raise ValueError("render_video_v2: script required")
    if not voice_path or not os.path.isfile(voice_path):
        raise FileNotFoundError("render_video_v2: voice_path must be a readable file")

    if ffmpeg_path is None or ffprobe_path is None:
        from services.influencer_engine.rendering.ffmpeg_path import get_ffmpeg_exe, get_ffprobe_exe

        ffmpeg_path = ffmpeg_path or get_ffmpeg_exe()
        ffprobe_path = ffprobe_path or get_ffprobe_exe()

    vcfg = resolve_video_config(aspect_ratio, duration_tier)
    ow, oh = vcfg.width, vcfg.height
    target_sec = float(vcfg.duration_seconds)

    bg_music = (music_path or "").strip() or CINEMATIC_BG_MUSIC_PATH
    if not bg_music or not os.path.isfile(bg_music):
        bg_music = None

    imgs: list[str] = []
    for p in image_paths or []:
        ps = str(p).strip()
        if ps and not ps.startswith("http") and os.path.isfile(ps):
            imgs.append(os.path.abspath(ps))

    work = tempfile.mkdtemp(prefix=f"cinematic_v2_{job_id}_")
    temps: list[str] = []
    final: str = ""
    try:
        if output_path:
            final = os.path.abspath(output_path)
            os.makedirs(os.path.dirname(final) or ".", exist_ok=True)
        else:
            fd_f, final = tempfile.mkstemp(suffix="_cinematic_v2.mp4")
            os.close(fd_f)
            try:
                os.unlink(final)
            except OSError:
                pass

        voice_use, voice_d = _voice_timed_to_target(
            voice_path, target_sec, work, ffmpeg_path, ffprobe_path
        )

        scenes = split_script_into_scenes(script, target_duration_sec=target_sec)
        if not scenes:
            scenes = [enrich_scene({"type": "hook", "text": script.strip()[:500]})]
        try:
            scenes = enhance_scenes(scenes)
        except Exception as e:
            logger.warning("REL failed: %s", e)
        print(f"[RENDER_V2] Scenes generated: {len(scenes)}", flush=True)
        plan = generate_clip_plan(scenes, target_duration_sec=target_sec)
        print("[RENDER_V2] Clip plan created", flush=True)
        if voice_d <= 0:
            raise RuntimeError("cinematic_v2: could not read voice duration")

        _, scale_factor = _apply_per_scene_voice_sync(plan, scenes, voice_d)

        flat: list[dict] = []
        for block in plan:
            for c in block.get("clips") or []:
                if isinstance(c, dict):
                    flat.append(c)
        if not flat:
            raise RuntimeError("cinematic_v2: empty clip plan")

        t_tr = max(0.05, min(1.5, float(CINEMATIC_TRANSITION_SEC)))
        n = len(flat)

        between = compute_between_transitions(flat, seed=str(job_id))
        avg_clip_duration = float(sum(float(c.get("duration") or 0) for c in flat)) / max(len(flat), 1)
        logger.info(
            {
                "job_id": job_id,
                "hook_strength": scenes[0].get("hook_strength") if scenes else None,
                "scene_count": len(scenes),
                "avg_clip_duration": round(avg_clip_duration, 4),
                "transition_variance": len(set(between)),
                "scale_factor": round(scale_factor, 6),
            }
        )

        stock_dir = os.path.join(work, "stock_dl")
        os.makedirs(stock_dir, exist_ok=True)

        from . import clip_fetcher as _clip_fetcher

        print("[RENDER_V2] Fetching clips...", flush=True)
        clip_paths: list[str] = []
        for i, c in enumerate(flat):
            dur = max(0.5, min(20.0, float(c.get("duration") or 5.0)))
            rendered: str | None = None
            if CINEMATIC_FETCH_STOCK:
                try:
                    scene_payload = dict(c)
                    scene_payload["text"] = scene_payload.get("prompt") or scene_payload.get("text") or ""
                    if scene_payload.get("search_queries"):
                        scene_payload["keywords"] = list(scene_payload["search_queries"])
                    got = _clip_fetcher.fetch_clips_for_scene(
                        scene_payload,
                        ffprobe_path=ffprobe_path,
                        target_duration_sec=dur,
                        temp_dir=stock_dir,
                    )
                    if got and got[0].get("path"):
                        p0 = str(got[0]["path"])
                        motion = str(c.get("motion_type") or "ken_burns_center")
                        if p0.lower().endswith((".mp4", ".mov", ".webm", ".mkv")):
                            rendered = _normalize_stock_video(
                                p0, dur, i, c, work, ffmpeg_path, motion, ow, oh
                            )
                        elif p0.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
                            rendered = _normalize_stock_image(
                                p0, dur, i, c, work, ffmpeg_path, motion, ow, oh
                            )
                except Exception as ex:
                    logger.warning("cinematic_v2 stock clip fetch/normalize failed idx=%s: %s", i, ex)
            if not rendered:
                rendered = _render_one_clip(c, dur, i, work, ffmpeg_path, imgs, ow, oh)
            clip_paths.append(rendered)

        print("[RENDER_V2] Clips ready", flush=True)

        pair_secs: list[float] = []
        for i in range(1, len(flat)):
            prev = dict(flat[i - 1])
            nxt = dict(flat[i])
            if not prev.get("type") and prev.get("scene_type"):
                prev["type"] = prev["scene_type"]
            if not nxt.get("type") and nxt.get("scene_type"):
                nxt["type"] = nxt["scene_type"]
            tr = between[i - 1] if i - 1 < len(between) else get_transition_filter(prev, nxt)
            if tr == "fast_cut":
                pair_secs.append(0.0)
            else:
                pint = str(prev.get("intensity") or "medium").lower()
                nint = str(nxt.get("intensity") or "medium").lower()
                ts = t_tr
                if pint == "high" or nint == "high":
                    ts = max(0.08, min(0.85, t_tr * 0.65))
                elif pint == "low" and nint == "low":
                    ts = max(0.35, min(1.5, t_tr * 1.2))
                pair_secs.append(ts)

        clip_inputs: list[dict] = []
        for i, c in enumerate(flat):
            d = dict(c)
            d["path"] = clip_paths[i]
            clip_inputs.append(d)

        print("[RENDER_V2] Stitching video...", flush=True)
        stitched = stitch_clips(
            clip_inputs,
            voice_use,
            ffmpeg_path=ffmpeg_path,
            ffprobe_path=ffprobe_path,
            transition_sec=t_tr,
            transition="fade",
            between_transitions=between if len(between) == max(0, n - 1) else None,
            pair_transition_secs=pair_secs if len(pair_secs) == max(0, n - 1) else None,
        )
        temps.append(stitched)
        print("[RENDER_V2] Video stitched", flush=True)

        hook_end, body_end = _voice_segment_ends(scenes, voice_d)
        mh = (CINEMATIC_MUSIC_HOOK_PATH or "").strip()
        mb = (CINEMATIC_MUSIC_BODY_PATH or "").strip()
        mc = (CINEMATIC_MUSIC_CTA_PATH or "").strip()
        print("[RENDER_V2] Mixing audio...", flush=True)
        mixed = mix_audio(
            voice_use,
            bg_music or "",
            ffmpeg_path=ffmpeg_path,
            music_hook_path=mh if mh and os.path.isfile(mh) else None,
            music_body_path=mb if mb and os.path.isfile(mb) else None,
            music_cta_path=mc if mc and os.path.isfile(mc) else None,
            hook_end_sec=hook_end,
            body_end_sec=body_end,
            voice_duration_sec=voice_d,
        )
        temps.append(mixed)
        print("[RENDER_V2] Audio complete", flush=True)

        fd_m, muxed = tempfile.mkstemp(suffix="_muxed.mp4")
        os.close(fd_m)
        temps.append(muxed)
        ok_mux, err_mux = _run(
            [
                ffmpeg_path,
                "-y",
                "-i",
                stitched,
                "-i",
                mixed,
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-map",
                "0:v:0",
                "-map",
                "1:a:0",
                "-shortest",
                muxed,
            ],
            timeout=600,
        )
        if not ok_mux or not os.path.isfile(muxed):
            raise RuntimeError(f"cinematic_v2 mux failed: {err_mux[:600]}")

        caps = generate_captions(scenes)
        ass_path = os.path.join(work, "captions.ass")
        write_ass_file(ass_path, caps, voice_d, width=ow, height=oh)

        sub_path = _ffmpeg_escape_path(ass_path)
        vf_burn = f"subtitles='{sub_path}',scale={ow}:{oh},setsar=1"
        ok_burn, err_burn = _run(
            [
                ffmpeg_path,
                "-y",
                "-i",
                muxed,
                "-vf",
                vf_burn,
                "-c:a",
                "copy",
                "-movflags",
                "+faststart",
                final,
            ],
            timeout=600,
        )
        if not ok_burn or not os.path.isfile(final):
            shutil.copyfile(muxed, final)

        if not os.path.isfile(final) or os.path.getsize(final) < 500:
            raise RuntimeError(f"cinematic_v2 final failed: {err_burn[:400]}")

        final_duration = probe_duration_seconds(final, ffprobe_path)
        file_size = os.path.getsize(final)
        if final_duration < 2 or file_size < 500_000:
            logger.warning(
                "Output validation failed: duration=%.2fs size=%s bytes (job_id=%s)",
                final_duration,
                file_size,
                job_id,
            )

        print("[RENDER_V2] DONE", flush=True)
        return os.path.abspath(final)
    finally:
        try:
            shutil.rmtree(work, ignore_errors=True)
        except Exception:
            pass
        for p in temps:
            if p and (not final or p != final) and os.path.isfile(p):
                try:
                    os.unlink(p)
                except OSError:
                    pass
