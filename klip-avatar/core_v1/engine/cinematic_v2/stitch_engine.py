"""Concatenate scene clips with a fast concat demuxer path; align duration to voice."""

from __future__ import annotations

import hashlib
import os
import random
import subprocess
import tempfile

# Stitch path: simple concat only (no xfade / heavy filter graphs).
MAX_STITCH_DURATION_SEC = 30.0
MIN_CLIP_DURATION_SEC = 0.5

# Weighted stochastic transition pool (REL production; no uniform random)
TRANSITION_WEIGHTS: dict[str, float] = {
    "fast_cut": 0.25,
    "fade": 0.15,
    "slideleft": 0.15,
    "slideright": 0.15,
    "zoomin": 0.15,
    "whip_pan": 0.1,
    "glitch_cut": 0.1,
    "blur_snap": 0.1,
}


def adjusted_weight(name: str, weights: dict[str, float], history: list[str]) -> float:
    base = float(weights.get(name, 0.05))
    penalty = history.count(name) * 0.05
    return max(0.01, base - penalty)


def weighted_choice(pool: list[str], weights: dict[str, float], history: list[str], rng: random.Random) -> str:
    """
    Pick a transition from pool avoiding the last three picks; weighted by
    TRANSITION_WEIGHTS. Missing keys get a small default weight.
    """
    if not pool:
        return "fade"
    candidates = [p for p in pool if p not in history[-3:]]
    if not candidates:
        candidates = list(pool)

    def w(p: str) -> float:
        return adjusted_weight(p, weights, history)

    total = sum(w(p) for p in candidates)
    if total <= 0:
        return candidates[rng.randrange(len(candidates))]

    r = rng.uniform(0, total)
    upto = 0.0
    for p in candidates:
        wt = w(p)
        if upto + wt >= r:
            return p
        upto += wt
    return candidates[-1]


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


def get_transition_filter(prev_scene: dict | None, next_scene: dict | None) -> str:
    """
    Choose transition between two scenes/clips.

    Returns one of: fast_cut | fade | slideleft | slideright | zoomin
    """
    ps = prev_scene if isinstance(prev_scene, dict) else {}
    ns = next_scene if isinstance(next_scene, dict) else {}
    ptype = str(ps.get("type") or ps.get("scene_type") or "").lower()
    if ptype == "hook":
        return "fast_cut"
    pint = str(ps.get("intensity") or "medium").lower()
    nint = str(ns.get("intensity") or "medium").lower()
    nsty = str(ns.get("visual_style") or "").lower()
    seed = f"{ptype}:{ns.get('type')}:{nsty}:{pint}:{nint}"
    h = int(hashlib.md5(seed.encode(), usedforsecurity=False).hexdigest(), 16)
    if pint == "high" or nint == "high" or "fast" in nsty:
        pool = ["slideleft", "slideright", "zoomin", "fade"]
        return pool[h % len(pool)]
    if pint == "low" and nint == "low":
        return "fade"
    return "fade"


def _normalize_pair_transition(name: str) -> str:
    n = (name or "fade").lower().strip()
    if n in ("fast_cut", "none", "cut", "hard_cut", "glitch_cut"):
        return "fast_cut"
    if n in ("slideleft", "slide_left", "whip_pan"):
        return "slideleft"
    if n in ("slideright", "slide_right"):
        return "slideright"
    if n in ("zoomin", "zoom-in", "zoom_in", "blur_snap"):
        return "zoomin"
    if n in ("xfade", "fade", "smooth"):
        return "fade"
    return "fade"


def _xfade_ffmpeg_name(tr: str) -> str:
    t = _normalize_pair_transition(tr)
    if t == "fast_cut":
        return "fade"
    m = {
        "fade": "fade",
        "slideleft": "slideleft",
        "slideright": "slideright",
        "zoomin": "zoomin",
        # REL aliases (whip_pan/blur_snap/glitch_cut) normalize to slideleft/zoomin/fast_cut
    }
    return m.get(t, "fade")


