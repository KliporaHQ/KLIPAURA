"""
Influencer Engine — Voice renderer.

Renders TTS audio from script text.
Supports: ElevenLabs (ELEVENLABS_API_KEY), TTS_SERVICE_URL, or mock fallback.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass


def get_voice_profile(avatar_profile: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Extract voice profile from avatar profile for consistent TTS style.
    Returns dict: accent, tone, energy, opening_phrase, voice_id (optional).
    """
    if not avatar_profile:
        return {
            "accent": "neutral",
            "tone": "warm, friendly",
            "energy": "medium",
            "opening_phrase": "Hey there",
        }
    vp = avatar_profile.get("voice_profile") or {}
    if isinstance(vp, dict):
        return {
            "accent": vp.get("accent") or "neutral",
            "tone": vp.get("tone") or "warm, friendly",
            "energy": vp.get("energy") or "medium",
            "opening_phrase": vp.get("opening_phrase") or avatar_profile.get("signature_phrase") or "Hey there",
            "voice_id": vp.get("voice_id"),
        }
    return {
        "accent": "neutral",
        "tone": "warm, friendly",
        "energy": "medium",
        "opening_phrase": avatar_profile.get("signature_phrase") or "Hey there",
    }


def generate_voice(text: str, config: Optional[Dict[str, Any]] = None, output_path: Optional[str] = None) -> Dict[str, Any]:
    """
    Generate voice audio from text. Uses ElevenLabs or TTS_SERVICE_URL from .env.
    Fallback: mock audio (no file written).
    Returns dict with url, path, duration_seconds, length_chars, mock (bool).
    """
    config = config or {}
    voice_id = config.get("voice_id") or "21m00Tcm4TlvDq8ikWAM"  # Rachel default
    tts_url = (os.environ.get("TTS_SERVICE_URL") or "").strip()
    eleven_key = (os.environ.get("ELEVENLABS_API_KEY") or os.environ.get("XI_API_KEY") or "").strip()

    # 1) ElevenLabs
    if eleven_key and text:
        out_path = output_path
        if not out_path:
            import tempfile
            fd, out_path = tempfile.mkstemp(suffix=".mp3", prefix="voice_")
            os.close(fd)
        ok, result = _elevenlabs_tts(text, eleven_key, voice_id, out_path)
        if ok and result:
            duration = result.get("duration_seconds") or max(1.0, len(text) / 15.0)
            return {
                "url": f"file://{out_path}" if out_path else "mock://audio/voice.mp3",
                "path": out_path,
                "voice_id": voice_id,
                "length_chars": len(text),
                "duration_seconds": duration,
                "mock": False,
            }

    # 2) Generic TTS_SERVICE_URL (POST { "script": text, "jobId": optional } -> { "url": audio_url } or binary)
    if tts_url and text:
        ok, result = _tts_service_url(text, tts_url, output_path, config)
        if ok and result:
            return {
                "url": result.get("url") or (f"file://{output_path}" if output_path else "mock://audio/voice.mp3"),
                "path": result.get("path") or output_path,
                "voice_id": voice_id,
                "length_chars": len(text),
                "duration_seconds": result.get("duration_seconds") or max(1.0, len(text) / 15.0),
                "mock": False,
            }

    # 3) Mock — write a real silent MP3 so the pipeline gets a valid file and ffmpeg can produce a non-corrupt video
    duration_sec = max(1.0, len(text) / 15.0)
    out_path = output_path
    if not out_path:
        import tempfile
        fd, out_path = tempfile.mkstemp(suffix=".mp3", prefix="voice_")
        os.close(fd)
    if _write_silent_mp3(out_path, duration_sec) and os.path.getsize(out_path) > 0:
        return {
            "url": f"file://{out_path}",
            "path": out_path,
            "voice_id": voice_id,
            "length_chars": len(text),
            "duration_seconds": duration_sec,
            "mock": True,
        }
    try:
        os.remove(out_path)
    except OSError:
        pass
    return {
        "url": "mock://audio/voice.mp3",
        "voice_id": voice_id,
        "length_chars": len(text),
        "duration_seconds": duration_sec,
        "path": None,
        "mock": True,
    }


