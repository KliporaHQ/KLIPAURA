"""VideoFormat engine — 10 format classes with tiered cost model and fallback chain.

Format hierarchy:
    Tier 1 (cheapest/most reliable): TextForwardFormat, StaticNarrationFormat
    Tier 2 (stub → delegate):        BeforeAfterFormat, ComparisonFormat, CountdownFormat
    Tier 3 (full production):        SplitFormat, FullscreenFormat, DemoSequenceFormat
    Tier 4 (AI-intensive):           LipsyncFormat, HookRevealFormat

Fallback chain (automatic on RuntimeError):
    LipsyncFormat → SplitFormat → FullscreenFormat → TextForwardFormat

Base64 padding fix (for WaveSpeed I2V):
    Use _b64_padded() when encoding image bytes — wavespeed_video.py may receive unpadded
    base64 from some sources which causes 400 errors.
"""

from __future__ import annotations

import base64
import os
import sys
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Type

_HERE = Path(__file__).resolve().parent
_CORE_V1 = _HERE.parent
if str(_CORE_V1) not in sys.path:
    sys.path.insert(0, str(_CORE_V1))


# ── Helpers ──────────────────────────────────────────────────────────────────

def _b64_padded(img_bytes: bytes) -> str:
    """Encode image bytes to base64 with correct padding (fixes WaveSpeed 400 errors)."""
    b64 = base64.b64encode(img_bytes).decode("utf-8")
    pad = 4 - len(b64) % 4
    if pad != 4:
        b64 += "=" * pad
    return b64


def _ffmpeg_exe(ffmpeg_path: Optional[str] = None) -> str:
    return (
        ffmpeg_path
        or os.getenv("FFMPEG_PATH")
        or "ffmpeg"
    ).strip() or "ffmpeg"


# ── Base class ───────────────────────────────────────────────────────────────

class VideoFormat(ABC):
    """Abstract base for all video formats."""

    tier: int = 0
    name: str = ""

    def can_render(self, passport: Any) -> Tuple[bool, str]:
        """Return (True, '') if this format can render the given passport.  Override to add checks."""
        return True, ""

    @abstractmethod
    def render(
        self,
        passport: Any,
        voice_path: str,
        avatar_assets: Dict[str, Any],
        output_path: str,
        ffmpeg_exe: Optional[str] = None,
    ) -> str:
        """Render video.  Returns output file path on success.  Raises RuntimeError on failure."""
        ...

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} tier={self.tier}>"


# ── Tier 1: Always-works fallbacks ───────────────────────────────────────────