def probe_duration_seconds(path: str, ffprobe_path: str) -> float:
    """Return media duration in seconds."""
    cmd = [
        ffprobe_path,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        path,
    ]
    ok, out = _run(cmd, timeout=60)
    if not ok:
        return 0.0
    try:
        return max(0.0, float((out or "").strip().splitlines()[0]))
    except (ValueError, IndexError):
        return 0.0


def _ffconcat_path_literal(path: str) -> str:
    ap = os.path.abspath(os.path.normpath(path)).replace("\\", "/")
    return ap.replace("'", "'\\''")


def _write_concat_demuxer_list(paths: list[str], durations: list[float]) -> str:
    """Write ffconcat list for deterministic concat (cut-only)."""
    fd, list_path = tempfile.mkstemp(suffix="_concat.txt", text=True)
    os.close(fd)
    try:
        with open(list_path, "w", encoding="utf-8", newline="\n") as f:
            f.write("ffconcat version 1.0\n")
            for p, d in zip(paths, durations):
                f.write(f"file '{_ffconcat_path_literal(p)}'\n")
                f.write(f"duration {d:.6f}\n")
            if paths:
                f.write(f"file '{_ffconcat_path_literal(paths[-1])}'\n")
        return list_path
    except Exception:
        try:
            os.unlink(list_path)
        except OSError:
            pass
        raise


def stitch_clips(
    clips: list,
    audio_path: str,
    *,
    ffmpeg_path: str,
    ffprobe_path: str,
    transition_sec: float = 0.45,
    transition: str = "fade",
    between_transitions: list[str] | None = None,
    pair_transition_secs: list[float] | None = None,
) -> str:
    """
    Combine video clips into one silent timeline via concat demuxer (cut-only, fast).

    clips: list of paths (str) or dicts with {"path": "..."}.
    audio_path: used to match output video duration to narration (voice primary).

    Returns path to stitched MP4 (silent video).
    """
    _ = (transition_sec, transition, between_transitions, pair_transition_secs)  # API compat; stitch uses cuts only

    paths: list[str] = []
    for c in clips or []:
        if isinstance(c, str) and c.strip():
            paths.append(c.strip())
        elif isinstance(c, dict) and c.get("path"):
            if str(c.get("source") or "").lower() == "lavfi" and str(c.get("type") or "").lower() == "color":
                assert float(c.get("duration") or 0) > MIN_CLIP_DURATION_SEC
            paths.append(str(c["path"]).strip())
    if not paths:
        raise ValueError("stitch_clips: no clip paths")
    for p in paths:
        if not os.path.isfile(p):
            raise FileNotFoundError(f"stitch_clips: missing clip {p}")

    n = len(paths)
    durs = [probe_duration_seconds(p, ffprobe_path) for p in paths]
    if any(d <= 0 for d in durs):
        raise RuntimeError("stitch_clips: could not read clip durations")
    for i, d in enumerate(durs):
        assert d > MIN_CLIP_DURATION_SEC, f"stitch_clips: clip {i} duration {d}s must be > {MIN_CLIP_DURATION_SEC}"

    total = sum(durs)
    if total > MAX_STITCH_DURATION_SEC:
        scale = MAX_STITCH_DURATION_SEC / total
        durs = [d * scale for d in durs]
        total = sum(durs)

    print(f"[STITCH] Clips: {n} | Total duration: {total:.2f}s", flush=True)

    if n == 1:
        fd0, out_path = tempfile.mkstemp(suffix="_stitched.mp4")
        os.close(fd0)
        voice_d = (
            probe_duration_seconds(audio_path, ffprobe_path)
            if audio_path and os.path.isfile(audio_path)
            else 0.0
        )
        if voice_d <= 0.05:
            ok, err = _run(
                [
                    ffmpeg_path,
                    "-y",
                    "-i",
                    paths[0],
                    "-t",
                    f"{durs[0]:.6f}",
                    "-an",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "ultrafast",
                    "-crf",
                    "28",
                    "-pix_fmt",
                    "yuv420p",
                    out_path,
                ],
                timeout=300,
            )
            if not ok:
                try:
                    os.unlink(out_path)
                except OSError:
                    pass
                raise RuntimeError(f"stitch single clip failed: {err[:400]}")
            print("[STITCH] FFmpeg complete", flush=True)
        else:
            _align_single_clip(paths[0], out_path, voice_d, durs[0], ffmpeg_path)
            print("[STITCH] FFmpeg complete", flush=True)
        return out_path

    fd, out_path = tempfile.mkstemp(suffix="_stitched.mp4")
    os.close(fd)

    list_path = _write_concat_demuxer_list(paths, durs)
    try:
        cmd = [
            ffmpeg_path,
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            list_path,
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "28",
            "-pix_fmt",
            "yuv420p",
            out_path,
        ]
        ok, err = _run(cmd, timeout=600)
        if not ok or not os.path.isfile(out_path):
            try:
                os.unlink(out_path)
            except OSError:
                pass
            raise RuntimeError(f"stitch_clips ffmpeg failed: {err[:800]}")
        print("[STITCH] FFmpeg complete", flush=True)
    finally:
        try:
            os.unlink(list_path)
        except OSError:
            pass

    voice_d = probe_duration_seconds(audio_path, ffprobe_path) if audio_path and os.path.isfile(audio_path) else 0.0
    if voice_d > 0.05:
        out_path = _align_video_to_duration(out_path, voice_d, ffmpeg_path, ffprobe_path)

    return out_path


