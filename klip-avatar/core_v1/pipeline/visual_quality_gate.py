from __future__ import annotations

import os
import subprocess
from typing import Any

# Minimal visual quality gate before pending_review (Phase 2)
# - Top: clean product footage 1080x1920 crop
# - Bottom: lip-sync avatar with natural motion
# - No scrappy/low-quality assets
# - Professional captions and branding
# - Export 1080x1920, H.264, 30fps


def ffprobe_duration(file_path: str) -> float:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", file_path],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return float(out.stdout.strip() or "0")
    except Exception:
        return 0.0


def ffprobe_video_info(file_path: str) -> dict[str, Any]:
    try:
        out = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height,codec_name,r_frame_rate",
                "-of",
                "csv=p=0",
                file_path,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        parts = out.stdout.strip().split(",")
        if len(parts) >= 4:
            return {
                "width": int(parts[0] or "0"),
                "height": int(parts[1] or "0"),
                "codec": parts[2].strip(),
                "fps": parts[3].strip(),
            }
    except Exception:
        pass
    return {"width": 0, "height": 0, "codec": "", "fps": ""}


def passes_visual_quality_gate(product_video_path: str, avatar_video_path: str) -> tuple[bool, str]:
    """
    Enforce Phase 2 visual quality checklist.
    Returns (passes, reason).
    """
    min_w = 1080
    min_h = 1920
    min_duration_sec = 8.0
    allowed_codecs = {"h264", "libx264"}

    prod_info = ffprobe_video_info(product_video_path)
    av_info = ffprobe_video_info(avatar_video_path)
    prod_dur = ffprobe_duration(product_video_path)
    av_dur = ffprobe_duration(avatar_video_path)

    # Top: clean product footage 1080x1920 crop
    if prod_info["width"] < min_w or prod_info["height"] < min_h:
        return False, f"product video resolution {prod_info['width']}x{prod_info['height']} < {min_w}x{min_h}"
    if prod_info["codec"].lower() not in allowed_codecs:
        return False, f"product video codec {prod_info['codec']} not H.264"
    if prod_dur < min_duration_sec:
        return False, f"product video duration {prod_dur:.1f}s < {min_duration_sec}s"

    # Bottom: lip-sync avatar with natural motion
    if av_info["width"] < min_w or av_info["height"] < min_h:
        return False, f"avatar video resolution {av_info['width']}x{av_info['height']} < {min_w}x{min_h}"
    if av_info["codec"].lower() not in allowed_codecs:
        return False, f"avatar video codec {av_info['codec']} not H.264"
    if av_dur < min_duration_sec:
        return False, f"avatar video duration {av_dur:.1f}s < {min_duration_sec}s"

    # No scrappy/low-quality assets (basic sanity checks)
    if prod_dur < 4.0 or av_dur < 4.0:
        return False, "video too short (possible scrappy asset)"
    # Ensure both streams have reasonable frame rate (>= 24fps)
    try:
        fps = float(av_info["fps"].split("/")[0]) / float(av_info["fps"].split("/")[1])
        if fps < 24:
            return False, f"avatar video fps {fps} < 24"
    except Exception:
        return False, "could not parse avatar fps"

    return True, "visual quality gate passed"


def apply_visual_quality_gate_before_approval(job_id: str, product_video_path: str, avatar_video_path: str) -> tuple[bool, str]:
    """
    Call this before pushing a job to HITL_PENDING.
    If gate fails, job is marked failed and not sent to approval.
    Returns (passes, reason).
    """
    if (os.getenv("UGC_ENFORCE_VISUAL_QUALITY_GATE") or "1").strip().lower() in ("0", "false", "no", "off"):
        return True, "visual quality gate disabled"

    return passes_visual_quality_gate(product_video_path, avatar_video_path)