class TextForwardFormat(VideoFormat):
    """Pure FFmpeg drawtext overlay — zero API calls.  The ultimate fallback."""

    tier = 1
    name = "TextForwardFormat"

    def render(self, passport, voice_path, avatar_assets, output_path, ffmpeg_exe=None):
        import subprocess, shlex, tempfile, os

        ffmpeg = _ffmpeg_exe(ffmpeg_exe)
        images = (passport.images if hasattr(passport, "images") else passport.get("images") or [])
        title = (passport.title if hasattr(passport, "title") else passport.get("title") or "Product Review")
        cta = os.getenv("AFFILIATE_CTA_DEFAULT") or "Get yours - link in bio"

        if not images:
            raise RuntimeError("TextForwardFormat: no images in passport")

        # Use first image as background
        img = images[0]
        img_path = img if os.path.isfile(img) else ""
        if not img_path:
            # Download image
            import urllib.request
            tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
            urllib.request.urlretrieve(img, tmp.name)
            img_path = tmp.name

        safe_title = title.replace("'", "").replace(":", " -")[:60]
        safe_cta = cta.replace("'", "")

        cmd = [
            ffmpeg, "-y",
            "-loop", "1", "-i", img_path,
            "-i", voice_path,
            "-vf",
            (
                f"scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
                f"drawtext=text='{safe_title}':x=(w-tw)/2:y=100:fontsize=48:fontcolor=white:box=1:boxcolor=black@0.6,"
                f"drawtext=text='{safe_cta}':x=(w-tw)/2:y=h-120:fontsize=36:fontcolor=yellow:box=1:boxcolor=black@0.6"
            ),
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k",
            "-shortest", "-movflags", "+faststart",
            output_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0 or not os.path.isfile(output_path):
            raise RuntimeError(f"TextForwardFormat FFmpeg failed: {result.stderr[-500:]}")
        return output_path


class StaticNarrationFormat(VideoFormat):
    """Digital product narration — delegates to TextForwardFormat (stub)."""

    tier = 1
    name = "StaticNarrationFormat"

    def render(self, passport, voice_path, avatar_assets, output_path, ffmpeg_exe=None):
        return TextForwardFormat().render(passport, voice_path, avatar_assets, output_path, ffmpeg_exe)


# ── Tier 2: Stub formats (delegate to tier 3) ────────────────────────────────

class BeforeAfterFormat(VideoFormat):
    """Before/after comparison — delegates to SplitFormat (stub)."""

    tier = 2
    name = "BeforeAfterFormat"

    def render(self, passport, voice_path, avatar_assets, output_path, ffmpeg_exe=None):
        return SplitFormat().render(passport, voice_path, avatar_assets, output_path, ffmpeg_exe)


class ComparisonFormat(VideoFormat):
    """Side-by-side comparison — delegates to SplitFormat (stub)."""

    tier = 2
    name = "ComparisonFormat"

    def render(self, passport, voice_path, avatar_assets, output_path, ffmpeg_exe=None):
        return SplitFormat().render(passport, voice_path, avatar_assets, output_path, ffmpeg_exe)


class CountdownFormat(VideoFormat):
    """Countdown reveal — delegates to SplitFormat (stub)."""

    tier = 2
    name = "CountdownFormat"

    def render(self, passport, voice_path, avatar_assets, output_path, ffmpeg_exe=None):
        return SplitFormat().render(passport, voice_path, avatar_assets, output_path, ffmpeg_exe)


# ── Tier 3: Full production formats ──────────────────────────────────────────

class SplitFormat(VideoFormat):
    """Product clip (top) + avatar Ken Burns slideshow (bottom) with narration.

    Wraps ``render_affiliate_split_video`` from services/video-render/engine.py.
    """

    tier = 3
    name = "SplitFormat"

    def can_render(self, passport):
        images = passport.images if hasattr(passport, "images") else passport.get("images") or []
        if not images:
            return False, "SplitFormat requires at least 1 image"
        return True, ""

    def render(self, passport, voice_path, avatar_assets, output_path, ffmpeg_exe=None):
        import importlib.util
        import os as _os

        # Load video-render engine via file path (hyphen in directory prevents normal import)
        _engine_path = _CORE_V1 / "services" / "video-render" / "engine.py"
        _spec = importlib.util.spec_from_file_location("klip_video_render_engine", str(_engine_path))
        if _spec is None or _spec.loader is None:
            raise RuntimeError("SplitFormat: cannot load video-render engine")
        _mod = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)  # type: ignore[union-attr]

        images = passport.images if hasattr(passport, "images") else passport.get("images") or []
        avatar_images = avatar_assets.get("avatar_images") or []
        job_id = (passport.passport_id if hasattr(passport, "passport_id") else passport.get("passport_id") or "")

        # Build a product clip from first image via Ken Burns if no pre-rendered clip provided
        product_video_path = avatar_assets.get("product_video_path") or ""
        if not product_video_path and images:
            product_video_path = _make_kb_clip(images[0], job_id, ffmpeg_exe)

        result = _mod.render_affiliate_split_video(
            {
                "job_id": job_id,
                "product_video_path": product_video_path,
                "image_urls": avatar_images or images[1:],
                "voice_path": voice_path,
            },
            output_path=output_path,
            ffmpeg_path=ffmpeg_exe,
            max_retries=1,
        )
        if not result.get("success"):
            raise RuntimeError(f"SplitFormat render failed: {result.get('error')}")
        return output_path