# Minimal valid silent MP3 (~1s) so mock audio is never 0 KB if ffmpeg is unavailable (base64)
_SILENT_MP3_B64 = (
    "SUQzBAAAAAAAI1RTU0UAAAAPAAADTGF2ZjU2LjM2LjEwMAAAAAAAAAAAAAAA//OEAAAAAAAAAAAAAAAAAAAAAAAASW5mbwAAAA8AAAAEAAABIADAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDV1dXV1dXV1dXV1dXV1dXV1dXV1dXV1dXV6urq6urq6urq6urq6urq6urq6urq6urq6v////////////////////////////////8AAAAATGF2YzU2LjQxAAAAAAAAAAAAAAAAJAAAAAAAAAAAASDs90hvAAAAAAAAAAAAAAAAAAAA//MUZAAAAAGkAAAAAAAAA0gAAAAATEFN//MUZAMAAAGkAAAAAAAAA0gAAAAARTMu//MUZAYAAAGkAAAAAAAAA0gAAAAAOTku//MUZAkAAAGkAAAAAAAAA0gAAAAANVVV"
)


def _write_silent_mp3(output_path: str, duration_seconds: float = 30.0) -> bool:
    """Write a valid silent MP3 so the file is never 0 KB. Uses ffmpeg if available, else minimal embedded MP3."""
    import subprocess
    import base64
    try:
        from .ffmpeg_path import get_ffmpeg_exe
        ffmpeg_exe = get_ffmpeg_exe()
    except Exception:
        ffmpeg_exe = "ffmpeg"
    # 1) Prefer ffmpeg for proper duration
    try:
        cmd = [
            ffmpeg_exe, "-y",
            "-f", "lavfi", "-i", f"anullsrc=r=44100:cl=stereo:d={duration_seconds}",
            "-acodec", "libmp3lame", "-q:a", "5",
            output_path,
        ]
        subprocess.run(cmd, capture_output=True, timeout=30, check=True)
        if os.path.isfile(output_path) and os.path.getsize(output_path) > 0:
            return True
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        pass
    # 2) Fallback: write minimal valid silent MP3 (short; ffmpeg will loop/extend when composing video)
    try:
        data = base64.b64decode(_SILENT_MP3_B64)
        if data:
            with open(output_path, "wb") as f:
                f.write(data)
            return os.path.isfile(output_path) and os.path.getsize(output_path) > 0
    except Exception:
        pass
    return False


def _elevenlabs_tts(text: str, api_key: str, voice_id: str, output_path: str) -> tuple[bool, Optional[Dict[str, Any]]]:
    """Call ElevenLabs API; write mp3 to output_path. Returns (ok, result_dict)."""
    import json
    import urllib.request
    import urllib.error
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    data = json.dumps({"text": text}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Accept": "audio/mpeg",
            "Content-Type": "application/json",
            "xi-api-key": api_key,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            audio_bytes = resp.read()
        if audio_bytes and output_path:
            with open(output_path, "wb") as f:
                f.write(audio_bytes)
        return True, {"path": output_path, "duration_seconds": max(1.0, len(text) / 15.0)}
    except (urllib.error.HTTPError, OSError):
        return False, None


def _tts_service_url(text: str, base_url: str, output_path: Optional[str], config: Dict[str, Any]) -> tuple[bool, Optional[Dict[str, Any]]]:
    """POST script to TTS_SERVICE_URL; expect { url } or binary. Returns (ok, result_dict)."""
    import json
    import urllib.request
    import urllib.error
    job_id = config.get("job_id") or ""
    payload = {"script": text, "jobId": job_id}
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        base_url.rstrip("/"),
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = resp.read()
        # If JSON with url
        try:
            out = json.loads(body.decode())
            if isinstance(out, dict) and out.get("url"):
                return True, {"url": out["url"], "duration_seconds": out.get("duration_seconds")}
        except Exception:
            pass
        # Binary audio -> save to output_path
        if body and output_path:
            with open(output_path, "wb") as f:
                f.write(body)
            return True, {"path": output_path, "url": f"file://{output_path}"}
        return False, None
    except (urllib.error.HTTPError, OSError):
        return False, None


class VoiceRenderer:
    """Renders voice/audio from text and voice config. Supports avatar voice_profile for consistency."""

    def render(
        self,
        text: str,
        config: Dict[str, Any],
        output_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Produce audio asset. Uses generate_voice (ElevenLabs / TTS_SERVICE_URL or mock).
        If config contains avatar_profile, voice_id can be overridden from get_voice_profile(avatar_profile).
        Returns dict with url, path, duration_seconds, mock.
        """
        avatar_profile = config.get("avatar_profile")
        if avatar_profile:
            voice_profile = get_voice_profile(avatar_profile)
            vid = voice_profile.get("voice_id")
            if vid:
                config = {**config, "voice_id": vid}
        return generate_voice(text, config, output_path)
