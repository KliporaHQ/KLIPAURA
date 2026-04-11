"""Mix voice (primary) with background music: level + sidechain ducking."""

from __future__ import annotations

import os
import subprocess
import tempfile

from .settings import CINEMATIC_MUSIC_LINEAR_GAIN


def _run(cmd: list[str], timeout: int = 600) -> tuple[bool, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode == 0, (r.stderr or r.stdout or "")
    except subprocess.TimeoutExpired:
        return False, f"Timeout after {timeout}s"
    except FileNotFoundError:
        return False, "ffmpeg not found"
    except Exception as e:
        return False, str(e)


def mix_audio(
    voice_path: str,
    music_path: str,
    *,
    ffmpeg_path: str,
    music_hook_path: str | None = None,
    music_body_path: str | None = None,
    music_cta_path: str | None = None,
    hook_end_sec: float | None = None,
    body_end_sec: float | None = None,
    voice_duration_sec: float | None = None,
) -> str:
    """
    Voice = primary; music = background (~-25 dB) with ducking when voice is present.

    Optional multi-segment BGM: hook / body / CTA files with time boundaries (seconds from voice start).
    When segment paths are missing, falls back to a single music_path with dynamic volume shaping.

    Returns path to a single mixed audio track (M4A/AAC).
    If music_path is missing or invalid, returns a copy of the voice track.
    """
    if not voice_path or not os.path.isfile(voice_path):
        raise FileNotFoundError("mix_audio: voice_path must exist")
    fd, out = tempfile.mkstemp(suffix="_mixed.m4a")
    os.close(fd)

    hook_p = (music_hook_path or "").strip()
    body_p = (music_body_path or "").strip()
    cta_p = (music_cta_path or "").strip()
    has_three = (
        bool(hook_p and body_p and cta_p)
        and os.path.isfile(hook_p)
        and os.path.isfile(body_p)
        and os.path.isfile(cta_p)
    )
    has_single = bool(music_path and os.path.isfile(music_path))
    if not has_single and not has_three:
        ok, err = _run(
            [ffmpeg_path, "-y", "-i", voice_path, "-c:a", "aac", "-b:a", "192k", out],
            timeout=120,
        )
        if ok and os.path.isfile(out):
            return out
        try:
            os.unlink(out)
        except OSError:
            pass
        raise RuntimeError(f"mix_audio voice copy failed: {err[:500]}")

    g = max(0.005, min(0.2, float(CINEMATIC_MUSIC_LINEAR_GAIN)))
    vd = float(voice_duration_sec or 0.0)
    he = float(hook_end_sec) if hook_end_sec is not None else None
    be = float(body_end_sec) if body_end_sec is not None else None

    # Prefer dedicated segment files when all three exist: concat with loop+trim (each track loops if short).
    if (
        has_three
        and vd > 0.5
        and he is not None
        and be is not None
        and 0.0 < he < be < vd
    ):
        t0 = he
        t1 = be
        d_hook = max(0.15, t0)
        d_body = max(0.15, t1 - t0)
        d_cta = max(0.15, vd - t1)
        gh = g * 1.35
        gb = g * 1.0
        gc = g * 1.18
        fc = (
            f"[1:a]aloop=loop=-1:size=2e+09,atrim=0:{d_hook:.4f},asetpts=PTS-STARTPTS,aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo,volume={gh:.6f}[h];"
            f"[2:a]aloop=loop=-1:size=2e+09,atrim=0:{d_body:.4f},asetpts=PTS-STARTPTS,aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo,volume={gb:.6f}[b];"
            f"[3:a]aloop=loop=-1:size=2e+09,atrim=0:{d_cta:.4f},asetpts=PTS-STARTPTS,aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo,volume={gc:.6f}[c];"
            f"[h][b][c]concat=n=3:v=0:a=1[bgseq];"
            f"[bgseq]apad=pad_dur=2,atrim=0:{vd:.4f},asetpts=PTS-STARTPTS[bg];"
            f"[bg][0:a]sidechaincompress=threshold=0.04:ratio=3:attack=10:release=250[duck];"
            f"[0:a][duck]amix=inputs=2:duration=first:dropout_transition=2:normalize=0[aout]"
        )
        cmd_m = [
            ffmpeg_path,
            "-y",
            "-i",
            voice_path,
            "-i",
            hook_p,
            "-i",
            body_p,
            "-i",
            cta_p,
            "-filter_complex",
            fc,
            "-map",
            "[aout]",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            out,
        ]
        ok_m, err_m = _run(cmd_m, timeout=300)
        if ok_m and os.path.isfile(out) and os.path.getsize(out) > 100:
            return out
        try:
            os.unlink(out)
        except OSError:
            pass
        fd, out = tempfile.mkstemp(suffix="_mixed.m4a")
        os.close(fd)

    if not has_single:
        ok, err = _run(
            [ffmpeg_path, "-y", "-i", voice_path, "-c:a", "aac", "-b:a", "192k", out],
            timeout=120,
        )
        if ok and os.path.isfile(out):
            return out
        try:
            os.unlink(out)
        except OSError:
            pass
        raise RuntimeError("mix_audio: no valid music_path after segment attempt")

    # Single BGM: optional time-based volume (hook louder, body balanced, CTA uplift)
    if vd > 0.5 and he is not None and be is not None and 0.0 < he < be < vd:
        t0 = he
        t1 = be
        vol_expr = (
            f"if(lt(t\\,{t0:.4f})\\,{g * 1.28:.8f}\\,if(lt(t\\,{t1:.4f})\\,{g:.8f}\\,{g * 1.15:.8f})"
        )
        fc = (
            f"[1:a]volume={vol_expr}:eval=frame,aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo[bg];"
            f"[bg][0:a]sidechaincompress=threshold=0.04:ratio=3:attack=10:release=250[duck];"
            f"[0:a][duck]amix=inputs=2:duration=first:dropout_transition=2:normalize=0[aout]"
        )
    else:
        # Music ducked by voice via sidechaincompress; then sum with full voice.
        fc = (
            f"[1:a]volume={g:.6f},aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo[bg];"
            f"[bg][0:a]sidechaincompress=threshold=0.04:ratio=3:attack=10:release=250[duck];"
            f"[0:a][duck]amix=inputs=2:duration=first:dropout_transition=2:normalize=0[aout]"
        )
    cmd = [
        ffmpeg_path,
        "-y",
        "-i",
        voice_path,
        "-i",
        music_path,
        "-filter_complex",
        fc,
        "-map",
        "[aout]",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        out,
    ]
    ok, err = _run(cmd, timeout=300)
    if ok and os.path.isfile(out) and os.path.getsize(out) > 100:
        return out
    try:
        os.unlink(out)
    except OSError:
        pass
    # Fallback: simple amix without ducking
    fd2, out2 = tempfile.mkstemp(suffix="_mixed_fb.m4a")
    os.close(fd2)
    g2 = g
    fc2 = (
        f"[1:a]volume={g2:.6f}[m];"
        f"[0:a][m]amix=inputs=2:duration=first:dropout_transition=2:normalize=0[aout]"
    )
    cmd2 = [
        ffmpeg_path,
        "-y",
        "-i",
        voice_path,
        "-i",
        music_path,
        "-filter_complex",
        fc2,
        "-map",
        "[aout]",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        out2,
    ]
    ok2, err2 = _run(cmd2, timeout=300)
    if ok2 and os.path.isfile(out2):
        return out2
    try:
        os.unlink(out2)
    except OSError:
        pass
    raise RuntimeError(f"mix_audio failed: {err[:400]} fallback: {err2[:400]}")
