"""
Resolve ffmpeg / ffprobe for KLIP-AVATAR (render worker, influencer_engine).

Resolution order (each binary):
1. FFMPEG_PATH / FFPROBE_PATH env (if set and valid)
2. On Windows only: E:\\KLIPAURA\\tools\\ffmpeg\\bin\\*.exe
3. shutil.which("ffmpeg") / shutil.which("ffprobe")
"""

from __future__ import annotations

import logging
import os
import shutil

log = logging.getLogger(__name__)

if os.name == "nt":
    DEFAULT_FFMPEG_EXE = r"E:\KLIPAURA\tools\ffmpeg\bin\ffmpeg.exe"
    DEFAULT_FFPROBE_EXE = r"E:\KLIPAURA\tools\ffmpeg\bin\ffprobe.exe"
else:
    DEFAULT_FFMPEG_EXE = ""
    DEFAULT_FFPROBE_EXE = ""

# Back-compat aliases (old name referenced in __all__ and external callers)
DEFAULT_KLIPORA_FFMPEG_EXE = DEFAULT_FFMPEG_EXE
DEFAULT_KLIPORA_FFPROBE_EXE = DEFAULT_FFPROBE_EXE

_ERR_FFMPEG = (
    'FFmpeg not found. Set FFMPEG_PATH env var or place ffmpeg.exe in E:\\KLIPAURA\\tools\\ffmpeg\\bin\\'
)
_ERR_FFPROBE = (
    'FFprobe not found. Set FFPROBE_PATH env var or place ffprobe.exe in E:\\KLIPAURA\\tools\\ffmpeg\\bin\\'
)


def get_ffmpeg_exe() -> str:
    """Return path to the ffmpeg executable."""
    p = (os.environ.get("FFMPEG_PATH") or "").strip()
    if p:
        if os.path.isfile(p):
            return p
        if os.path.isdir(p):
            exe = os.path.join(p, "ffmpeg.exe") if os.name == "nt" else os.path.join(p, "ffmpeg")
            if os.path.isfile(exe):
                return exe
    if os.path.isfile(DEFAULT_FFMPEG_EXE):
        return DEFAULT_FFMPEG_EXE
    w = shutil.which("ffmpeg")
    if w:
        return w
    log.error(_ERR_FFMPEG)
    return ""


def get_ffprobe_exe() -> str:
    """Return path to the ffprobe executable."""
    p = (os.environ.get("FFPROBE_PATH") or "").strip()
    if p:
        if os.path.isfile(p):
            return p
        if os.path.isdir(p):
            exe = os.path.join(p, "ffprobe.exe") if os.name == "nt" else os.path.join(p, "ffprobe")
            if os.path.isfile(exe):
                return exe
    if os.path.isfile(DEFAULT_FFPROBE_EXE):
        return DEFAULT_FFPROBE_EXE
    w = shutil.which("ffprobe")
    if w:
        return w
    log.error(_ERR_FFPROBE)
    return ""


def validate_ffmpeg_runtime() -> tuple[bool, str | None]:
    """Return (ok, path) if ffmpeg can be executed."""
    try:
        ffmpeg_path = get_ffmpeg_exe()
    except Exception:
        ffmpeg_path = ""
    if not ffmpeg_path:
        return False, None
    if os.path.isfile(ffmpeg_path):
        return True, ffmpeg_path
    resolved = shutil.which(ffmpeg_path)
    if resolved:
        return True, resolved
    return False, None


def log_ffmpeg_startup_status(logger: logging.Logger) -> None:
    """Log whether ffmpeg is available."""
    try:
        ok, path = validate_ffmpeg_runtime()
        if ok:
            logger.info("FFmpeg OK (render / affiliate Beebom pipeline): %s", path)
        else:
            logger.warning(_ERR_FFMPEG)
    except Exception as e:
        logger.debug("FFmpeg startup probe failed: %s", e)


__all__ = [
    "DEFAULT_FFMPEG_EXE",
    "DEFAULT_FFPROBE_EXE",
    "DEFAULT_KLIPORA_FFMPEG_EXE",  # back-compat alias
    "DEFAULT_KLIPORA_FFPROBE_EXE",  # back-compat alias
    "get_ffmpeg_exe",
    "get_ffprobe_exe",
    "log_ffmpeg_startup_status",
    "validate_ffmpeg_runtime",
]