def _align_single_clip(src: str, out: str, target_d: float, src_d: float, ffmpeg_path: str) -> None:
    if target_d <= 0 or abs(target_d - src_d) < 0.12:
        cmd = [ffmpeg_path, "-y", "-i", src, "-c", "copy", "-an", out]
        ok, err = _run(cmd, timeout=300)
        if ok and os.path.isfile(out):
            return
        raise RuntimeError(f"single clip copy failed: {err[:400]}")
    if target_d > src_d:
        pad = target_d - src_d
        cmd = [
            ffmpeg_path,
            "-y",
            "-i",
            src,
            "-vf",
            f"tpad=stop_mode=clone:stop_duration={pad:.4f}",
            "-t",
            f"{target_d:.4f}",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "28",
            "-pix_fmt",
            "yuv420p",
            out,
        ]
    else:
        cmd = [
            ffmpeg_path,
            "-y",
            "-i",
            src,
            "-t",
            f"{target_d:.4f}",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "28",
            "-pix_fmt",
            "yuv420p",
            out,
        ]
    ok, err = _run(cmd, timeout=300)
    if not ok:
        raise RuntimeError(f"single clip align failed: {err[:400]}")


def _align_video_to_duration(video_path: str, target_d: float, ffmpeg_path: str, ffprobe_path: str) -> str:
    cur = probe_duration_seconds(video_path, ffprobe_path)
    if cur <= 0 or abs(cur - target_d) < 0.12:
        return video_path
    fd, aligned = tempfile.mkstemp(suffix="_aligned.mp4")
    os.close(fd)
    try:
        if target_d > cur:
            pad = target_d - cur
            cmd = [
                ffmpeg_path,
                "-y",
                "-i",
                video_path,
                "-vf",
                f"tpad=stop_mode=clone:stop_duration={pad:.4f}",
                "-t",
                f"{target_d:.4f}",
                "-an",
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-crf",
                "28",
                "-pix_fmt",
                "yuv420p",
                aligned,
            ]
        else:
            cmd = [
                ffmpeg_path,
                "-y",
                "-i",
                video_path,
                "-t",
                f"{target_d:.4f}",
                "-an",
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-crf",
                "28",
                "-pix_fmt",
                "yuv420p",
                aligned,
            ]
        ok, err = _run(cmd, timeout=300)
        if ok and os.path.isfile(aligned):
            try:
                os.unlink(video_path)
            except OSError:
                pass
            return aligned
        raise RuntimeError(f"align video failed: {err[:400]}")
    except Exception:
        try:
            os.unlink(aligned)
        except OSError:
            pass
        raise

