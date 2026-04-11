"""Extract a single preview frame from a video file using FFmpeg (optional)."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from typing import Optional


def extract_video_thumbnail_jpeg(
    video_path: str,
    output_jpeg: str,
    *,
    seek_sec: float = 1.0,
    width: int = 320,
    ffmpeg_exe: Optional[str] = None,
) -> bool:
    """
    Grab one frame at ``seek_sec`` and scale to ``width`` px wide. Returns True on success.
    """
    if not video_path or not os.path.isfile(video_path):
        return False
    exe = ffmpeg_exe
    if not exe:
        try:
            from klipaura_core.infrastructure.ffmpeg_path import get_ffmpeg_exe

            exe = get_ffmpeg_exe()
        except Exception:
            exe = ""
    if not exe:
        exe = os.environ.get("FFMPEG_PATH") or shutil.which("ffmpeg")
    if not exe:
        return False
    os.makedirs(os.path.dirname(output_jpeg) or ".", exist_ok=True)
    cmd = [
        exe,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        str(max(0.0, seek_sec)),
        "-i",
        video_path,
        "-frames:v",
        "1",
        "-vf",
        f"scale={int(width)}:-2",
        output_jpeg,
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=60, check=False)
        return r.returncode == 0 and os.path.isfile(output_jpeg) and os.path.getsize(output_jpeg) > 100
    except Exception:
        return False


__all__ = ["extract_video_thumbnail_jpeg"]
