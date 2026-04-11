"""
Generate .srt from narration + duration or Whisper segments; burn with FFmpeg subtitles filter.

- **Whisper (optional):** Set ``KLIP_WHISPER=1`` and install ``openai-whisper`` for word-aligned segments.
- **Burn:** Centered bold white + outline via ``subtitles`` + ``force_style`` (same visual intent as drawtext).
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import tempfile
from typing import List, Optional, Tuple

from .ffmpeg_uae_compliance import ffmpeg_filtergraph_embed_path

log = logging.getLogger(__name__)


def _ffmpeg() -> str:
    try:
        from .ffmpeg_path import get_ffmpeg_exe

        return get_ffmpeg_exe()
    except Exception:
        return "ffmpeg"


def _srt_ts(t: float) -> str:
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}".replace(".", ",")


def build_srt_from_segments(segments: List[Tuple[float, float, str]]) -> str:
    """Build SRT from (start_sec, end_sec, text) — e.g. Whisper ``segments``."""
    lines: List[str] = []
    n = 1
    for a, b, text in segments:
        t = (text or "").strip()
        if not t or b <= a:
            continue
        lines.append(str(n))
        lines.append(f"{_srt_ts(a)} --> {_srt_ts(b)}")
        lines.append(t)
        lines.append("")
        n += 1
    if not lines:
        return "1\n00:00:00,000 --> 00:00:01,000\n…\n"
    return "\n".join(lines)


def transcribe_whisper_segments_optional(audio_path: str) -> Optional[List[Tuple[float, float, str]]]:
    """
    Optional OpenAI Whisper (local). Env: ``KLIP_WHISPER=1`` (or ``true``), ``KLIP_WHISPER_MODEL`` (default tiny).
    Returns None if disabled, missing deps, or failure.
    """
    if (os.environ.get("KLIP_WHISPER") or "").strip().lower() not in ("1", "true", "yes", "on"):
        return None
    if not audio_path or not os.path.isfile(audio_path):
        return None
    try:
        import whisper  # type: ignore[import-untyped]

        model_name = (os.environ.get("KLIP_WHISPER_MODEL") or "tiny").strip()
        model = whisper.load_model(model_name)
        result = model.transcribe(audio_path, fp16=False)
        out: List[Tuple[float, float, str]] = []
        for seg in result.get("segments") or []:
            try:
                a = float(seg.get("start", 0))
                b = float(seg.get("end", 0))
                tx = str(seg.get("text") or "").strip()
                if tx and b > a:
                    out.append((a, b, tx))
            except Exception:
                continue
        return out if out else None
    except Exception as e:
        log.debug("Whisper optional transcribe skipped: %s", e)
        return None


def build_srt_simple(text: str, duration_sec: float) -> str:
    """Naive timed SRT: spread words evenly across duration."""
    words = [w for w in re.split(r"\s+", (text or "").strip()) if w]
    if not words or duration_sec <= 0:
        return "1\n00:00:00,000 --> 00:00:01,000\n…\n"
    n = max(1, len(words) // 8)
    chunks: List[str] = []
    for i in range(0, len(words), n):
        chunks.append(" ".join(words[i : i + n]))
    lines: List[str] = []
    t_per = duration_sec / len(chunks)

    for i, ch in enumerate(chunks):
        a = i * t_per
        b = min(duration_sec, (i + 1) * t_per)
        lines.append(str(i + 1))
        lines.append(f"{_srt_ts(a)} --> {_srt_ts(b)}")
        lines.append(ch)
        lines.append("")
    return "\n".join(lines)


def burn_subtitles_center(
    video_in: str,
    video_out: str,
    srt_path: str,
) -> bool:
    """Bold white text, black outline, centered (ASS-style via SRT + force_style)."""
    if not os.path.isfile(video_in) or not os.path.isfile(srt_path):
        return False
    exe = _ffmpeg()
    sp = ffmpeg_filtergraph_embed_path(srt_path)
    # Alignment=2 middle center; Bold=1; OutlineColour black
    vf = f"subtitles='{sp}':force_style='Fontsize=22,Bold=1,Outline=3,Shadow=0,Alignment=2,PrimaryColour=&HFFFFFF,OutlineColour=&H000000'"
    try:
        subprocess.run(
            [exe, "-y", "-i", video_in, "-vf", vf, "-c:a", "copy", video_out],
            capture_output=True,
            timeout=600,
            check=True,
        )
        return os.path.isfile(video_out) and os.path.getsize(video_out) > 100
    except Exception:
        return False


def write_srt_file(text: str, duration_sec: float) -> Tuple[str, str]:
    """Returns (path, content). Caller deletes path when done."""
    body = build_srt_simple(text, duration_sec)
    fd, path = tempfile.mkstemp(suffix=".srt", prefix="subs_")
    os.close(fd)
    with open(path, "w", encoding="utf-8") as f:
        f.write(body)
    return path, body


def _probe_duration(path: str) -> float:
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
            [exe, "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if r.returncode == 0 and (r.stdout or "").strip():
            return float((r.stdout or "").strip())
    except Exception:
        pass
    return 0.0


def apply_subtitle_burn_inplace(
    video_path: str,
    audio_path: Optional[str],
    narration_text: str,
) -> bool:
    """
    If ``KLIP_BURN_SUBTITLES`` is enabled, burn subtitles over ``video_path`` (overwritten on success).
    Prefers Whisper timings when ``KLIP_WHISPER=1`` and audio is available; else even word-chunk SRT from narration.
    """
    if (os.environ.get("KLIP_BURN_SUBTITLES") or "").strip().lower() not in ("1", "true", "yes", "on"):
        return False
    if not video_path or not os.path.isfile(video_path):
        return False
    dur = _probe_duration(video_path) or 30.0
    segs: Optional[List[Tuple[float, float, str]]] = None
    if audio_path and os.path.isfile(audio_path):
        segs = transcribe_whisper_segments_optional(audio_path)
    if segs:
        body = build_srt_from_segments(segs)
    else:
        body = build_srt_simple(narration_text or "", dur)
    fd, srt_path = tempfile.mkstemp(suffix=".srt", prefix="subs_")
    os.close(fd)
    fd2, out_tmp = tempfile.mkstemp(suffix="_subs.mp4", prefix="vb_")
    os.close(fd2)
    try:
        with open(srt_path, "w", encoding="utf-8") as f:
            f.write(body)
        ok = burn_subtitles_center(video_path, out_tmp, srt_path)
        if ok and os.path.isfile(out_tmp) and os.path.getsize(out_tmp) > 100:
            shutil.move(out_tmp, video_path)
            return True
    finally:
        try:
            if os.path.isfile(srt_path):
                os.unlink(srt_path)
        except Exception:
            pass
        try:
            if os.path.isfile(out_tmp):
                os.unlink(out_tmp)
        except Exception:
            pass
    return False
