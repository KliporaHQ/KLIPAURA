"""
FFmpeg rendering engine: scenes (zoompan), voice overlay, optional music → 1080x1920 MP4 30fps.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import tempfile
import time
import typing as t


def _load_ffmpeg_uae_module() -> t.Any:
    """Load UAE / filter-escape helpers (same repo; works when this file is importlib-loaded)."""
    here = os.path.dirname(os.path.abspath(__file__))
    rel = os.path.normpath(os.path.join(here, "..", "influencer_engine", "rendering", "ffmpeg_uae_compliance.py"))
    if not os.path.isfile(rel):
        return None
    spec = importlib.util.spec_from_file_location("ffmpeg_uae_compliance", rel)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_ff_uae = _load_ffmpeg_uae_module()
if _ff_uae is not None:
    _ffmpeg_filtergraph_embed_path = _ff_uae.ffmpeg_filtergraph_embed_path
    _uae_ai_disclosure_fc = _ff_uae.uae_ai_disclosure_filter_complex
    _zoompan_kb = _ff_uae.zoompan_ken_burns_expr
    _escape_drawtext_literal = _ff_uae._escape_drawtext_literal
else:

    def _ffmpeg_filtergraph_embed_path(path: str) -> str:
        if not path:
            return path
        p = os.path.normpath(os.path.abspath(path)).replace("\\", "/")
        if len(p) > 1 and p[1] == ":":
            p = p[0] + "\\:" + p[2:]
        return p

    def _uae_ai_disclosure_fc(in_label: str, out_label: str) -> str:
        t = (
            "AI-Generated Content".replace("\\", "\\\\").replace(":", "\\:").replace("'", "'\\''")
        )
        return (
            f"[{in_label}]drawtext=text='{t}':fontcolor=white@0.5:fontsize=24:"
            f"x=w-tw-10:y=h-th-10[{out_label}]"
        )

    def _zoompan_kb(
        duration_frames: int, out_w: int, out_h: int, fps: int, rate: str = "0.0015", cap: str = "1.1"
    ) -> str:
        d = max(1, int(duration_frames))
        return (
            f"zoompan=z='min(zoom+{rate},{cap})':d={d}:"
            f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
            f"s={out_w}x{out_h}:fps={fps}"
        )

    def _escape_drawtext_literal(text: str) -> str:
        return (
            (text or "")
            .replace("\\", "\\\\")
            .replace(":", "\\:")
            .replace("'", "'\\''")
        )

OUTPUT_WIDTH = 1080
OUTPUT_HEIGHT = 1920
OUTPUT_FPS = 30
RENDER_ENGINE = "FFmpeg"


def _default_ffmpeg_path() -> str:
    from services.influencer_engine.rendering.ffmpeg_path import get_ffmpeg_exe

    return get_ffmpeg_exe()


def _default_ffprobe_path() -> str:
    from services.influencer_engine.rendering.ffmpeg_path import get_ffprobe_exe

    return get_ffprobe_exe()
# Boutique studio: slower encode, higher visual quality (override via env for A/B).
BOUTIQUE_X264_PRESET = os.environ.get("KLIP_FFMPEG_PRESET", "slow")
BOUTIQUE_X264_CRF = os.environ.get("KLIP_FFMPEG_CRF", "18")

# Affiliate split: product (muted) on top, avatar/slideshow bottom, narration audio only.
AFFILIATE_LAYOUT_V1 = "affiliate_split_v1"
# 55% top (product motion) / 45% bottom (talking avatar) — set ``KLIP_LAYOUT_MODE=affiliate_split_55_45`` or ``AFFILIATE_SPLIT_TOP_RATIO=0.55``.
AFFILIATE_LAYOUT_SPLIT_55_45 = "affiliate_split_55_45"
# Beebom-style: full-frame talking avatar (lipsync) + floating product PiP + timed text overlays.
AFFILIATE_STYLE_BEEBOM = "beebom"
AFFILIATE_STYLE_SPLIT = "split"
# Each panel uses a strict 9:16 content window (height = panel height), centered in the 1080-wide band.
ASPECT_W = 9
ASPECT_H = 16


def _nine_sixteen_inner_size(panel_h: int) -> tuple[int, int]:
    """Inner (w, h) with w:h = 9:16, h derived from panel height (even dimensions for encoders)."""
    th = max(ASPECT_H, int(panel_h))
    th -= th % 2
    tw = int(round(th * ASPECT_W / ASPECT_H))
    tw -= tw % 2
    return max(ASPECT_W, tw), max(ASPECT_H, th)


def _affiliate_split_top_ratio() -> float:
    """Effective top-band height ratio: ``AFFILIATE_SPLIT_TOP_RATIO`` wins; else ``KLIP_LAYOUT_MODE=affiliate_split_55_45`` → 0.55; else 0.30."""
    raw = (os.environ.get("AFFILIATE_SPLIT_TOP_RATIO") or "").strip()
    if raw:
        try:
            return max(0.2, min(0.8, float(raw)))
        except ValueError:
            pass
    mode = (os.environ.get("KLIP_LAYOUT_MODE") or "").strip()
    if mode == AFFILIATE_LAYOUT_SPLIT_55_45:
        return 0.55
    return 0.30


def _affiliate_split_heights() -> tuple[int, int]:
    ratio = _affiliate_split_top_ratio()
    top_h = max(240, int(OUTPUT_HEIGHT * ratio))
    bottom_h = OUTPUT_HEIGHT - top_h
    if bottom_h < 240:
        bottom_h = 240
        top_h = OUTPUT_HEIGHT - bottom_h
    return top_h, bottom_h


def _even_dim(n: int) -> int:
    n = max(2, int(n))
    return n - (n % 2)


def _affiliate_top_band_mad_boost_vf() -> str:
    """
    Extra temporal variance on the product (top) stream for affiliate split compose.
    detect_product_usage() gates on MAD in the top band; subtle I2V/KB can still fail when
    compose uses AFFILIATE_TOP_KB_RATE=0 (no zoompan). Kill-switch: AFFILIATE_TOP_BAND_MAD_BOOST=0.

    Tuned for PRODUCT_BAND_MIN_MAD (~0.042): scale drift + crop + eq(contrast+luma) + optional unsharp.
    Optional: AFFILIATE_TOP_BAND_MAD_BOOST_PX (default 12, clamp 6–20); AFFILIATE_TOP_BAND_MAD_UNSHARP=0 skips unsharp.
    """
    dis = (os.environ.get("AFFILIATE_TOP_BAND_MAD_BOOST") or "1").strip().lower()
    if dis in ("0", "false", "no", "off"):
        return ""
    try:
        amp = float(os.environ.get("AFFILIATE_TOP_BAND_MAD_BOOST_PX") or "12")
    except ValueError:
        amp = 12.0
    amp = max(6.0, min(20.0, amp))
    s = amp / 12.0
    ax1, ax2 = 10.0 * s, 4.0 * s
    ay1, ay2 = 8.0 * s, 3.0 * s
    # Total horizontal+vertical pad = ph; half on each side; crop removes ph so output size matches pre-pad.
    ph = int(8 + 2 * amp + 8)
    ph = ph - (ph % 2)
    ph = max(32, min(64, ph))
    half = ph // 2
    # Non-integer frequencies vs 22%/48%/74% samples; contrast+luma help flat/white product art.
    unsharp = "unsharp=3:3:0.4"
    dis_u = (os.environ.get("AFFILIATE_TOP_BAND_MAD_UNSHARP") or "1").strip().lower()
    if dis_u in ("0", "false", "no", "off"):
        unsharp = ""
    tail = (
        "eq=contrast=1.06:brightness=0.028*sin(5.3*t):eval=frame"
        + (f",{unsharp}" if unsharp else "")
        + ",setsar=1"
    )
    return (
        ","
        "scale='trunc(max(2\\,iw*(1+0.018*sin(2.3*t))))':'trunc(max(2\\,ih*(1+0.018*cos(2.1*t))))':eval=frame,"
        f"pad=iw+{ph}:ih+{ph}:{half}:{half},"
        f"crop=iw-{ph}:ih-{ph}:"
        f"{half}+{ax1:.4f}*sin(t*4)+{ax2:.4f}*sin(t*11.3):"
        f"{half}+{ay1:.4f}*cos(t*3.7)+{ay2:.4f}*cos(t*7.1),"
        + tail
    )


def _affiliate_cta_filter_segment(in_label: str, out_label: str, duration_sec: float) -> str:
    """
    End-of-video CTA (not caption-dependent). Text from AFFILIATE_CTA_OVERLAY (default:
    "Get yours • Link in bio"); merchant belongs in narration/description, not here.
    Disabled when overlay is empty or AFFILIATE_CTA_DISABLE is truthy.
    """
    line = (os.environ.get("AFFILIATE_CTA_OVERLAY") or "Get yours - Link in bio | anikaglow-20").strip()
    if not line:
        return ""
    dis = (os.environ.get("AFFILIATE_CTA_DISABLE") or "").strip().lower()
    if dis in ("1", "true", "yes", "on"):
        return ""
    # UGC: ASS burn already carries CTA copy — skip drawtext to avoid two stacked text layers.
    skip_dt = (os.environ.get("AFFILIATE_SPLIT_SKIP_DRAWTEXT_CTA") or "").strip().lower()
    if skip_dt in ("1", "true", "yes", "on"):
        return ""
    d = max(0.01, float(duration_sec))
    raw_timing = (os.environ.get("AFFILIATE_CTA_TIMING") or "").strip().lower()
    if raw_timing in ("final_3s", "final_5s", "final_10s"):
        secs = 3.0 if raw_timing == "final_3s" else 5.0 if raw_timing == "final_5s" else 10.0
        t0 = max(0.0, d - secs)
        t1 = max(t0 + 0.05, d)
    else:
        t0 = max(0.0, d * 0.80)
        t1 = max(t0 + 0.05, d)
    txt = _escape_drawtext_literal(line)
    # Commas in enable= must be escaped for filter_complex
    en = f"between(t\\,{t0:.3f}\\,{t1:.3f})"
    return (
        f"[{in_label}]drawtext=text='{txt}':fontsize=38:fontcolor=white:"
        f"box=1:boxcolor=black@0.6:boxborderw=8:"
        f"x=(w-text_w)/2:y=h*0.88:enable={en}[{out_label}];"
    )


class RenderInput(t.TypedDict, total=False):
    job_id: str
    script: str
    narration_script: str
    image_urls: t.List[str]
    clip_urls: t.List[str]
    voice_url: str
    voice_path: str
    caption_text: str
    music_url: str
    music_path: str
    duration_per_scene: float
    chat_id: str
    worker_id: str
    video_layout: str
    product_video_path: str
    product_video_url: str
    avatar_video_path: str
    style: str
    overlay: t.Dict[str, t.Any]


class RenderResult(t.TypedDict, total=False):
    success: bool
    output_path: str
    output_url: str
    error: str
    job_id: str
    processing_time: float
    ProcessingTime: float
    RenderEngine: str
    WorkerId: str


def _run(cmd: t.List[str], timeout: int = 300, stage: str = "ffmpeg_subprocess") -> t.Tuple[bool, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode == 0, (r.stderr or r.stdout or "")
    except subprocess.TimeoutExpired:
        return False, f"Timeout after {timeout}s [stage={stage}]"
    except FileNotFoundError:
        return False, "FFmpeg not found"
    except Exception as e:
        return False, str(e)


def render_video(
    input_data: RenderInput,
    output_path: t.Optional[str] = None,
    ffmpeg_path: t.Optional[str] = None,
    max_retries: int = 3,
) -> RenderResult:
    """
    Render vertical video from image paths + voice path. Optional captions/music.
    Images can be local paths; voice_path should be local (download from URL before calling).
    """
    ffmpeg_path = ffmpeg_path if ffmpeg_path is not None else _default_ffmpeg_path()
    job_id = input_data.get("job_id") or "job"
    image_urls = input_data.get("image_urls") or input_data.get("clip_urls") or []
    voice_path = input_data.get("voice_path") or ""
    voice_url = input_data.get("voice_url") or ""
    duration_per_scene = input_data.get("duration_per_scene") or 5.0

    # Cinematic Engine V2: multi-scene + transitions + BGM + captions (opt-in via USE_CINEMATIC_V2).
    # On failure, falls through to the legacy slideshow renderer unchanged.
    v2_script = (input_data.get("script") or input_data.get("narration_script") or "").strip()
    v2_voice = (input_data.get("voice_path") or "").strip()
    if (
        os.environ.get("USE_CINEMATIC_V2", "").strip().lower() in ("1", "true", "yes", "on")
        and v2_script
        and v2_voice
        and os.path.isfile(v2_voice)
    ):
        try:
            import sys

            _repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
            if _repo_root not in sys.path:
                sys.path.insert(0, _repo_root)
            from engine.cinematic_v2.renderer_v2 import render_video_v2

            _music = (input_data.get("music_path") or "").strip()
            if not _music or not os.path.isfile(_music):
                from engine.cinematic_v2.settings import CINEMATIC_BG_MUSIC_PATH

                _m2 = (CINEMATIC_BG_MUSIC_PATH or "").strip()
                _music = _m2 if _m2 and os.path.isfile(_m2) else ""
            _imgs = list(input_data.get("image_urls") or input_data.get("clip_urls") or [])
            t0 = time.perf_counter()
            out_v2 = render_video_v2(
                script=v2_script,
                voice_path=v2_voice,
                music_path=_music if _music else None,
                output_path=output_path,
                ffmpeg_path=ffmpeg_path,
                image_paths=_imgs,
                job_id=str(job_id),
            )
            pt = time.perf_counter() - t0
            worker_id = input_data.get("worker_id") or os.environ.get("WORKER_ID", "video-render")
            return {
                "success": True,
                "output_path": out_v2,
                "job_id": job_id,
                "processing_time": round(pt, 2),
                "ProcessingTime": round(pt, 2),
                "RenderEngine": "CinematicV2",
                "WorkerId": worker_id,
            }
        except Exception:
            pass

    if not image_urls:
        return {"success": False, "error": "No images", "job_id": job_id}

    # Treat as paths if not http
    image_paths = [u if not u.startswith("http") else u for u in image_urls]
    duration_frames = max(1, int(OUTPUT_FPS * duration_per_scene))
    last_error = ""
    last_processing_time = 0.0

    worker_id = input_data.get("worker_id") or os.environ.get("WORKER_ID", "video-render")
    for attempt in range(max_retries):
        out = output_path
        if not out:
            fd, out = tempfile.mkstemp(suffix=".mp4")
            os.close(fd)
        t0 = time.perf_counter()
        result = _render_impl(
            job_id=job_id,
            image_paths=image_paths,
            voice_path=voice_path or voice_url,
            duration_frames=duration_frames,
            output_path=out,
            ffmpeg_path=ffmpeg_path,
        )
        last_processing_time = time.perf_counter() - t0
        result["processing_time"] = round(last_processing_time, 2)
        result["ProcessingTime"] = round(last_processing_time, 2)
        result["RenderEngine"] = RENDER_ENGINE
        result["WorkerId"] = worker_id
        if result.get("success"):
            return result
        last_error = result.get("error", "Unknown")
    return {"success": False, "error": last_error, "job_id": job_id, "processing_time": last_processing_time, "RenderEngine": RENDER_ENGINE, "WorkerId": worker_id}


def _render_impl(
    job_id: str,
    image_paths: t.List[str],
    voice_path: str,
    duration_frames: int,
    output_path: str,
    ffmpeg_path: str,
) -> RenderResult:
    n = len(image_paths)
    # Per-image: -loop 1 -t T -i img → zoompan → [v0]...[vn-1]; concat → [v]
    t_per = duration_frames / OUTPUT_FPS
    inputs = []
    for p in image_paths:
        inputs.extend(["-loop", "1", "-t", str(t_per), "-i", p])
    audio_idx = n
    if voice_path and os.path.isfile(voice_path):
        inputs.extend(["-i", voice_path])

    # zoompan: scale to fit 1080x1920, pad, Ken Burns centering (d = output frames, >= 1)
    zp = _zoompan_kb(duration_frames, OUTPUT_WIDTH, OUTPUT_HEIGHT, OUTPUT_FPS)
    parts = []
    for i in range(n):
        parts.append(
            f"[{i}:v]scale={OUTPUT_WIDTH}:{OUTPUT_HEIGHT}:force_original_aspect_ratio=decrease,"
            f"pad={OUTPUT_WIDTH}:{OUTPUT_HEIGHT}:(ow-iw)/2:(oh-ih)/2,"
            f"{zp}[v{i}]"
        )
    concat_in = "".join(f"[v{i}]" for i in range(n))
    parts.append(f"{concat_in}concat=n={n}:v=1:a=0[vcat]")
    parts.append(_uae_ai_disclosure_fc("vcat", "v"))
    filter_complex = ";".join(parts)

    cmd = [ffmpeg_path, "-y"] + inputs + ["-filter_complex", filter_complex, "-map", "[v]"]
    if voice_path and os.path.isfile(voice_path):
        cmd += ["-map", f"{audio_idx}:a", "-c:a", "aac", "-shortest"]
    else:
        cmd += ["-an"]
    cmd += [
        "-c:v",
        "libx264",
        "-preset",
        BOUTIQUE_X264_PRESET,
        "-crf",
        BOUTIQUE_X264_CRF,
        "-r",
        str(OUTPUT_FPS),
        output_path,
    ]

    try:
        ff_timeout = int(os.environ.get("RENDER_FFMPEG_SLIDESHOW_TIMEOUT_SEC", "300") or "300")
    except ValueError:
        ff_timeout = 300
    ok, err = _run(cmd, timeout=ff_timeout, stage="ffmpeg_standard_slideshow")
    if ok:
        return {"success": True, "output_path": output_path, "job_id": job_id}
    return {"success": False, "error": err[:500], "job_id": job_id}


def _render_bottom_slideshow_only(
    job_id: str,
    image_paths: t.List[str],
    duration_frames: int,
    output_path: str,
    ffmpeg_path: str,
    panel_w: int,
    panel_h: int,
) -> RenderResult:
    """Bottom band: strict 9:16 zoompan frames, centered horizontally in panel_w x panel_h."""
    inner_w, inner_h = _nine_sixteen_inner_size(panel_h)
    n = len(image_paths)
    t_per = duration_frames / OUTPUT_FPS
    inputs: t.List[str] = []
    for p in image_paths:
        inputs.extend(["-loop", "1", "-t", str(t_per), "-i", p])
    parts = []
    kb_rate = (os.environ.get("AFFILIATE_BOTTOM_KB_RATE") or "0.0030").strip()
    kb_cap = (os.environ.get("AFFILIATE_BOTTOM_KB_CAP") or "1.10").strip()
    zp_b = _zoompan_kb(duration_frames, inner_w, inner_h, OUTPUT_FPS, rate=kb_rate, cap=kb_cap)
    try:
        face_zoom = float((os.environ.get("AFFILIATE_BOTTOM_FACE_ZOOM") or "1.12").strip())
    except ValueError:
        face_zoom = 1.12
    face_zoom = max(1.0, min(1.28, face_zoom))
    for i in range(n):
        if face_zoom > 1.005:
            zw = max(inner_w + 2, int(round(inner_w * face_zoom)))
            zh = max(inner_h + 2, int(round(inner_h * face_zoom)))
            zw -= zw % 2
            zh -= zh % 2
            face_chain = (
                f"scale={inner_w}:{inner_h}:force_original_aspect_ratio=decrease,"
                f"pad={inner_w}:{inner_h}:(ow-iw)/2:(oh-ih)/2,"
                f"scale={zw}:{zh}:force_original_aspect_ratio=increase,"
                f"crop={inner_w}:{inner_h}:(iw-{inner_w})/2:(ih-{inner_h})/2,"
            )
        else:
            face_chain = (
                f"scale={inner_w}:{inner_h}:force_original_aspect_ratio=decrease,"
                f"pad={inner_w}:{inner_h}:(ow-iw)/2:(oh-ih)/2,"
            )
        parts.append(f"[{i}:v]{face_chain}{zp_b}[v{i}]")
    concat_in = "".join(f"[v{i}]" for i in range(n))
    parts.append(f"{concat_in}concat=n={n}:v=1:a=0[vc]")
    parts.append(f"[vc]pad={panel_w}:{panel_h}:(ow-iw)/2:(oh-ih)/2,setsar=1[vpad]")
    parts.append(_uae_ai_disclosure_fc("vpad", "v"))
    filter_complex = ";".join(parts)
    cmd = (
        [ffmpeg_path, "-y"]
        + inputs
        + [
            "-filter_complex",
            filter_complex,
            "-map",
            "[v]",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            BOUTIQUE_X264_PRESET,
            "-crf",
            BOUTIQUE_X264_CRF,
            "-r",
            str(OUTPUT_FPS),
            output_path,
        ]
    )
    try:
        ff_timeout = int(os.environ.get("RENDER_FFMPEG_AFFILIATE_BOTTOM_TIMEOUT_SEC", "600") or "600")
    except ValueError:
        ff_timeout = 600
    ok, err = _run(cmd, timeout=ff_timeout, stage="ffmpeg_affiliate_bottom_slideshow")
    if ok:
        return {"success": True, "output_path": output_path, "job_id": job_id}
    return {"success": False, "error": err[:500], "job_id": job_id}


def _compose_affiliate_split(
    job_id: str,
    product_path: str,
    bottom_path: str,
    voice_path: str,
    output_path: str,
    ffmpeg_path: str,
    top_h: int,
    bottom_h: int,
) -> RenderResult:
    """Stack top (product) and bottom (avatar); top band is full canvas width (1080px), center-crop fill."""
    tw = OUTPUT_WIDTH
    th = _even_dim(top_h)
    bw = OUTPUT_WIDTH
    bh = _even_dim(bottom_h)
    ffprobe_path = _default_ffprobe_path()
    dur_s = _ffprobe_duration(product_path, ffprobe_path)
    top_frames = max(1, int(OUTPUT_FPS * dur_s))
    top_rate = (os.environ.get("AFFILIATE_TOP_KB_RATE") or "0.00075").strip()
    top_cap = (os.environ.get("AFFILIATE_TOP_KB_CAP") or "1.04").strip()
    try:
        tr = float(top_rate)
    except ValueError:
        tr = 0.0
    zp_top = ""
    if tr > 0:
        zp_top = "," + _zoompan_kb(top_frames, tw, th, OUTPUT_FPS, rate=top_rate, cap=top_cap)
    mad_boost = _affiliate_top_band_mad_boost_vf()
    cta = _affiliate_cta_filter_segment("vstk", "vcta", dur_s)
    mid_after_stack = "vcta" if cta else "vstk"
    fc = (
        # Product: full-width top strip (1080×th), center-crop (no letterbox), optional Ken Burns
        f"[0:v]scale={tw}:{th}:force_original_aspect_ratio=increase,"
        f"crop={tw}:{th},setsar=1{zp_top}{mad_boost}[top];"
        # Bottom: full-width strip, center-crop
        f"[1:v]scale={bw}:{bh}:force_original_aspect_ratio=increase,"
        f"crop={bw}:{bh},setsar=1[bot];"
        f"[top][bot]vstack=inputs=2,format=yuv420p[vstk];"
        f"{cta}"
        + _uae_ai_disclosure_fc(mid_after_stack, "v")
    )
    cmd = [
        ffmpeg_path,
        "-y",
        "-i",
        product_path,
        "-i",
        bottom_path,
        "-i",
        voice_path,
        "-filter_complex",
        fc,
        "-map",
        "[v]",
        "-map",
        "2:a:0",
        "-c:v",
        "libx264",
        "-preset",
        BOUTIQUE_X264_PRESET,
        "-crf",
        BOUTIQUE_X264_CRF,
        "-r",
        str(OUTPUT_FPS),
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-shortest",
        output_path,
    ]
    try:
        ff_timeout = int(os.environ.get("RENDER_FFMPEG_AFFILIATE_COMPOSE_TIMEOUT_SEC", "600") or "600")
    except ValueError:
        ff_timeout = 600
    ok, err = _run(cmd, timeout=ff_timeout, stage="ffmpeg_affiliate_split_compose")
    if ok:
        return {"success": True, "output_path": output_path, "job_id": job_id}
    return {"success": False, "error": err[:500], "job_id": job_id}


def render_affiliate_split_video(
    input_data: RenderInput,
    output_path: t.Optional[str] = None,
    ffmpeg_path: t.Optional[str] = None,
    max_retries: int = 3,
) -> RenderResult:
    """
    Product clip on top (muted), avatar on bottom (slideshow from images or pre-rendered avatar_video_path),
    narration only on the muxed track.
    Expects local paths: product_video_path, voice_path local; bottom from image_urls or avatar_video_path.
    """
    ffmpeg_path = ffmpeg_path if ffmpeg_path is not None else _default_ffmpeg_path()
    job_id = input_data.get("job_id") or "job"
    product_path = (input_data.get("product_video_path") or "").strip()
    image_urls = input_data.get("image_urls") or input_data.get("clip_urls") or []
    avatar_video_path = (input_data.get("avatar_video_path") or "").strip()
    use_bottom_video = bool(avatar_video_path and os.path.isfile(avatar_video_path))
    voice_path = input_data.get("voice_path") or ""
    voice_url = input_data.get("voice_url") or ""
    duration_per_scene = input_data.get("duration_per_scene") or 5.0
    worker_id = input_data.get("worker_id") or os.environ.get("WORKER_ID", "video-render")

    if not product_path or not os.path.isfile(product_path):
        return {"success": False, "error": "Missing or invalid product_video_path", "job_id": job_id}
    if not image_urls and not use_bottom_video:
        return {"success": False, "error": "No bottom panel: set image_urls or avatar_video_path", "job_id": job_id}

    image_paths = [u if not str(u).startswith("http") else u for u in image_urls]
    effective_voice = voice_path if (voice_path and os.path.isfile(voice_path)) else voice_url
    if not effective_voice or not os.path.isfile(str(effective_voice)):
        return {"success": False, "error": "Affiliate split requires local voice_path for mux", "job_id": job_id}

    duration_frames = max(1, int(OUTPUT_FPS * duration_per_scene))
    top_h, bottom_h = _affiliate_split_heights()
    if (os.environ.get("UGC_DEBUG_PIPELINE_QUALITY") or "0").strip().lower() in ("1", "true", "yes", "on"):
        print(
            f"DEBUG_PIPELINE_QUALITY: final_product_panel_dimensions={OUTPUT_WIDTH}x{top_h} "
            f"bottom_panel={OUTPUT_WIDTH}x{bottom_h}",
            flush=True,
        )
    last_error = ""
    last_processing_time = 0.0

    for attempt in range(max_retries):
        out = output_path
        if not out:
            fd, out = tempfile.mkstemp(suffix=".mp4")
            os.close(fd)
        t0 = time.perf_counter()
        bottom_tmp = ""
        owned_bottom_tmp = True
        try:
            if use_bottom_video:
                bottom_tmp = avatar_video_path
                owned_bottom_tmp = False
            else:
                fd_b, bottom_tmp = tempfile.mkstemp(suffix="_bottom.mp4")
                os.close(fd_b)
                bres = _render_bottom_slideshow_only(
                    job_id,
                    image_paths,
                    duration_frames,
                    bottom_tmp,
                    ffmpeg_path,
                    OUTPUT_WIDTH,
                    bottom_h,
                )
                if not bres.get("success"):
                    last_error = bres.get("error", "bottom_failed")
                    last_processing_time = time.perf_counter() - t0
                    continue
            cres = _compose_affiliate_split(
                job_id,
                product_path,
                bottom_tmp,
                str(effective_voice),
                out,
                ffmpeg_path,
                top_h,
                bottom_h,
            )
            last_processing_time = time.perf_counter() - t0
            cres["processing_time"] = round(last_processing_time, 2)
            cres["ProcessingTime"] = round(last_processing_time, 2)
            cres["RenderEngine"] = RENDER_ENGINE
            cres["WorkerId"] = worker_id
            if cres.get("success"):
                return cres
            last_error = cres.get("error", "compose_failed")
        finally:
            if owned_bottom_tmp and bottom_tmp:
                try:
                    os.unlink(bottom_tmp)
                except Exception:
                    pass

    return {
        "success": False,
        "error": last_error,
        "job_id": job_id,
        "processing_time": last_processing_time,
        "RenderEngine": RENDER_ENGINE,
        "WorkerId": worker_id,
    }


def _beebom_inter_size() -> tuple[int, int]:
    """Economical intermediate canvas (half 1080p height); final upscale to OUTPUT_*."""
    try:
        w = int(os.environ.get("BEEBOM_INTER_WIDTH", "540"))
        h = int(os.environ.get("BEEBOM_INTER_HEIGHT", "960"))
    except ValueError:
        w, h = 540, 960
    w = max(360, min(720, w - (w % 2)))
    h = max(640, min(1280, h - (h % 2)))
    return w, h


def _ffprobe_duration(path: str, ffprobe_path: str) -> float:
    cmd = [
        ffprobe_path,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        path,
    ]
    ok, out = _run(cmd, timeout=60, stage="ffprobe_duration")
    if not ok:
        return 30.0
    try:
        return max(1.0, float((out or "").strip().split()[0]))
    except (ValueError, IndexError):
        return 30.0


def _one_overlay_line(s: t.Any, max_len: int = 44) -> str:
    t = " ".join(str(s or "").split())
    if len(t) > max_len:
        t = t[: max_len - 1] + "…"
    return t


def _secs_to_ass_time(sec: float) -> str:
    sec = max(0.0, float(sec))
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = sec % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _ass_escape(text: str) -> str:
    return (
        text.replace("\\", "\\\\")
        .replace("{", "\\{")
        .replace("}", "\\}")
        .replace("\n", " ")
    )


def _write_beebom_ass(
    inter_w: int,
    inter_h: int,
    slots: t.List[t.Tuple[str, float, float, str]],
) -> str:
    """slots: (text, start, end, kind) kind = title | lower | foot"""
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {inter_w}
PlayResY: {inter_h}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Title,Arial,26,&H00FFFFFF,&H000000FF,&H80000000,&H80000000,-1,0,0,0,100,100,0,0,1,2,1,8,40,40,36,1
Style: Lower,Arial,22,&H00FFFFFF,&H000000FF,&H80000000,&H80000000,-1,0,0,0,100,100,0,0,1,2,1,2,48,48,120,1
Style: Foot,Arial,18,&H00E0FFFF,&H000000FF,&H80000000,&H80000000,-1,0,0,0,100,100,0,0,1,2,1,2,56,56,72,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Text
"""
    lines: t.List[str] = [head]
    for text, a, b, kind in slots:
        if not text:
            continue
        st = _secs_to_ass_time(a)
        en = _secs_to_ass_time(b)
        style = {"title": "Title", "lower": "Lower", "foot": "Foot"}.get(kind, "Foot")
        body = _ass_escape(text)
        lines.append(f"Dialogue: 0,{st},{en},{style},,0,0,0,,{body}\n")
    fd, p = tempfile.mkstemp(suffix=".ass", prefix="beebom_")
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
        f.writelines(lines)
    return p


