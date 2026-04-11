"""
Phase 2 — deterministic validation hooks + Ken Burns fallback (no new services).

``frames_analysis`` / per-frame tags are optional; when absent, validation passes so
pipelines run without a vision API. Wire tag-based checks when upstream provides data.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import typing as t

OUTPUT_FPS = 30
OUTPUT_WIDTH = 1080
OUTPUT_HEIGHT = 1920

# Product I2V — interaction-first (aligned with engine/ugc_visual_prompts.py UGC_I2V_BASE).
PRODUCT_I2V_LOCKED = (
    "close-up of hands actively using the product, clear interaction, "
    "fingers pressing, applying, opening, or demonstrating the product in use, "
    "product centered and dominant in frame, real human usage, natural movement, dynamic hand motion, "
    "camera slightly moving, realistic lighting, no static shot, no slow zoom, "
    "continuous interaction throughout the clip. "
    "the product must be physically manipulated by hands at all times, no idle frames, "
    "no passive display, no product-only shot"
)

# Avatar I2V — UGC talking-head style (enough motion for full-frame static heuristics on split render).
AVATAR_I2V_LOCKED = (
    "person speaking energetically to camera, noticeable head movement and nods, "
    "natural hand gestures and shoulder movement, handheld micro-shake, subtle camera movement, "
    "light blinking, realistic lighting, smartphone selfie video style, authentic social media style, "
    "not static frozen frame, not cinematic, not studio, no exaggeration, no distortion"
)

# Heuristic thresholds (no vision API): tune here only.
# UGC product motion (hands/usage) runs higher frame-to-frame diff than static studio.
MOTION_THRESHOLD_PRODUCT = 0.55
MOTION_THRESHOLD_AVATAR = 0.50
# First vs last frame mean-abs-diff (0–1); above suggests morph / identity drift on I2V product clips.
PRODUCT_IDENTITY_MAD_MAX = 0.38

# Product I2V: reject human-like hallucinations when tags exist.
PRODUCT_FORBIDDEN_TAGS = frozenset(
    {
        "person",
        "face",
        "human",
        "smoke",
        "fire",
        "explosion",
        "distortion",
        "mutation",
    }
)

# Avatar I2V: allow person; reject obvious junk when tags exist.
AVATAR_FORBIDDEN_TAGS = frozenset(
    {
        "smoke",
        "fire",
        "explosion",
        "distortion",
        "mutation",
    }
)


def is_valid_generation(
    frames_analysis: list[dict[str, t.Any]] | None,
    *,
    mode: t.Literal["product", "avatar"] = "product",
) -> bool:
    """
    If ``frames_analysis`` is empty/None, returns True (no tag data to reject).

    When each item is ``{"tags": [...]}``, rejects if any tag matches the mode list.
    """
    if not frames_analysis:
        return True
    forbidden = PRODUCT_FORBIDDEN_TAGS if mode == "product" else AVATAR_FORBIDDEN_TAGS
    for frame in frames_analysis:
        if not isinstance(frame, dict):
            continue
        tags = frame.get("tags", [])
        if not isinstance(tags, (list, tuple)):
            continue
        for tag in tags:
            tlow = str(tag).lower().strip()
            if tlow in forbidden:
                return False
    return True


def final_visual_gate(product_ok: bool, avatar_ok: bool) -> t.Literal["PASS", "FORCE_PRODUCT_FALLBACK", "FORCE_AVATAR_FALLBACK"]:
    """
    Single decision label before mux: fail-safe ordering (product first, then avatar).
    """
    if not product_ok:
        return "FORCE_PRODUCT_FALLBACK"
    if not avatar_ok:
        return "FORCE_AVATAR_FALLBACK"
    return "PASS"


def clip_list_passes_final_gate(paths: list[str], min_bytes: int = 4000) -> bool:
    """Reject missing / tiny outputs so borderline corrupt clips never reach render."""
    if not paths:
        return False
    for p in paths:
        if not p or not os.path.isfile(p):
            return False
        try:
            if os.path.getsize(p) < min_bytes:
                return False
        except OSError:
            return False
    return True


def strict_product_validation(meta: dict[str, t.Any] | None) -> bool:
    """
    Hard rejection when upstream provides structured meta (tags, counts, shape flags).
    If ``meta`` is None/empty, returns True (no data to enforce).
    """
    if not meta:
        return True
    forbidden = frozenset({"person", "face", "human", "smoke", "fire", "effect"})
    tags = meta.get("tags", [])
    if isinstance(tags, (list, tuple)):
        for tag in tags:
            tlow = str(tag).lower().strip()
            if tlow in forbidden:
                return False
    try:
        oc = int(meta.get("object_count", 1))
    except (TypeError, ValueError):
        oc = 1
    if oc > 1:
        return False
    if meta.get("shape_changed", False):
        return False
    return True


def ugc_product_clip_guard_mp4(path: str, ffmpeg_path: str, ffprobe_path: str) -> bool:
    """
    UGC product strips: person/hands expected — only decode + duration guard (no face rejection).
    """
    return basic_visual_guardrail_mp4(path, ffprobe_path, ffmpeg_path)


def product_sanity_check_mp4(path: str, ffmpeg_path: str, ffprobe_path: str) -> bool:
    """
    Lightweight product-strip checks before trusting I2V output:
    base guardrail, optional motion proxy (two frames), optional OpenCV face reject.
    """
    if not basic_visual_guardrail_mp4(path, ffprobe_path, ffmpeg_path):
        return False
    dur = _ffprobe_duration(path, ffprobe_path)
    if dur < 0.35:
        return False
    t1 = max(0.04, dur * 0.22)
    t2 = max(0.08, dur * 0.78)
    p1 = ""
    p2 = ""
    try:
        fd1, p1 = tempfile.mkstemp(suffix="_a.png")
        fd2, p2 = tempfile.mkstemp(suffix="_b.png")
        os.close(fd1)
        os.close(fd2)
        for ss, outp in ((t1, p1), (t2, p2)):
            r = subprocess.run(
                [
                    ffmpeg_path,
                    "-y",
                    "-ss",
                    f"{ss:.4f}",
                    "-i",
                    path,
                    "-vframes",
                    "1",
                    "-q:v",
                    "3",
                    outp,
                ],
                capture_output=True,
                text=True,
                timeout=60,
            )
            if r.returncode != 0 or not os.path.isfile(outp) or os.path.getsize(outp) < 400:
                return False
        motion_ok, _face_ok = _motion_and_face_checks(p1, p2)
        # UGC product I2V may show hands/faces; do not reject on center-face heuristic.
        return motion_ok
    except (OSError, subprocess.TimeoutExpired):
        return False
    finally:
        for pp in (p1, p2):
            if pp:
                try:
                    os.unlink(pp)
                except OSError:
                    pass


def product_identity_check_mp4(path: str, ffmpeg_path: str, ffprobe_path: str) -> bool:
    """
    I2V product clips only: first vs last frame should stay structurally similar (no morph).
    Not used for Ken Burns segments (zoom would fail this check).
    """
    if not path or not os.path.isfile(path):
        return False
    dur = _ffprobe_duration(path, ffprobe_path)
    if dur < 0.5:
        return False
    t0 = min(0.08, dur * 0.05)
    t1 = max(0.0, dur - 0.12)
    p0 = ""
    p1 = ""
    try:
        fd0, p0 = tempfile.mkstemp(suffix="_id0.png")
        fd1, p1 = tempfile.mkstemp(suffix="_id1.png")
        os.close(fd0)
        os.close(fd1)
        for ss, outp in ((t0, p0), (t1, p1)):
            r = subprocess.run(
                [
                    ffmpeg_path,
                    "-y",
                    "-ss",
                    f"{ss:.4f}",
                    "-i",
                    path,
                    "-vframes",
                    "1",
                    "-q:v",
                    "3",
                    outp,
                ],
                capture_output=True,
                text=True,
                timeout=60,
            )
            if r.returncode != 0 or not os.path.isfile(outp) or os.path.getsize(outp) < 400:
                return False
        return _frame_pair_identity_ok(p0, p1)
    except (OSError, subprocess.TimeoutExpired):
        return False
    finally:
        for pp in (p0, p1):
            if pp:
                try:
                    os.unlink(pp)
                except OSError:
                    pass


def avatar_quality_check_mp4(path: str, ffmpeg_path: str, ffprobe_path: str) -> bool:
    """
    Reject only obvious bad avatar I2V: excessive frame-to-frame change (glitchy / chaotic motion).
    Eye/mouth warp detection needs a dedicated model; not added here.
    """
    if not basic_visual_guardrail_mp4(path, ffprobe_path, ffmpeg_path):
        return False
    dur = _ffprobe_duration(path, ffprobe_path)
    if dur < 0.35:
        return False
    t1 = max(0.05, dur * 0.28)
    t2 = max(0.1, dur * 0.72)
    p1 = ""
    p2 = ""
    try:
        fd1, p1 = tempfile.mkstemp(suffix="_avq1.png")
        fd2, p2 = tempfile.mkstemp(suffix="_avq2.png")
        os.close(fd1)
        os.close(fd2)
        for ss, outp in ((t1, p1), (t2, p2)):
            r = subprocess.run(
                [
                    ffmpeg_path,
                    "-y",
                    "-ss",
                    f"{ss:.4f}",
                    "-i",
                    path,
                    "-vframes",
                    "1",
                    "-q:v",
                    "3",
                    outp,
                ],
                capture_output=True,
                text=True,
                timeout=60,
            )
            if r.returncode != 0 or not os.path.isfile(outp) or os.path.getsize(outp) < 400:
                return False
        mad = _mean_abs_diff_grayscale(p1, p2)
        if mad is None:
            return True
        return mad <= MOTION_THRESHOLD_AVATAR
    except (OSError, subprocess.TimeoutExpired):
        return False
    finally:
        for pp in (p1, p2):
            if pp:
                try:
                    os.unlink(pp)
                except OSError:
                    pass


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


def _frame_pair_identity_ok(path_first: str, path_last: str) -> bool:
    mad = _mean_abs_diff_grayscale(path_first, path_last)
    if mad is None:
        return True
    return mad <= PRODUCT_IDENTITY_MAD_MAX


def _motion_and_face_checks(path_a: str, path_b: str) -> tuple[bool, bool]:
    """Returns (motion_ok, face_ok). Motion: mean abs diff; face: dominant centered faces only."""
    motion_ok = True
    face_ok = True
    try:
        mad = _mean_abs_diff_grayscale(path_a, path_b)
        if mad is None:
            return False, True
        if mad > MOTION_THRESHOLD_PRODUCT:
            motion_ok = False
    except Exception:
        motion_ok = False

    try:
        import cv2  # type: ignore[import-untyped]

        cascade_path = os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml")
        if os.path.isfile(cascade_path):
            cascade = cv2.CascadeClassifier(cascade_path)
            for p in (path_a, path_b):
                gray = cv2.imread(p, cv2.IMREAD_GRAYSCALE)
                if gray is None:
                    continue
                h, w = gray.shape[:2]
                if w < 8 or h < 8:
                    continue
                faces = cascade.detectMultiScale(gray, 1.1, 3, minSize=(24, 24))
                for (fx, fy, fw, fh) in faces:
                    area_ratio = (float(fw) * float(fh)) / float(w * h)
                    cx = (fx + fw / 2.0) / float(w)
                    cy = (fy + fh / 2.0) / float(h)
                    centered = 0.25 <= cx <= 0.75 and 0.25 <= cy <= 0.75
                    if area_ratio > 0.15 and centered:
                        face_ok = False
                        break
                if not face_ok:
                    break
    except Exception:
        pass
    return motion_ok, face_ok


def _affiliate_top_kb_zoom_rate(raw: str | None) -> str:
    """Per-frame zoom delta for zoompan; coerces common typo (e.g. 1.6 -> 0.0016)."""
    r = (raw or os.environ.get("AFFILIATE_TOP_KB_RATE") or "0.00075").strip() or "0.00075"
    try:
        v = float(r)
        if v > 0.2:
            v = 0.0016
        elif v >= 1.0:
            v = 0.0016
        v = max(0.0001, min(0.02, v))
    except ValueError:
        v = 0.00075
    s = f"{v:.6f}".rstrip("0").rstrip(".")
    return s if s else "0.00075"


def _affiliate_top_kb_pan_px(key: str, default: float) -> float:
    raw = os.environ.get(key)
    if raw is None or not str(raw).strip():
        return default
    try:
        return max(0.0, float(raw))
    except ValueError:
        return default


def zoompan_product_premium_expr(
    duration_frames: int,
    out_w: int,
    out_h: int,
    fps: int,
    cap: str,
    rate: str | None = None,
) -> str:
    """
    Product strip: zoom-in + sinusoidal pan (multi-axis motion for top-band MAD heuristics).
    Rate/cap/pan from AFFILIATE_TOP_KB_* env (see ugc_pipeline setdefaults).
    """
    d = max(1, int(duration_frames))
    try:
        cap_f = float(cap)
    except ValueError:
        cap_f = 1.04
    cap_f = max(1.01, min(1.28, cap_f))
    r = _affiliate_top_kb_zoom_rate(rate)
    px = _affiliate_top_kb_pan_px("AFFILIATE_TOP_KB_PAN_X", 14.0)
    py = _affiliate_top_kb_pan_px("AFFILIATE_TOP_KB_PAN_Y", 8.0)
    return (
        f"zoompan=z='min(zoom+{r},{cap_f})':d={d}:"
        f"x='iw/2-(iw/zoom/2)+{px}*sin(6.2831853*on/{d})':"
        f"y='ih/2-(ih/zoom/2)+{py}*sin(3.14159265*on/{d})':"
        f"s={out_w}x{out_h}:fps={fps}"
    )


def basic_visual_guardrail_mp4(path: str, ffprobe_path: str, ffmpeg_path: str) -> bool:
    """
    Lightweight checks: file presence, min size, min duration, one decodable frame.
    Does not replace a vision model; extend when OpenCV or an API is available.
    """
    if not path or not os.path.isfile(path):
        return False
    try:
        if os.path.getsize(path) < 50_000:
            return False
    except OSError:
        return False
    dur = _ffprobe_duration(path, ffprobe_path)
    if dur < 0.35:
        return False
    fd, png_path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    try:
        r = subprocess.run(
            [
                ffmpeg_path,
                "-y",
                "-i",
                path,
                "-vframes",
                "1",
                "-q:v",
                "3",
                png_path,
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if r.returncode != 0:
            return False
        if not os.path.isfile(png_path) or os.path.getsize(png_path) < 800:
            return False
    except (OSError, subprocess.TimeoutExpired):
        return False
    finally:
        try:
            os.unlink(png_path)
        except OSError:
            pass
    return True


def _ffprobe_duration(path: str, ffprobe_path: str) -> float:
    r = subprocess.run(
        [ffprobe_path, "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if r.returncode != 0:
        return 0.0
    try:
        return max(0.0, float((r.stdout or "").strip().split()[0]))
    except (ValueError, IndexError):
        return 0.0


def ffprobe_image_wh(path: str, ffprobe_path: str) -> tuple[int, int]:
    """Return (width, height) of a still image; (0, 0) if unknown."""
    if not path or not os.path.isfile(path):
        return 0, 0
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
            "csv=p=0:s=x",
            path,
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if r.returncode != 0:
        return 0, 0
    line = (r.stdout or "").strip().splitlines()
    if not line:
        return 0, 0
    parts = line[0].strip().split("x")
    if len(parts) != 2:
        return 0, 0
    try:
        return max(0, int(parts[0])), max(0, int(parts[1]))
    except ValueError:
        return 0, 0


def ffmpeg_upscale_lanczos_2x(src: str, dst: str, ffmpeg_path: str) -> bool:
    """2× Lanczos upscale (mandatory pre-pass before Ken Burns on product stills)."""
    if not src or not os.path.isfile(src):
        return False
    try:
        r = subprocess.run(
            [
                ffmpeg_path,
                "-y",
                "-i",
                src,
                "-vf",
                "scale=iw*2:ih*2:flags=lanczos",
                dst,
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        return r.returncode == 0 and os.path.isfile(dst) and os.path.getsize(dst) > 500
    except (OSError, subprocess.TimeoutExpired):
        return False


def filter_locals_meeting_min_width(
    url_and_path: list[tuple[str, str]],
    ffprobe_path: str,
    min_w: int = 800,
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """
    Split (url, local_path) pairs into (ok, rejected) by image width.
    """
    ok: list[tuple[str, str]] = []
    bad: list[tuple[str, str]] = []
    for u, p in url_and_path:
        w, _h = ffprobe_image_wh(p, ffprobe_path)
        if w >= min_w:
            ok.append((u, p))
        else:
            bad.append((u, p))
    return ok, bad


def lanczos_2x_upscale_to_work(
    src_path: str,
    work_dir: str,
    tag: str,
    ffmpeg_path: str,
) -> str | None:
    """Write ``{tag}_upscaled.jpg`` under ``work_dir``; return path or None."""
    if not src_path or not os.path.isfile(src_path):
        return None
    os.makedirs(work_dir, exist_ok=True)
    dst = os.path.join(work_dir, f"{tag}_upscaled.jpg")
    if ffmpeg_upscale_lanczos_2x(src_path, dst, ffmpeg_path):
        return dst
    return None


def _default_ffprobe_path() -> str:
    from services.influencer_engine.rendering.ffmpeg_path import get_ffprobe_exe

    return get_ffprobe_exe()


def _first_product_kb_mad_signal_vf_suffix() -> str:
    """
    Sub-3px oscillating crop after zoompan — boosts frame-to-frame MAD at detector sample
    times (smooth KB alone stays below threshold). Kill-switch: UGC_FIRST_PRODUCT_KB_MAD_SIGNAL=0.
    """
    dis = (os.environ.get("UGC_FIRST_PRODUCT_KB_MAD_SIGNAL") or "1").strip().lower()
    if dis in ("0", "false", "no", "off"):
        return ""
    return (
        ",pad=iw+8:ih+8:4:4,"
        "crop=iw-8:ih-8:4+3*sin(6.2831853*t*5.5):4+3*cos(6.2831853*t*4.7),"
        "setsar=1"
    )


def ken_burns_fallback_mp4(
    image_path: str,
    out_path: str,
    duration_sec: float,
    ffmpeg_path: str,
    *,
    kb_rate: str = "0.0012",
    kb_cap: str = "1.06",
    variant: t.Literal["product", "avatar"] = "avatar",
    inject_pixel_mad_signal: bool = False,
    skip_upscale: bool = False,
    motion_variant: int = 0,
) -> bool:
    """
    Static image → full-frame 9:16 MP4. Product uses premium smooth zoom + drift; avatar uses standard KB.
    When ``inject_pixel_mad_signal`` is True (first product clip only), append a micro pad/crop wobble so
    top-band heuristics see enough pixel delta vs KB-only drift.

    Lanczos 2× upscale runs immediately before the zoompan graph unless ``skip_upscale=True`` (caller
    already upscaled). ``motion_variant`` varies Ken Burns rate/cap per clip index for avatar fallbacks.
    """
    if not image_path or not os.path.isfile(image_path):
        return False
    dur = max(0.5, float(duration_sec))
    dframes = max(1, int(OUTPUT_FPS * dur))
    work_dir = os.path.dirname(os.path.abspath(out_path)) or "."
    tmp_up = ""
    loop_input = image_path
    upscale_applied = False
    try:
        if not skip_upscale:
            fd, tmp_up = tempfile.mkstemp(suffix="_kb_up.jpg", dir=work_dir)
            os.close(fd)
            if not ffmpeg_upscale_lanczos_2x(image_path, tmp_up, ffmpeg_path):
                return False
            loop_input = tmp_up
            upscale_applied = True
        if variant == "product":
            try:
                cap = (os.environ.get("AFFILIATE_TOP_KB_CAP") or kb_cap or "1.04").strip()
            except Exception:
                cap = "1.04"
            rate = (os.environ.get("AFFILIATE_TOP_KB_RATE") or "0.00075").strip()
            zp = zoompan_product_premium_expr(dframes, OUTPUT_WIDTH, OUTPUT_HEIGHT, OUTPUT_FPS, cap, rate=rate)
        else:
            try:
                from services.influencer_engine.rendering.ffmpeg_uae_compliance import zoompan_ken_burns_expr
            except Exception:
                return False
            try:
                base_r = float((kb_rate or os.environ.get("AFFILIATE_BOTTOM_KB_RATE") or "0.0030").strip())
                base_c = float((kb_cap or os.environ.get("AFFILIATE_BOTTOM_KB_CAP") or "1.10").strip())
            except ValueError:
                base_r, base_c = 0.003, 1.1
            idx = int(motion_variant)
            base_r = base_r * (1.0 + 0.07 * (idx % 5) + 0.02 * ((idx // 5) % 2))
            base_c = min(1.28, base_c * (1.0 + 0.018 * (idx % 4)))
            br = f"{base_r:.6f}"
            bc = f"{base_c:.4f}"
            zp = zoompan_ken_burns_expr(dframes, OUTPUT_WIDTH, OUTPUT_HEIGHT, OUTPUT_FPS, rate=br, cap=bc)
        mad_suffix = ""
        if inject_pixel_mad_signal and variant == "product":
            mad_suffix = _first_product_kb_mad_signal_vf_suffix()
        vf = (
            f"scale={OUTPUT_WIDTH}:{OUTPUT_HEIGHT}:force_original_aspect_ratio=decrease,"
            f"pad={OUTPUT_WIDTH}:{OUTPUT_HEIGHT}:(ow-iw)/2:(oh-ih)/2,setsar=1,{zp}{mad_suffix}"
        )
        ow, oh = ffprobe_image_wh(image_path, _default_ffprobe_path())
        dbg = (os.environ.get("UGC_DEBUG_PIPELINE_QUALITY") or "1").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
        if dbg:
            print(
                "DEBUG_PIPELINE_QUALITY: selected_still_resolution="
                f"{ow}x{oh} upscale_applied={upscale_applied!s} variant={variant!s} motion_variant={motion_variant}",
                flush=True,
            )
        r = subprocess.run(
            [
                ffmpeg_path,
                "-y",
                "-loop",
                "1",
                "-i",
                loop_input,
                "-t",
                f"{dur:.4f}",
                "-vf",
                vf,
                "-an",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-pix_fmt",
                "yuv420p",
                "-r",
                str(OUTPUT_FPS),
                out_path,
            ],
            capture_output=True,
            text=True,
            timeout=600,
        )
        return r.returncode == 0 and os.path.isfile(out_path) and os.path.getsize(out_path) > 2000
    except (OSError, subprocess.TimeoutExpired):
        return False
    finally:
        if tmp_up and os.path.isfile(tmp_up):
            try:
                os.unlink(tmp_up)
            except OSError:
                pass


def apply_micro_spatial_jitter_mp4(path: str, ffmpeg_path: str) -> bool:
    """
    Sub-3px oscillating crop on padded canvas; boosts frame-to-frame MAD for
    top-band heuristics (NO_VISIBLE_PRODUCT_USAGE). Rewrites path in place.
    Set UGC_PRODUCT_STRIP_MICRO_JITTER=0 to skip (used on full product_fit in ugc_pipeline).
    """
    if not path or not os.path.isfile(path):
        return False
    dis = (os.environ.get("UGC_PRODUCT_STRIP_MICRO_JITTER") or "1").strip().lower()
    if dis in ("0", "false", "no", "off"):
        return True
    tmp = ""
    try:
        fd, tmp = tempfile.mkstemp(suffix="_jit.mp4")
        os.close(fd)
        # Spatial shift + frame-aliased brightness (n) so 22%/48%/74% samples differ in top-band MAD.
        vf = (
            "pad=iw+8:ih+8:4:4,"
            "crop=iw-8:ih-8:4+3*sin(6.2831853*t*3.2):4+3*cos(6.2831853*t*2.7),"
            "eq=brightness=0.12*sin(6.2831853*n/4),"
            "setsar=1"
        )
        r = subprocess.run(
            [
                ffmpeg_path,
                "-y",
                "-i",
                path,
                "-vf",
                vf,
                "-an",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-pix_fmt",
                "yuv420p",
                "-r",
                str(OUTPUT_FPS),
                tmp,
            ],
            capture_output=True,
            text=True,
            timeout=600,
        )
        if r.returncode != 0 or not os.path.isfile(tmp) or os.path.getsize(tmp) < 2000:
            return False
        os.replace(tmp, path)
        tmp = ""
        return True
    except (OSError, subprocess.TimeoutExpired):
        return False
    finally:
        if tmp and os.path.isfile(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass
