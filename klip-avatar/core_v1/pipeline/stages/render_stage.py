from utils.logger import log_stage, log_error, log_structured
from utils.state import state
import subprocess
import shutil
import time
from pathlib import Path
import os
from config import OUTPUT_DIR

import pipeline.job_helpers  # noqa: F401


def _resolve_audio_path(assets: dict) -> str:
    default_rel = "audio.mp3"
    raw = assets.get("audio_path") or assets.get("voice_path", default_rel)
    audio_path = Path(raw)
    if audio_path.is_absolute():
        return str(audio_path)
    if audio_path.parts and audio_path.parts[0] == "output":
        resolved = OUTPUT_DIR.joinpath(*audio_path.parts[1:])
    else:
        resolved = OUTPUT_DIR / audio_path.name
    return str(resolved.resolve())


def _simple_ffmpeg_render(
    ffmpeg_path: str,
    img_path: str,
    audio_path: str,
    video_path: str,
    width: int,
    height: int,
) -> None:
    cmd = [
        ffmpeg_path,
        "-y",
        "-loop",
        "1",
        "-i",
        img_path,
        "-i",
        audio_path,
        "-c:v",
        "libx264",
        "-c:a",
        "aac",
        "-shortest",
        "-pix_fmt",
        "yuv420p",
        "-vf",
        f"zoompan=z='zoom+0.001':d=1:s={width}x{height},scale={width}:{height},setsar=1",
        video_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        log_structured("render", "FFmpeg failed", "error", stderr=(result.stderr or "")[:800], returncode=result.returncode)
        raise RuntimeError(f"FFmpeg failed: {(result.stderr or '')[:500]}")


def run(assets: dict) -> str:
    """Cinematic V2 render when available; otherwise simple FFmpeg image+audio."""
    log_stage("RENDER", "Starting professional video render", 70)
    start_time = time.time()

    ffmpeg_path = os.getenv("FFMPEG_PATH")
    if not ffmpeg_path:
        ffmpeg_path = shutil.which("ffmpeg")
    if not ffmpeg_path:
        raise RuntimeError("FFmpeg not found. Set FFMPEG_PATH or add to PATH.")

    log_structured(
        "render",
        "FFmpeg binary resolved",
        "info",
        ffmpeg_path=ffmpeg_path,
        source="FFMPEG_PATH" if os.getenv("FFMPEG_PATH") else "PATH",
    )

    vc = assets.get("video_config") or {}
    aspect_ratio = str(vc.get("aspect_ratio") or "9:16")
    duration_tier = str(vc.get("duration_tier") or "SHORT").upper()
    try:
        from engine.cinematic_v2.video_config import resolve_dimensions

        width, height = resolve_dimensions(aspect_ratio)
    except Exception:
        width, height = 1080, 1920

    script = str(assets.get("script") or "")
    audio_path = _resolve_audio_path(assets)
    imgs = [p for p in (assets.get("images") or []) if p and os.path.isfile(str(p))]

    output_dir = OUTPUT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)
    video_path = str(output_dir / "video.mp4")

    use_cinematic = os.getenv("CORE_V1_USE_CINEMATIC_V2", "true").lower() in ("true", "1", "yes")

    if use_cinematic and script.strip():
        try:
            from engine.cinematic_v2.renderer_v2 import render_video_v2

            out = render_video_v2(
                script=script,
                voice_path=audio_path,
                music_path=None,
                output_path=video_path,
                ffmpeg_path=ffmpeg_path,
                image_paths=imgs,
                job_id="core_v1",
                aspect_ratio=aspect_ratio,
                duration_tier=duration_tier,
            )
            duration = time.time() - start_time
            log_structured(
                "render",
                f"Cinematic V2 render OK in {duration:.1f}s",
                "info",
                path=out,
            )
            log_stage("RENDER", f"Video rendered ({os.path.getsize(out)/1024:.1f}KB) in {duration:.1f}s", 95)
            state.update(output=out, durations={"render": round(duration, 2)})
            return out
        except Exception as e:
            log_error("RENDER", f"Cinematic V2 failed, falling back: {e}")
            log_structured("render", "cinematic_v2 fallback", "warn", error=str(e)[:400])

    try:
        img_path = str(output_dir / "placeholder.jpg")
        if not os.path.exists(img_path):
            try:
                subprocess.run(
                    [
                        ffmpeg_path,
                        "-y",
                        "-f",
                        "lavfi",
                        "-i",
                        f"color=c=black:s={width}x{height}:d=5",
                        "-vf",
                        f"drawtext=text='KLIP AVATAR':fontcolor=white:fontsize=60:x=(w-text_w)/2:y=(h-text_h)/2",
                        img_path,
                    ],
                    capture_output=True,
                    timeout=5,
                )
            except Exception:
                pass

        log_structured("render", "Rendering with simple FFmpeg fallback", "info", audio_path=audio_path, path="fallback")
        log_stage("RENDER", "Executing FFmpeg composition...", 80)
        _simple_ffmpeg_render(ffmpeg_path, img_path, audio_path, video_path, width, height)

        if not os.path.exists(video_path):
            raise RuntimeError("FFmpeg reported success but output file is missing")
        if os.path.getsize(video_path) < 1000:
            raise RuntimeError("Rendered video too small — likely failed")

        duration = time.time() - start_time
        log_structured(
            "render",
            f"Video rendered successfully in {duration:.1f}s",
            "info",
            path="real",
            bytes=os.path.getsize(video_path),
        )
        log_stage("RENDER", f"Video rendered successfully ({os.path.getsize(video_path)/1024:.1f}KB) in {duration:.1f}s", 95)
        state.update(output=video_path, durations={"render": round(duration, 2)})
        return video_path

    except Exception as e:
        log_error("RENDER", f"Render failed: {str(e)}")
        log_structured("render", "Render pipeline failed — no placeholder output", "error", path="failed")
        raise
