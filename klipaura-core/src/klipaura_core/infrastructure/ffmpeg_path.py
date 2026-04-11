"""Shim: re-export ffmpeg helpers from the real rendering module or fallback."""

from __future__ import annotations

import os
import shutil


def get_ffmpeg_exe() -> str:
    env = (os.environ.get("FFMPEG_PATH") or "").strip()
    if env and os.path.isfile(env):
        return env
    w = shutil.which("ffmpeg")
    return w or "ffmpeg"


def get_ffprobe_exe() -> str:
    env = (os.environ.get("FFPROBE_PATH") or "").strip()
    if env and os.path.isfile(env):
        return env
    w = shutil.which("ffprobe")
    return w or "ffprobe"