class FullscreenFormat(VideoFormat):
    """Single-stream fullscreen Ken Burns.

    Wraps ``ken_burns_fallback_mp4`` from engine/cinematic_v2/phase2_generation_guard.py.
    """

    tier = 3
    name = "FullscreenFormat"

    def can_render(self, passport):
        images = passport.images if hasattr(passport, "images") else passport.get("images") or []
        if not images:
            return False, "FullscreenFormat requires at least 1 image"
        return True, ""

    def render(self, passport, voice_path, avatar_assets, output_path, ffmpeg_exe=None):
        import tempfile, os, subprocess
        from engine.cinematic_v2.phase2_generation_guard import ken_burns_fallback_mp4

        images = passport.images if hasattr(passport, "images") else passport.get("images") or []
        if not images:
            raise RuntimeError("FullscreenFormat: no images")

        # Get audio duration
        duration = _audio_duration_sec(voice_path) or 30.0

        # Download first image if URL
        img = images[0]
        img_path = img if os.path.isfile(img) else _download_image(img)

        ffmpeg = _ffmpeg_exe(ffmpeg_exe)
        # Build silent video with Ken Burns
        silent_out = output_path.replace(".mp4", "_silent.mp4")
        ok = ken_burns_fallback_mp4(img_path, silent_out, duration, ffmpeg, variant="product")
        if not ok or not os.path.isfile(silent_out):
            raise RuntimeError("FullscreenFormat: ken_burns_fallback_mp4 failed")

        # Mux with voice
        cmd = [ffmpeg, "-y", "-i", silent_out, "-i", voice_path,
               "-c:v", "copy", "-c:a", "aac", "-b:a", "128k",
               "-shortest", output_path]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            raise RuntimeError(f"FullscreenFormat mux failed: {r.stderr[-300:]}")
        return output_path


class DemoSequenceFormat(VideoFormat):
    """Demo sequence — tries LipsyncFormat first, falls through to SplitFormat."""

    tier = 3
    name = "DemoSequenceFormat"

    def render(self, passport, voice_path, avatar_assets, output_path, ffmpeg_exe=None):
        try:
            return LipsyncFormat().render(passport, voice_path, avatar_assets, output_path, ffmpeg_exe)
        except RuntimeError:
            return SplitFormat().render(passport, voice_path, avatar_assets, output_path, ffmpeg_exe)


# ── Tier 4: AI-intensive formats ─────────────────────────────────────────────

class LipsyncFormat(VideoFormat):
    """Talking-head avatar with WaveSpeed AI lipsync.

    Wraps ``generate_lipsync_bottom`` from pipeline/ugc_lipsync_adapter.py.
    Applies base64 padding fix before sending to WaveSpeed.
    """

    tier = 4
    name = "LipsyncFormat"

    def can_render(self, passport):
        if not (os.getenv("WAVESPEED_API_KEY") or "").strip():
            return False, "LipsyncFormat requires WAVESPEED_API_KEY"
        return True, ""

    def render(self, passport, voice_path, avatar_assets, output_path, ffmpeg_exe=None):
        from pipeline.ugc_lipsync_adapter import generate_lipsync_bottom

        avatar_images = avatar_assets.get("avatar_images") or []
        if not avatar_images:
            raise RuntimeError("LipsyncFormat: no avatar_images in avatar_assets")

        ok, result = generate_lipsync_bottom(
            avatar_images,
            voice_path,
            output_path,
            job_id=(passport.passport_id if hasattr(passport, "passport_id") else None),
        )
        if not ok:
            raise RuntimeError(f"LipsyncFormat: {result}")
        return output_path


class HookRevealFormat(VideoFormat):
    """Hook-then-reveal — tries LipsyncFormat first, falls through to SplitFormat."""

    tier = 4
    name = "HookRevealFormat"

    def render(self, passport, voice_path, avatar_assets, output_path, ffmpeg_exe=None):
        try:
            return LipsyncFormat().render(passport, voice_path, avatar_assets, output_path, ffmpeg_exe)
        except RuntimeError:
            return SplitFormat().render(passport, voice_path, avatar_assets, output_path, ffmpeg_exe)


# ── Registry & fallback chain ────────────────────────────────────────────────

