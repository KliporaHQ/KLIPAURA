from utils.logger import log_stage, log_error, log_structured
from utils.state import state
from services.elevenlabs_client import elevenlabs_client
import time
from pathlib import Path
from config import OUTPUT_DIR

import pipeline.job_helpers  # noqa: F401
from engine.cinematic_v2.video_config import resolve_duration_seconds


def run(script: str, job: dict | None = None) -> dict:
    """Prepare media assets with real voice generation."""
    log_stage("MEDIA", "Preparing media assets", 40)
    job = job or {}
    vc = job.get("video_config") or {}
    duration_tier = str(vc.get("duration_tier") or "SHORT").upper()
    try:
        target_sec = float(resolve_duration_seconds(duration_tier))
    except Exception:
        target_sec = 6.0
    max_chars = max(80, int(target_sec * 14))

    try:
        voice_path = elevenlabs_client.generate_voice((script or "")[: max_chars])

        out = OUTPUT_DIR
        out.mkdir(parents=True, exist_ok=True)

        assets = {
            "voice_path": voice_path,
            "audio_path": voice_path,
            "images": [str(out / "avatar.png"), str(out / "product.jpg")],
            "script": script,
            "duration": target_sec,
            "video_config": vc,
            "mode": job.get("mode") or "TOPIC",
            "product_url": job.get("product_url"),
        }

        time.sleep(0.5)
        log_structured(
            "media",
            "Media assets ready",
            "info",
            path="real" if elevenlabs_client.last_used_real_tts else "fallback",
            audio_path=voice_path,
        )
        log_stage("MEDIA", f"Media assets ready with audio: {voice_path}", 60)
        return assets

    except RuntimeError as e:
        log_error("MEDIA", str(e))
        log_structured(
            "media",
            "ElevenLabs failed — fallback mock audio so pipeline can continue",
            "warn",
            path="fallback",
            reason=str(e)[:300],
        )
        out = OUTPUT_DIR
        out.mkdir(parents=True, exist_ok=True)
        fallback_path = str(out / "audio.mp3")
        elevenlabs_client._create_mock_audio(fallback_path)
        return {
            "voice_path": fallback_path,
            "audio_path": fallback_path,
            "images": [],
            "script": script,
            "duration": target_sec,
            "video_config": vc,
            "mode": job.get("mode") or "TOPIC",
            "product_url": job.get("product_url"),
        }
    except Exception as e:
        log_error("MEDIA", str(e))
        log_structured("media", "Unexpected error — fallback mock audio", "warn", path="fallback", error=str(e)[:300])
        out = OUTPUT_DIR
        out.mkdir(parents=True, exist_ok=True)
        fallback_path = str(out / "audio.mp3")
        elevenlabs_client._create_mock_audio(fallback_path)
        return {
            "voice_path": fallback_path,
            "audio_path": fallback_path,
            "images": [],
            "script": script,
            "duration": target_sec,
            "video_config": vc,
            "mode": job.get("mode") or "TOPIC",
            "product_url": job.get("product_url"),
        }
