"""
Final UGC URL pipeline validation — duration from audio, scene plan, static/product checks.

Raises RuntimeError with stable codes consumed by scripts/ugc_url_pipeline.py.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Final

# Aligned with phase2_generation_guard motion heuristics; composite output is softer.
STATIC_VIDEO_MEAN_MAD_MAX: Final[float] = 0.022
STATIC_VIDEO_MAX_PAIR_MAD_MAX: Final[float] = 0.045
PRODUCT_BAND_MIN_MAD: Final[float] = 0.042
TOP_BAND_FRAC: Final[float] = 0.60


@dataclass(frozen=True)
class UgcPlanScene:
    """Minimal scene row for final-plan checks (I2V strip types)."""

    type: str
    is_static: bool


def ugc_scene_plan_from_types(scene_types: list[str]) -> list[UgcPlanScene]:
    """
    Build plan rows from string types. UGC prompts always imply motion for hook
    (non-static, non product-only still).
    """
    out: list[UgcPlanScene] = []
    for key in scene_types:
        k = (key or "").strip().lower() or "demo"
        # All UGC I2V keys include handheld / motion in engine/ugc_visual_prompts.py
        is_static = False
        out.append(UgcPlanScene(type=k, is_static=is_static))
    return out


def validate_ugc_final_scene_plan(scenes: list[UgcPlanScene]) -> None:
    if not scenes:
        raise RuntimeError("NO_DEMO_IN_FINAL_PLAN")
    if not any(s.type == "demo" for s in scenes):
        raise RuntimeError("NO_DEMO_IN_FINAL_PLAN")
    # Opening: hook (attention) or demo (product-first for split visibility / NO_VISIBLE_PRODUCT_USAGE)
    if scenes[0].type not in ("hook", "demo"):
        raise RuntimeError("INVALID_FIRST_SCENE")
    if scenes[0].is_static:
        raise RuntimeError("HOOK_HAS_NO_MOTION")


def validate_cta_text(cta: str) -> None:
    """ASCII-only CTA; reject replacement-char / empty-box artifacts."""
    line = (cta or "").strip()
    if not line:
        raise RuntimeError("INVALID_CTA_TEXT")
    if "\u25a1" in line or "□" in line:
        raise RuntimeError("INVALID_CTA_TEXT")
    try:
        line.encode("ascii")
    except UnicodeEncodeError as e:
        raise RuntimeError("INVALID_CTA_TEXT") from e


def assert_cta_enabled_for_final_segment() -> None:
    dis = (os.environ.get("AFFILIATE_CTA_DISABLE") or "").strip().lower()
    if dis in ("1", "true", "yes", "on"):
        raise RuntimeError("CTA_DISABLED")


def _mean_abs_diff_grayscale(path_a: str, path_b: str) -> float | None:
    try:
        from PIL import Image

        def _gray_small(p: str) -> list[float]:
            im = Image.open(p).convert("L").resize((64, 64))
            return [px / 255.0 for px in im.getdata()]

        a = _gray_small(path_a)
        b = _gray_small(path_b)
        if len(a) != len(b):
            return None
        return sum(abs(a[i] - b[i]) for i in range(len(a))) / len(a)
    except Exception:
        return None


def _mean_abs_diff_grayscale_crop_top_band(path_a: str, path_b: str, top_frac: float = TOP_BAND_FRAC) -> float | None:
    """Compare mean abs diff on top band only (product strip in affiliate split)."""
    try:
        from PIL import Image

        def _gray_top(p: str) -> list[float]:
            im = Image.open(p).convert("L")
            w, h = im.size
            ch = max(2, int(h * max(0.2, min(0.85, top_frac))))
            im = im.crop((0, 0, w, ch)).resize((64, 64))
            return [px / 255.0 for px in im.getdata()]

        a = _gray_top(path_a)
        b = _gray_top(path_b)
        if len(a) != len(b):
            return None
        return sum(abs(a[i] - b[i]) for i in range(len(a))) / len(a)
    except Exception:
        return None


def _extract_frame(ffmpeg_path: str, video_path: str, t_sec: float, out_png: str) -> bool:
    # Seek after -i for frame-accurate decode (input seek before -i snaps to keyframes and can
    # yield identical PNGs for different t_sec, collapsing MAD for product/static checks).
    r = subprocess.run(
        [
            ffmpeg_path,
            "-y",
            "-i",
            video_path,
            "-ss",
            f"{max(0.0, t_sec):.4f}",
            "-vframes",
            "1",
            "-q:v",
            "3",
            out_png,
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    return r.returncode == 0 and os.path.isfile(out_png) and os.path.getsize(out_png) > 400


def detect_static_frames(
    output_video: str,
    ffmpeg_path: str,
    ffprobe_path: str,
    *,
    num_samples: int = 10,
) -> bool:
    """
    Return True if the video appears globally static (frozen frame), using
    consecutive sampled frame mean-abs-diff on downscaled grayscale.
    """
    if not output_video or not os.path.isfile(output_video):
        return True
    dur = _ffprobe_duration(output_video, ffprobe_path)
    if dur < 0.5:
        return True
    times = [dur * (i + 0.5) / num_samples for i in range(num_samples)]
    paths: list[str] = []
    try:
        for i, ts in enumerate(times):
            fd, p = tempfile.mkstemp(suffix=f"_sf{i}.png")
            os.close(fd)
            paths.append(p)
            if not _extract_frame(ffmpeg_path, output_video, ts, p):
                return True
        mads: list[float] = []
        for i in range(len(paths) - 1):
            mad = _mean_abs_diff_grayscale(paths[i], paths[i + 1])
            if mad is None:
                return True
            mads.append(mad)
        if not mads:
            return True
        mean_mad = sum(mads) / len(mads)
        max_mad = max(mads)
        if mean_mad < STATIC_VIDEO_MEAN_MAD_MAX and max_mad < STATIC_VIDEO_MAX_PAIR_MAD_MAX:
            return True
        return False
    finally:
        for p in paths:
            try:
                os.unlink(p)
            except OSError:
                pass


def _effective_top_band_frac() -> float:
    """Match affiliate split product pane height (``AFFILIATE_SPLIT_TOP_RATIO`` only)."""
    raw = os.environ.get("AFFILIATE_SPLIT_TOP_RATIO")
    if raw is None or str(raw).strip() == "":
        raw = "0.30"
    try:
        r = float(raw)
        return max(0.2, min(0.85, r))
    except ValueError:
        return TOP_BAND_FRAC


def detect_product_usage(output_video: str, ffmpeg_path: str, ffprobe_path: str) -> bool:
    """
    Heuristic: motion in the top product band between sampled times (hands / usage proxy).
    Returns True if usage-like motion is visible; False if the band looks frozen.
    """
    if not output_video or not os.path.isfile(output_video):
        return False
    dur = _ffprobe_duration(output_video, ffprobe_path)
    if dur < 1.0:
        return False
    band = _effective_top_band_frac()
    samples = [dur * 0.22, dur * 0.48, dur * 0.74]
    paths: list[str] = []
    try:
        for i, ts in enumerate(samples):
            fd, p = tempfile.mkstemp(suffix=f"_pu{i}.png")
            os.close(fd)
            paths.append(p)
            if not _extract_frame(ffmpeg_path, output_video, ts, p):
                return False
        m12 = _mean_abs_diff_grayscale_crop_top_band(paths[0], paths[1], band)
        m23 = _mean_abs_diff_grayscale_crop_top_band(paths[1], paths[2], band)
        m13 = _mean_abs_diff_grayscale_crop_top_band(paths[0], paths[2], band)
        if m12 is None or m23 is None or m13 is None:
            return False
        ok = max(m12, m23) >= PRODUCT_BAND_MIN_MAD
        if (os.environ.get("UGC_DEBUG_PRODUCT_MAD") or "").strip().lower() in ("1", "true", "yes", "on"):
            print(
                f"DEBUG detect_product_usage band={band:.3f} t_samples_s="
                f"[{samples[0]:.2f},{samples[1]:.2f},{samples[2]:.2f}] "
                f"MAD_pairs m12={m12:.5f} m23={m23:.5f} m13={m13:.5f} "
                f"need_max_pair>={PRODUCT_BAND_MIN_MAD} ok={ok}",
                flush=True,
            )
        return ok
    finally:
        for p in paths:
            try:
                os.unlink(p)
            except OSError:
                pass


def get_product_band_mad_scores(
    output_video: str,
    ffmpeg_path: str,
    ffprobe_path: str,
) -> tuple[float | None, float | None, float]:
    """
    Same sampling as ``detect_product_usage`` — returns (m12, m23, band_frac).
    Does not print; use for pipelines that must log MAD without enabling UGC_DEBUG_PRODUCT_MAD.
    """
    if not output_video or not os.path.isfile(output_video):
        return None, None, _effective_top_band_frac()
    dur = _ffprobe_duration(output_video, ffprobe_path)
    if dur < 1.0:
        return None, None, _effective_top_band_frac()
    band = _effective_top_band_frac()
    samples = [dur * 0.22, dur * 0.48, dur * 0.74]
    paths: list[str] = []
    try:
        for i, ts in enumerate(samples):
            fd, p = tempfile.mkstemp(suffix=f"_mad{i}.png")
            os.close(fd)
            paths.append(p)
            if not _extract_frame(ffmpeg_path, output_video, ts, p):
                return None, None, band
        m12 = _mean_abs_diff_grayscale_crop_top_band(paths[0], paths[1], band)
        m23 = _mean_abs_diff_grayscale_crop_top_band(paths[1], paths[2], band)
        return m12, m23, band
    finally:
        for p in paths:
            try:
                os.unlink(p)
            except OSError:
                pass


def _ffprobe_duration(path: str, ffprobe_path: str) -> float:
    r = subprocess.run(
        [
            ffprobe_path,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            path,
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if r.returncode != 0:
        return 0.0
    try:
        return float((r.stdout or "").strip().split()[0])
    except (ValueError, IndexError):
        return 0.0


def ffprobe_video_dimensions(path: str, ffprobe_path: str) -> tuple[int, int] | None:
    r = subprocess.run(
        [
            ffprobe_path,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "csv=p=0",
            path,
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if r.returncode != 0:
        return None
    line = (r.stdout or "").strip().splitlines()
    if not line:
        return None
    parts = line[0].split(",")
    if len(parts) < 2:
        return None
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return None


def ffprobe_has_audio(path: str, ffprobe_path: str) -> bool:
    r = subprocess.run(
        [
            ffprobe_path,
            "-v",
            "error",
            "-select_streams",
            "a",
            "-show_entries",
            "stream=codec_type",
            "-of",
            "csv=p=0",
            path,
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if r.returncode != 0:
        return False
    out = (r.stdout or "").strip().lower()
    return bool(out)


def validate_final_output_file(
    output_path: str,
    ffprobe_path: str,
    *,
    min_duration_sec: float = 45.0,
    min_size_bytes: int = 500_000,
    expect_wh: tuple[int, int] = (1080, 1920),
) -> None:
    if not output_path or not os.path.isfile(output_path):
        raise RuntimeError("FINAL_OUTPUT_MISSING")
    try:
        sz = os.path.getsize(output_path)
    except OSError as e:
        raise RuntimeError("FINAL_OUTPUT_MISSING") from e
    if sz < min_size_bytes:
        raise RuntimeError("FINAL_OUTPUT_TOO_SMALL")
    dur = _ffprobe_duration(output_path, ffprobe_path)
    if dur < min_duration_sec:
        raise RuntimeError("VIDEO_TOO_SHORT")
    if not ffprobe_has_audio(output_path, ffprobe_path):
        raise RuntimeError("FINAL_OUTPUT_NO_AUDIO")
    wh = ffprobe_video_dimensions(output_path, ffprobe_path)
    if wh != expect_wh:
        raise RuntimeError(f"FINAL_OUTPUT_BAD_RESOLUTION:{wh!r}")