FALLBACK_CHAIN: List[Type[VideoFormat]] = [
    LipsyncFormat,
    SplitFormat,
    FullscreenFormat,
    TextForwardFormat,
]

_ALL_FORMATS: List[Type[VideoFormat]] = [
    LipsyncFormat,
    SplitFormat,
    FullscreenFormat,
    TextForwardFormat,
    StaticNarrationFormat,
    BeforeAfterFormat,
    ComparisonFormat,
    CountdownFormat,
    DemoSequenceFormat,
    HookRevealFormat,
]

FORMAT_REGISTRY: Dict[str, Type[VideoFormat]] = {cls.name: cls for cls in _ALL_FORMATS}


def render_with_fallback(
    format_name: str,
    passport: Any,
    voice_path: str,
    avatar_assets: Dict[str, Any],
    output_path: str,
    ffmpeg_exe: Optional[str] = None,
) -> str:
    """Try ``format_name`` first, then walk FALLBACK_CHAIN on RuntimeError.

    Returns the output file path.
    Raises RuntimeError if ALL formats fail (includes TextForwardFormat which should never fail
    if images are present).
    """
    chain: List[Type[VideoFormat]] = []
    cls = FORMAT_REGISTRY.get(format_name)
    if cls:
        chain.append(cls)
    # Add fallbacks not already in chain
    for fc in FALLBACK_CHAIN:
        if fc not in chain:
            chain.append(fc)

    last_err = "no formats attempted"
    for fmt_cls in chain:
        fmt = fmt_cls()
        ok, reason = fmt.can_render(passport)
        if not ok:
            print(f"[format_engine] {fmt.name} skipped: {reason}", flush=True)
            last_err = reason
            continue
        try:
            print(f"[format_engine] Trying {fmt.name}...", flush=True)
            result = fmt.render(passport, voice_path, avatar_assets, output_path, ffmpeg_exe)
            print(f"[format_engine] {fmt.name} succeeded → {result}", flush=True)
            return result
        except RuntimeError as exc:
            last_err = str(exc)
            print(f"[format_engine] {fmt.name} failed: {last_err} — trying next", flush=True)

    raise RuntimeError(f"All formats exhausted. Last error: {last_err}")


# ── Private helpers ───────────────────────────────────────────────────────────

def _download_image(url: str) -> str:
    import tempfile
    import urllib.request
    suffix = ".jpg" if ".jpg" in url.lower() else ".png"
    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    urllib.request.urlretrieve(url, tmp.name)
    return tmp.name


def _make_kb_clip(image_or_url: str, job_id: str, ffmpeg_exe: Optional[str]) -> str:
    """Create a short Ken Burns clip from an image URL or path.  Returns local path."""
    import tempfile, os
    img = image_or_url if os.path.isfile(image_or_url) else _download_image(image_or_url)
    out = os.path.join(tempfile.gettempdir(), f"kb_{job_id}_product.mp4")
    try:
        from engine.cinematic_v2.phase2_generation_guard import ken_burns_fallback_mp4
        ken_burns_fallback_mp4(img, out, 8.0, _ffmpeg_exe(ffmpeg_exe), variant="product")
    except Exception:
        pass
    return out if os.path.isfile(out) else ""


def _audio_duration_sec(voice_path: str) -> Optional[float]:
    """Return audio duration in seconds using ffprobe, or None on failure."""
    import subprocess, json as _json
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_streams", voice_path],
            capture_output=True, text=True, timeout=10,
        )
        info = _json.loads(result.stdout)
        for stream in info.get("streams", []):
            dur = stream.get("duration")
            if dur:
                return float(dur)
    except Exception:
        pass
    return None


__all__ = [
    "VideoFormat",
    "FORMAT_REGISTRY",
    "FALLBACK_CHAIN",
    "render_with_fallback",
    "TextForwardFormat",
    "StaticNarrationFormat",
    "SplitFormat",
    "LipsyncFormat",
    "FullscreenFormat",
    "BeforeAfterFormat",
    "ComparisonFormat",
    "CountdownFormat",
    "DemoSequenceFormat",
    "HookRevealFormat",
    "_b64_padded",
]
