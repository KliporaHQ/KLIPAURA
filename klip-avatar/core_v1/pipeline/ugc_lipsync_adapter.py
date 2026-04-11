from __future__ import annotations

import os
import time
from typing import Any

from services.influencer_engine.rendering.wavespeed_video import generate_lipsync_video_to_path


def generate_lipsync_bottom(
    avatar_paths: list[str],
    voice_path: str,
    output_path: str,
    *,
    api_key: str | None = None,
    motion_style: str = "natural_talking_head",
    job_id: str | None = None,
) -> tuple[bool, str]:
    """
    Render a single talking-head avatar video using WaveSpeed lipsync with strict lip-sync and natural motion.
    Falls back to Ken Burns slideshow if lipsync fails or API key missing.
    Returns (ok, error_message_or_path).
    """
    if not avatar_paths:
        return False, "no avatar images provided"
    if not voice_path or not os.path.isfile(voice_path):
        return False, "voice audio file missing"

    key = api_key or (os.getenv("WAVESPEED_API_KEY") or "").strip()
    if not key:
        return False, "WAVESPEED_API_KEY missing"

    img = avatar_paths[0]
    out_path = output_path or "lipsync_bottom.mp4"
    try:
        max_retries = max(0, min(5, int(os.environ.get("WAVESPEED_LIPSYNC_MAX_RETRIES", "2") or "2")))
    except ValueError:
        max_retries = 2
    last_err = "lipsync not attempted"
    for attempt in range(max_retries + 1):
        if attempt:
            delay = min(8.0, float(2 ** (attempt - 1)))
            print(f"  lipsync retry {attempt}/{max_retries} after: {last_err}; sleep {delay:.1f}s", flush=True)
            time.sleep(delay)
        path, err = generate_lipsync_video_to_path(img, voice_path, key, out_path, job_id=job_id)
        last_err = err or last_err
        if path and os.path.isfile(path):
            return True, path
    return False, last_err or "lipsync generation failed"
