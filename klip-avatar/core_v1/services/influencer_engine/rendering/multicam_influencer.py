"""
Influencer multi-cam: jump-cuts + Ken Burns (zoompan) for avatar_role=influencer.

Does not use Beebom vertical split. Full-frame 1080x1920. Three framing variants are
derived from one source clip via crop + zoompan (no extra WaveSpeed generations).

Canonical Ken Burns zoompan (subtle drift, 1080x1920):
  zoompan=z='min(zoom+0.0012,1.12)':d=900:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1080x1920:fps=30
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import tempfile
from typing import Any, Dict, List, Tuple

from .ffmpeg_uae_compliance import uae_ai_disclosure_vf_chain

log = logging.getLogger(__name__)

INFLUENCER_MULTICAM_ZOOMPAN = (
    "zoompan=z='min(zoom+0.0012,1.12)':d=900:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1080x1920:fps=30"
)

# Optional: KLIP_FFMPEG_LOG_DIR → append stderr from failed/risky ffmpeg runs (diagnostics).
def _ffmpeg_log_path() -> str:
    d = (os.environ.get("KLIP_FFMPEG_LOG_DIR") or "").strip()
    if not d:
        try:
            here = os.path.dirname(os.path.abspath(__file__))
            repo = os.path.dirname(os.path.dirname(os.path.dirname(here)))
            d = os.path.join(repo, "outputs", "logs")
        except Exception:
            d = ""
    if not d:
        return ""
    try:
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, "ffmpeg_multicam.log")
    except Exception:
        return ""


def _append_ffmpeg_log(label: str, cmd: list, stderr: str) -> None:
    path = _ffmpeg_log_path()
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8", errors="replace") as f:
            f.write(f"\n--- {label} ---\n{' '.join(cmd)}\n{stderr[:12000]}\n")
    except Exception:
        pass


def _probe_video_fps(path: str) -> float:
    """Return average frame rate for video stream, default 30."""
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
            [
                exe,
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=r_frame_rate",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                path,
            ],
            capture_output=True,
            text=True,
            timeout=20,
        )
        if r.returncode != 0:
            return 30.0
        s = (r.stdout or "").strip()
        if "/" in s:
            a, b = s.split("/", 1)
            return float(a) / max(float(b), 1e-6)
    except Exception:
        pass
    return 30.0


def _zoompan_output_frames(dur_sec: float, fps: float) -> int:
    """
    zoompan `d` = number of OUTPUT frames. Must match trimmed input length; a fixed d=900
    with a shorter lipsync clip pads with black — the classic 'black screen + watermark' failure.
    """
    if dur_sec <= 0:
        return 1
    n = int(dur_sec * fps + 0.5)
    return max(1, min(n, 86400))


def _ffmpeg_exe() -> str:
    try:
        from .ffmpeg_path import get_ffmpeg_exe

        return get_ffmpeg_exe()
    except Exception:
        return "ffmpeg"


def _ffprobe_duration(path: str) -> float:
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
            timeout=20,
        )
        if r.returncode == 0 and (r.stdout or "").strip():
            return float((r.stdout or "").strip())
    except Exception:
        pass
    return 0.0


def _silence_midpoints(audio_path: str, min_silence: float = 0.35) -> List[float]:
    exe = _ffmpeg_exe()
    mids: List[float] = []
    try:
        r = subprocess.run(
            [
                exe,
                "-hide_banner",
                "-nostats",
                "-i",
                audio_path,
                "-af",
                f"silencedetect=noise=-35dB:d={min_silence}",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        text = (r.stderr or "") + (r.stdout or "")
        starts = [float(m.group(1)) for m in re.finditer(r"silence_start:\s*([\d.]+)", text)]
        ends = [float(m.group(1)) for m in re.finditer(r"silence_end:\s*([\d.]+)", text)]
        for i, s in enumerate(starts):
            e = ends[i] if i < len(ends) else s + min_silence
            mids.append((s + e) / 2.0)
    except Exception as e:
        log.debug("silence detect failed: %s", e)
    return mids


def _variant_filter(which: int, d_frames: int) -> str:
    caps = ("1.08", "1.12", "1.18")
    rates = ("0.0008", "0.0012", "0.0016")
    cap = caps[which % 3]
    rate = rates[which % 3]
    d = max(1, int(d_frames))
    return (
        f"scale=1080:1920:force_original_aspect_ratio=increase,"
        f"crop=1080:1920,"
        f"zoompan=z='min(zoom+{rate},{cap})':d={d}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1080x1920:fps=30"
    )


def compose_influencer_multicam(
    source_video: str,
    audio_path: str,
    output_path: str,
) -> Dict[str, Any]:
    """
    Build 3 zoompan variants, cut between them at silence midpoints (jump-cuts), mux audio.

    Influencer lane is **single full-frame** lipsync (avatar *is* the picture). This is not
    B-roll under + PiP avatar; multicam only reframes that one clip (wide / medium / close).
    """
    src_abs = os.path.abspath(os.path.normpath(source_video))
    aud_abs = os.path.abspath(os.path.normpath(audio_path))
    out_abs = os.path.abspath(os.path.normpath(output_path))
    if not os.path.isfile(src_abs) or not os.path.isfile(aud_abs):
        log.warning("multicam missing_input src=%s aud=%s", src_abs, aud_abs)
        return {"ok": False, "error": "missing_input"}
    dur_v = _ffprobe_duration(src_abs)
    dur_a = _ffprobe_duration(aud_abs)
    dur = min(d for d in (dur_v, dur_a) if d and d > 0) if (dur_v > 0 and dur_a > 0) else (dur_v or dur_a or 0.0)
    if dur <= 0:
        return {"ok": False, "error": "zero_duration"}
    fps = _probe_video_fps(src_abs)
    d_frames = _zoompan_output_frames(dur, fps)
    exe = _ffmpeg_exe()
    tmpdir = tempfile.mkdtemp(prefix="multicam_")
    try:
        variants: List[str] = []
        for w in range(3):
            vp = os.path.join(tmpdir, f"v{w}.mp4")
            vf = _variant_filter(w, d_frames)
            cmd_v = [
                exe,
                "-y",
                "-i",
                src_abs,
                "-vf",
                vf,
                "-an",
                "-t",
                str(min(dur, 600)),
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                vp,
            ]
            r = subprocess.run(
                cmd_v,
                capture_output=True,
                timeout=900,
                check=False,
            )
            err = (r.stderr or b"").decode("utf-8", errors="replace") if isinstance(r.stderr, bytes) else (r.stderr or "")
            if r.returncode != 0:
                _append_ffmpeg_log(f"variant_{w}_failed", cmd_v, err)
                log.warning("multicam variant %s ffmpeg rc=%s: %s", w, r.returncode, err[:500])
            if os.path.isfile(vp) and os.path.getsize(vp) > 100:
                variants.append(vp)
        if len(variants) < 2:
            return {"ok": False, "error": "variant_build_failed"}

        mids = _silence_midpoints(aud_abs)
        cuts = [t for t in mids if 0.3 < t < dur - 0.3]
        if len(cuts) < 2:
            step = max(2.0, dur / 4.0)
            cuts = [step, step * 2, step * 3]
        cuts = sorted({round(c, 2) for c in cuts if c < dur - 0.2})
        boundaries = [0.0] + cuts + [dur]
        segment_specs: List[Tuple[int, float, float]] = []
        for i in range(len(boundaries) - 1):
            a, b = boundaries[i], boundaries[i + 1]
            if b - a < 0.15:
                continue
            segment_specs.append((i % len(variants), a, b))

        part_paths: List[str] = []
        for i, (vidx, a, b) in enumerate(segment_specs):
            src = variants[vidx % len(variants)]
            outp = os.path.join(tmpdir, f"part_{i:03d}.mp4")
            seg_cmd = [
                exe,
                "-y",
                "-ss",
                str(a),
                "-i",
                os.path.abspath(src),
                "-t",
                str(b - a),
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-an",
                outp,
            ]
            r_seg = subprocess.run(
                seg_cmd,
                capture_output=True,
                timeout=300,
                check=False,
            )
            if r_seg.returncode != 0:
                _append_ffmpeg_log(
                    "segment_cut",
                    seg_cmd,
                    (r_seg.stderr or b"").decode("utf-8", errors="replace")
                    if isinstance(r_seg.stderr, bytes)
                    else (r_seg.stderr or ""),
                )
            if os.path.isfile(outp) and os.path.getsize(outp) > 50:
                part_paths.append(outp)
        if not part_paths:
            return {"ok": False, "error": "segment_cut_failed"}

        list_path = os.path.join(tmpdir, "concat.txt")
        with open(list_path, "w", encoding="utf-8") as f:
            for p in part_paths:
                ap = os.path.abspath(p).replace("\\", "/")
                f.write(f"file '{ap}'\n")
        concat_out = os.path.join(tmpdir, "joined.mp4")
        c_cmd = [exe, "-y", "-f", "concat", "-safe", "0", "-i", list_path, "-c", "copy", concat_out]
        r_cat = subprocess.run(
            c_cmd,
            capture_output=True,
            timeout=300,
            check=False,
        )
        if r_cat.returncode != 0:
            _append_ffmpeg_log(
                "multicam_concat",
                c_cmd,
                (r_cat.stderr or b"").decode("utf-8", errors="replace")
                if isinstance(r_cat.stderr, bytes)
                else (r_cat.stderr or ""),
            )
        if not os.path.isfile(concat_out):
            return {"ok": False, "error": "concat_failed"}
        _preset = (os.environ.get("KLIP_FFMPEG_PRESET") or "slow").strip()
        _crf = (os.environ.get("KLIP_FFMPEG_CRF") or "18").strip()
        mux_cmd = [
            exe,
            "-y",
            "-i",
            os.path.abspath(concat_out),
            "-i",
            aud_abs,
            "-vf",
            uae_ai_disclosure_vf_chain(),
            "-c:v",
            "libx264",
            "-preset",
            _preset,
            "-crf",
            _crf,
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
            out_abs,
        ]
        r_mux = subprocess.run(
            mux_cmd,
            capture_output=True,
            timeout=300,
            check=False,
        )
        if r_mux.returncode != 0:
            _append_ffmpeg_log(
                "multicam_mux",
                mux_cmd,
                (r_mux.stderr or b"").decode("utf-8", errors="replace")
                if isinstance(r_mux.stderr, bytes)
                else (r_mux.stderr or ""),
            )
        ok = os.path.isfile(out_abs) and os.path.getsize(out_abs) > 100
        return {"ok": ok, "path": out_abs if ok else None, "zoompan": INFLUENCER_MULTICAM_ZOOMPAN, "d_frames": d_frames}
    finally:
        try:
            import shutil

            shutil.rmtree(tmpdir, ignore_errors=True)
        except Exception:
            pass