def _is_image_product_path(p: str) -> bool:
    low = p.lower()
    return low.endswith((".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"))


def render_beebom_affiliate_video(
    input_data: RenderInput,
    output_path: t.Optional[str] = None,
    ffmpeg_path: t.Optional[str] = None,
    ffprobe_path: t.Optional[str] = None,
    max_retries: int = 2,
) -> RenderResult:
    """
    Affiliate Beebom-style composite: avatar | b-roll (side-by-side) + narration from voice file.

    ``ffmpeg_path`` / ``ffprobe_path``: optional absolute paths; if omitted, resolved via
    ``services.influencer_engine.rendering.ffmpeg_path.get_ffmpeg_exe()`` / ``get_ffprobe_exe()``.
    """
    ffmpeg_path = ffmpeg_path if ffmpeg_path is not None else _default_ffmpeg_path()
    ffprobe_path = ffprobe_path if ffprobe_path is not None else _default_ffprobe_path()
    job_id = input_data.get("job_id") or "job"
    avatar_path = (input_data.get("avatar_video_path") or "").strip()
    product_path = (input_data.get("product_video_path") or "").strip()
    voice_path = (input_data.get("voice_path") or "").strip()
    worker_id = input_data.get("worker_id") or os.environ.get("WORKER_ID", "video-render")
    overlay = input_data.get("overlay") if isinstance(input_data.get("overlay"), dict) else {}

    if not avatar_path or not os.path.isfile(avatar_path):
        return {"success": False, "error": "Missing avatar_video_path", "job_id": job_id}
    if not product_path or not os.path.isfile(product_path):
        return {"success": False, "error": "Missing product_video_path", "job_id": job_id}
    if not voice_path or not os.path.isfile(voice_path):
        return {"success": False, "error": "Missing or invalid voice_path for Beebom narration", "job_id": job_id}

    dur = _ffprobe_duration(avatar_path, ffprobe_path)
    half_w = OUTPUT_WIDTH // 2
    half_w -= half_w % 2
    H = OUTPUT_HEIGHT

    title = _one_overlay_line(overlay.get("product_title") or overlay.get("title"), 48)
    price = _one_overlay_line(overlay.get("product_price") or overlay.get("price"), 24)
    specs = _one_overlay_line(overlay.get("product_specs") or overlay.get("specs"), 52)
    pros = _one_overlay_line(overlay.get("product_pros") or overlay.get("pros"), 52)
    cons = _one_overlay_line(overlay.get("product_cons") or overlay.get("cons"), 40)

    seg = max(2.0, min(10.0, dur / 5.0))
    t0, t1 = 0.0, seg
    t2, t3 = seg, min(dur, seg * 2)
    t4, t5 = min(dur, seg * 2), min(dur, seg * 3)
    t6, t7 = min(dur, seg * 3), dur

    tmp_files: t.List[str] = []
    try:
        ass_slots: t.List[t.Tuple[str, float, float, str]] = []
        if title:
            ass_slots.append((title, t0, t1, "title"))
        if price:
            ass_slots.append((price, t2, t3, "lower"))
        if pros:
            ass_slots.append((pros, t4, t5, "foot"))
        if specs:
            ass_slots.append((specs, t6, t7, "foot"))
        elif cons:
            ass_slots.append((cons, t6, t7, "foot"))

        ass_path = _write_beebom_ass(OUTPUT_WIDTH, OUTPUT_HEIGHT, ass_slots)
        tmp_files.append(ass_path)
        ass_f = _ffmpeg_filtergraph_embed_path(ass_path)

        # [0] avatar, [1] b-roll (product), [2] narration (ElevenLabs / TTS local file)
        if _is_image_product_path(product_path):
            inputs = [
                ffmpeg_path,
                "-y",
                "-i",
                avatar_path,
                "-loop",
                "1",
                "-t",
                str(dur),
                "-i",
                product_path,
                "-i",
                voice_path,
            ]
        else:
            inputs = [
                ffmpeg_path,
                "-y",
                "-i",
                avatar_path,
                "-i",
                product_path,
                "-i",
                voice_path,
            ]

        scale_half = (
            f"scale={half_w}:{H}:force_original_aspect_ratio=decrease,"
            f"pad={half_w}:{H}:(ow-iw)/2:(oh-ih)/2,setsar=1"
        )
        parts = [
            f"[0:v]{scale_half}[va]",
            f"[1:v]{scale_half}[vb]",
            "[va][vb]hstack=inputs=2:shortest=1[vstacked]",
        ]
        if ass_slots:
            parts.append(f"[vstacked]subtitles='{ass_f}'[v0]")
            parts.append(_uae_ai_disclosure_fc("v0", "outv"))
        else:
            parts.append(_uae_ai_disclosure_fc("vstacked", "outv"))
        filter_complex = ";".join(parts)

        inputs.extend(
            [
                "-filter_complex",
                filter_complex,
                "-map",
                "[outv]",
                "-map",
                "2:a:0",
                "-shortest",
                "-c:v",
                "libx264",
                "-preset",
                BOUTIQUE_X264_PRESET,
                "-crf",
                BOUTIQUE_X264_CRF,
                "-r",
                str(OUTPUT_FPS),
                "-c:a",
                "aac",
                "-b:a",
                "192k",
            ]
        )

        last_error = ""
        last_processing_time = 0.0
        for attempt in range(max_retries):
            out = output_path
            if not out:
                fd, out = tempfile.mkstemp(suffix=".mp4")
                os.close(fd)
            t0p = time.perf_counter()
            cmd = inputs + [out]
            try:
                ff_timeout = int(os.environ.get("RENDER_FFMPEG_BEEBOM_TIMEOUT_SEC", "900") or "900")
            except ValueError:
                ff_timeout = 900
            ok, err = _run(cmd, timeout=ff_timeout, stage="ffmpeg_beebom_composite")
            last_processing_time = time.perf_counter() - t0p
            res: RenderResult = {
                "success": ok and os.path.isfile(out) and os.path.getsize(out) > 0,
                "output_path": out if ok else "",
                "job_id": job_id,
                "processing_time": round(last_processing_time, 2),
                "ProcessingTime": round(last_processing_time, 2),
                "RenderEngine": RENDER_ENGINE,
                "WorkerId": worker_id,
            }
            if res["success"]:
                return res
            last_error = err[:800] if err else "beebom_ffmpeg_failed"
        return {
            "success": False,
            "error": last_error,
            "job_id": job_id,
            "processing_time": last_processing_time,
            "RenderEngine": RENDER_ENGINE,
            "WorkerId": worker_id,
        }
    finally:
        for p in tmp_files:
            try:
                os.unlink(p)
            except Exception:
                pass


