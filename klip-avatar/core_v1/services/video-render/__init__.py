"""
KLIPAURA Video Render — FFmpeg pipeline (no Shotstack).

Pipeline: images → video scenes (zoompan) → concatenate → voice overlay → captions → music → 1080x1920 MP4 30fps.
Called by Mission Control or Railway render service. Max retries: 3.
"""

from __future__ import annotations

from .engine import render_video, RenderInput, RenderResult

__all__ = ["render_video", "RenderInput", "RenderResult"]
