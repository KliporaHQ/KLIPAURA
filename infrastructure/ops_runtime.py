"""Non-secret runtime hints for Mission Control (ffmpeg path, API key presence)."""

from __future__ import annotations

import os
import shutil
import subprocess


def _ffmpeg_version_line() -> str | None:
    exe = shutil.which("ffmpeg")
    if not exe:
        return None
    try:
        r = subprocess.run(
            [exe, "-version"],
            capture_output=True,
            text=True,
            timeout=4,
            check=False,
        )
        line = (r.stdout or "").splitlines()[0] if r.stdout else ""
        return line.strip()[:120] if line else f"ffmpeg at {exe}"
    except Exception:
        return f"ffmpeg at {exe}"


def _ffprobe_ok() -> bool:
    return bool(shutil.which("ffprobe"))


def get_providers_snapshot() -> dict:
    """Boolean flags only — never return secret values."""
    env = os.environ
    ffmpeg_path_env = bool((env.get("FFMPEG_PATH") or "").strip())
    ffprobe_path_env = bool((env.get("FFPROBE_PATH") or "").strip())
    which_ffmpeg = shutil.which("ffmpeg")
    which_ffprobe = shutil.which("ffprobe")
    ver = _ffmpeg_version_line()
    return {
        "ffmpeg": {
            "on_path": bool(which_ffmpeg),
            "path": which_ffmpeg or "",
            "version_line": ver or "",
            "env_ffmpeg_path_set": ffmpeg_path_env,
        },
        "ffprobe": {
            "on_path": bool(which_ffprobe),
            "path": which_ffprobe or "",
            "env_ffprobe_path_set": ffprobe_path_env,
        },
        "api_keys_present": {
            "wavespeed": bool((env.get("WAVESPEED_API_KEY") or "").strip()),
            "elevenlabs": bool((env.get("ELEVENLABS_API_KEY") or "").strip()),
            "groq": bool((env.get("GROQ_API_KEY") or "").strip()),
        },
        "notes": "On Railway, ffmpeg comes from Nixpacks (not .env). Set FFMPEG_PATH only if you want a custom binary.",
    }
