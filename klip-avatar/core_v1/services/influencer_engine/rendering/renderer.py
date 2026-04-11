"""
Influencer Engine — Renderer factory and backend abstraction.

Supports: mock, local, external API renderers.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

BACKEND_ENV = "INFLUENCER_RENDER_BACKEND"  # mock | local | external


class RendererBackend(ABC):
    """Abstract renderer backend."""

    @abstractmethod
    def render_avatar(self, config: Dict[str, Any], output_path: Optional[str] = None) -> Dict[str, Any]:
        pass

    @abstractmethod
    def render_voice(self, text: str, config: Dict[str, Any], output_path: Optional[str] = None) -> Dict[str, Any]:
        pass

    @abstractmethod
    def render_video(self, config: Dict[str, Any], output_path: Optional[str] = None) -> Dict[str, Any]:
        pass

    @abstractmethod
    def render_thumbnail(self, config: Dict[str, Any], output_path: Optional[str] = None) -> Dict[str, Any]:
        pass


class MockRenderer(RendererBackend):
    """No-op renderer for tests and missing dependencies."""

    def render_avatar(self, config: Dict[str, Any], output_path: Optional[str] = None) -> Dict[str, Any]:
        return {"url": f"mock://avatar/{config.get('avatar_id', 'default')}", "mock": True}

    def render_voice(self, text: str, config: Dict[str, Any], output_path: Optional[str] = None) -> Dict[str, Any]:
        return {"url": "mock://audio/voice.mp3", "mock": True, "length": len(text)}

    def render_video(self, config: Dict[str, Any], output_path: Optional[str] = None) -> Dict[str, Any]:
        return {"url": "mock://video/out.mp4", "mock": True}

    def render_thumbnail(self, config: Dict[str, Any], output_path: Optional[str] = None) -> Dict[str, Any]:
        return {"url": "mock://thumb/thumb.jpg", "mock": True}


class LocalRenderer(RendererBackend):
    """Local rendering (delegates to avatar/voice/video/thumbnail renderers)."""

    def __init__(self):
        from .avatar_renderer import AvatarRenderer
        from .voice_renderer import VoiceRenderer
        from .video_renderer import VideoRenderer
        from .thumbnail_renderer import ThumbnailRenderer
        self._avatar = AvatarRenderer()
        self._voice = VoiceRenderer()
        self._video = VideoRenderer()
        self._thumb = ThumbnailRenderer()

    def render_avatar(self, config: Dict[str, Any], output_path: Optional[str] = None) -> Dict[str, Any]:
        return self._avatar.render(config, output_path)

    def render_voice(self, text: str, config: Dict[str, Any], output_path: Optional[str] = None) -> Dict[str, Any]:
        return self._voice.render(text, config, output_path)

    def render_video(self, config: Dict[str, Any], output_path: Optional[str] = None) -> Dict[str, Any]:
        return self._video.render(config, output_path)

    def render_thumbnail(self, config: Dict[str, Any], output_path: Optional[str] = None) -> Dict[str, Any]:
        return self._thumb.render(config, output_path)


class ExternalAPIRenderer(RendererBackend):
    """External API renderer (placeholder: same as mock until API configured)."""

    def __init__(self, base_url: str = ""):
        self.base_url = base_url or os.environ.get("RENDER_API_URL", "https://render.example.com")

    def render_avatar(self, config: Dict[str, Any], output_path: Optional[str] = None) -> Dict[str, Any]:
        return {"url": f"{self.base_url}/avatar", "mock": True}

    def render_voice(self, text: str, config: Dict[str, Any], output_path: Optional[str] = None) -> Dict[str, Any]:
        return {"url": f"{self.base_url}/voice", "mock": True}

    def render_video(self, config: Dict[str, Any], output_path: Optional[str] = None) -> Dict[str, Any]:
        return {"url": f"{self.base_url}/video", "mock": True}

    def render_thumbnail(self, config: Dict[str, Any], output_path: Optional[str] = None) -> Dict[str, Any]:
        return {"url": f"{self.base_url}/thumbnail", "mock": True}


def get_renderer(backend: Optional[str] = None) -> RendererBackend:
    """Return renderer backend: mock, local, or external."""
    mode = (backend or os.environ.get(BACKEND_ENV, "mock")).lower()
    if mode == "local":
        return LocalRenderer()
    if mode == "external":
        return ExternalAPIRenderer()
    return MockRenderer()