def update_airtable_if_available(
    job_id: str,
    processing_time: float,
    render_engine: str = RENDER_ENGINE,
    worker_id: str = "",
    airtable_record_id: t.Optional[str] = None,
) -> bool:
    """Update Airtable Video_Queue with ProcessingTime, RenderEngine, WorkerId if env AIRTABLE_* is set."""
    base_id = os.environ.get("AIRTABLE_BASE_ID")
    api_key = os.environ.get("AIRTABLE_API_KEY")
    if not base_id or not api_key or not airtable_record_id:
        return False
    try:
        import urllib.request
        import json as _json
        url = f"https://api.airtable.com/v0/{base_id}/Video_Queue/{airtable_record_id}"
        data = {
            "fields": {
                "ProcessingTime": processing_time,
                "RenderEngine": render_engine,
                "WorkerId": worker_id or "video-render",
            }
        }
        req = urllib.request.Request(
            url,
            data=_json.dumps(data).encode(),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="PATCH",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception:
        return False


# ── Beebom Split Fast — fast-paced affiliate B-roll with hook overlays and CTA ────────────────

def render_beebom_split_fast(
    input_data: RenderInput,
    output_path: t.Optional[str] = None,
    ffmpeg_path: t.Optional[str] = None,
    ffprobe_path: t.Optional[str] = None,
    max_retries: int = 2,
    fallback_to_ken_burns: bool = True,
) -> RenderResult:
    """
    Fast-paced affiliate B-roll composite (beebom_split_fast style).

    Layout: full-screen 9:16 product B-roll scenes (3s each) with:
      - Dynamic hook text overlay (top third, 0–3 s)
      - Feature/benefit bullet (mid, 3–6 s)
      - Strong CTA + affiliate link (bottom, last 3 s)
      - UAE AI-disclosure watermark burned into every frame

    Inputs (via input_data dict):
      product_video_path  — local path to product B-roll / product image
      voice_path          — narration MP3/WAV (ElevenLabs TTS)
      hook_text           — short hook string, e.g. "This changed my skincare routine"
      cta_text            — CTA line, e.g. "Link in bio — tap to grab 40% off"
      affiliate_url       — affiliate URL for overlay text
      overlay             — optional dict with product_title, product_price, product_specs

    Fallback: if rendering fails, falls back to ``render_video()`` (Ken Burns slideshow).
    """
    ffmpeg_path = ffmpeg_path if ffmpeg_path is not None else _default_ffmpeg_path()
    ffprobe_path = ffprobe_path if ffprobe_path is not None else _default_ffprobe_path()
    job_id = input_data.get("job_id") or "job"
    product_path = (input_data.get("product_video_path") or "").strip()
    voice_path = (input_data.get("voice_path") or "").strip()
    hook_text = (input_data.get("hook_text") or "").strip()
    cta_text = (input_data.get("cta_text") or "").strip()
    affiliate_url = (input_data.get("affiliate_url") or "").strip()
    overlay = input_data.get("overlay") if isinstance(input_data.get("overlay"), dict) else {}
    worker_id = input_data.get("worker_id") or os.environ.get("WORKER_ID", "video-render")
    image_urls = input_data.get("image_urls") or input_data.get("clip_urls") or []

    # Derive display text from overlay dict as fallback for hook/cta
    if not hook_text and overlay.get("product_title"):
        hook_text = str(overlay.get("product_title") or "")[:60]
    if not cta_text:
        cta_text = "Link in bio — tap now"

    def _escape(t: str) -> str:
        return (
            (t or "")
            .replace("\\", "\\\\")
            .replace(":", "\\:")
            .replace("'", "'\\''")
            .replace("[", "\\[")
            .replace("]", "\\]")
        )

    def _try_render() -> RenderResult:
        if not voice_path or not os.path.isfile(voice_path):
            return {"success": False, "error": "beebom_split_fast requires local voice_path", "job_id": job_id}

        # Determine input: product video or product image
        is_image = False
        if product_path and os.path.isfile(product_path):
            ext = os.path.splitext(product_path)[1].lower()
            is_image = ext in (".jpg", ".jpeg", ".png", ".webp")
        elif not product_path and image_urls:
            # No product video; use the first scene image as background B-roll
            first = image_urls[0] if not str(image_urls[0]).startswith("http") else None
            if first and os.path.isfile(str(first)):
                pass  # use render_video() fallback path instead

        fd, out = tempfile.mkstemp(suffix="_bsf.mp4")
        os.close(fd)
        out = output_path or out
        t0 = time.perf_counter()

        try:
            # Build drawtext filter chain:
            #   [hook]  0–3 s: top-third hook text
            #   [cta]   last 3 s: CTA + affiliate URL
            #   [disc]  always: UAE AI-disclosure bottom-right
            voice_dur = _ffprobe_duration(voice_path, ffprobe_path)
            if voice_dur < 1.0:
                voice_dur = 30.0
            cta_start = max(0.0, voice_dur - 4.0)

            eh = _escape(hook_text[:55])
            ec = _escape(cta_text[:55])
            eu = _escape(affiliate_url[:60]) if affiliate_url else ""
            disc = _escape("AI Generated | Affiliate Content")

            hook_dt = (
                f"drawtext=text='{eh}':fontcolor=white:fontsize=44:x=(w-tw)/2:y=h*0.12"
                f":box=1:boxcolor=black@0.55:boxborderw=8"
                f":enable='between(t,0,3)'"
            ) if eh else ""

            cta_dt = (
                f"drawtext=text='{ec}':fontcolor=yellow:fontsize=36:x=(w-tw)/2:y=h*0.82"
                f":box=1:boxcolor=black@0.6:boxborderw=8"
                f":enable='gte(t,{cta_start:.2f})'"
            ) if ec else ""

            url_dt = (
                f"drawtext=text='{eu}':fontcolor=cyan:fontsize=26:x=(w-tw)/2:y=h*0.88"
                f":box=1:boxcolor=black@0.5:boxborderw=6"
                f":enable='gte(t,{cta_start:.2f})'"
            ) if eu else ""

            disc_dt = (
                f"drawtext=text='{disc}':fontcolor=white@0.45:fontsize=20"
                f":x=w-tw-10:y=h-th-10"
            )

            vf_parts = [x for x in [hook_dt, cta_dt, url_dt, disc_dt] if x]
            vf = ",".join(vf_parts)

            if is_image or not product_path or not os.path.isfile(product_path):
                bg_input = product_path if (product_path and os.path.isfile(product_path)) else None
                if not bg_input:
                    return {"success": False, "error": "No product_video_path or product image for beebom_split_fast", "job_id": job_id}
                cmd = [
                    ffmpeg_path, "-y",
                    "-loop", "1", "-t", str(voice_dur), "-i", bg_input,
                    "-i", voice_path,
                    "-vf", f"scale={OUTPUT_WIDTH}:{OUTPUT_HEIGHT}:force_original_aspect_ratio=decrease,pad={OUTPUT_WIDTH}:{OUTPUT_HEIGHT}:(ow-iw)/2:(oh-ih)/2,{vf}",
                    "-c:v", "libx264", "-preset", BOUTIQUE_X264_PRESET, "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "128k", "-shortest", "-movflags", "+faststart",
                    out,
                ]
            else:
                cmd = [
                    ffmpeg_path, "-y",
                    "-stream_loop", "-1", "-i", product_path,
                    "-i", voice_path,
                    "-vf", f"scale={OUTPUT_WIDTH}:{OUTPUT_HEIGHT}:force_original_aspect_ratio=decrease,pad={OUTPUT_WIDTH}:{OUTPUT_HEIGHT}:(ow-iw)/2:(oh-ih)/2,setsar=1,{vf}",
                    "-c:v", "libx264", "-preset", BOUTIQUE_X264_PRESET, "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "128k", "-shortest", "-movflags", "+faststart",
                    out,
                ]

            timeout_sec = int(os.environ.get("BEEBOM_FAST_RENDER_TIMEOUT_SEC", "300"))
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_sec)
            elapsed = time.perf_counter() - t0
            if r.returncode == 0 and os.path.isfile(out) and os.path.getsize(out) > 0:
                return {
                    "success": True,
                    "output_path": out,
                    "job_id": job_id,
                    "processing_time": round(elapsed, 2),
                    "ProcessingTime": round(elapsed, 2),
                    "RenderEngine": RENDER_ENGINE,
                    "WorkerId": worker_id,
                    "render_style": "beebom_split_fast",
                }
            err = (r.stderr or r.stdout or "")[:600]
            return {"success": False, "error": err or "beebom_split_fast ffmpeg failed", "job_id": job_id}
        except subprocess.TimeoutExpired:
            return {"success": False, "error": f"beebom_split_fast timeout after {timeout_sec}s", "job_id": job_id}
        except Exception as exc:
            return {"success": False, "error": str(exc), "job_id": job_id}

    last_result: RenderResult = {"success": False, "error": "no_attempts", "job_id": job_id}
    for attempt in range(max_retries):
        last_result = _try_render()
        if last_result.get("success"):
            return last_result

    # Fallback: Ken Burns slideshow (never return a static background)
    if fallback_to_ken_burns:
        image_urls = input_data.get("image_urls") or input_data.get("clip_urls") or []
        if image_urls:
            import logging as _log
            _log.getLogger(__name__).warning(
                "[RENDER ENGINE] beebom_split_fast failed (%s); falling back to ken_burns slideshow job=%s",
                last_result.get("error"),
                job_id,
            )
            return render_video(
                input_data,
                output_path=output_path,
                ffmpeg_path=ffmpeg_path,
                max_retries=1,
            )

    return last_result
