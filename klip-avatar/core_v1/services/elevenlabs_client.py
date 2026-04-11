import os
import requests
import time
from pathlib import Path
from utils.logger import log, log_error, log_structured
from config import OUTPUT_DIR

# Widely supported on standard plans; override with ELEVENLABS_MODEL_ID if needed.
DEFAULT_TTS_MODEL = "eleven_multilingual_v2"


class ElevenLabsClient:
    """Real ElevenLabs voice generation client with fallback."""

    def __init__(self):
        self.api_key = os.getenv("ELEVENLABS_API_KEY", "mock_key")
        self.voice_id = "21m00Tcm4TlvDq8ikWAM"  # Default Rachel voice
        self.output_dir = OUTPUT_DIR
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.last_used_real_tts = False

    def _api_key_valid(self) -> bool:
        k = (self.api_key or "").strip()
        return bool(k) and not k.lower().startswith("mock") and len(k) >= 10

    def generate_voice(self, text: str, voice_id: str = None) -> str:
        """Generate real voice audio using ElevenLabs API."""
        if not voice_id:
            voice_id = self.voice_id

        log(f"Generating voice for {len(text)} chars using voice {voice_id}", "ELEVENLABS")

        audio_path = str(self.output_dir / "audio.mp3")

        if not self._api_key_valid():
            self.last_used_real_tts = False
            log_structured("elevenlabs", "No valid API key — mock audio", "warn", path="fallback", api="skipped")
            log("No valid ElevenLabs key - using mock audio", "ELEVENLABS")
            self._create_mock_audio(audio_path)
            return audio_path

        model_id = (os.getenv("ELEVENLABS_MODEL_ID") or DEFAULT_TTS_MODEL).strip()

        try:
            url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
            headers = {
                "Accept": "audio/mpeg",
                "Content-Type": "application/json",
                "xi-api-key": self.api_key
            }
            data = {
                "text": text[:1000],  # Limit text
                "model_id": model_id,
                "voice_settings": {
                    "stability": 0.5,
                    "similarity_boost": 0.75
                }
            }

            response = requests.post(url, json=data, headers=headers, timeout=30)

            if response.status_code == 401:
                log_structured(
                    "elevenlabs",
                    "API rejected request (401)",
                    "error",
                    path="failed",
                    model_id=model_id,
                )
                raise RuntimeError("Invalid ElevenLabs API key or plan does not support model")
            if response.status_code == 200:
                self.last_used_real_tts = True
                with open(audio_path, "wb") as f:
                    f.write(response.content)
                log_structured(
                    "elevenlabs",
                    "TTS success",
                    "info",
                    path="real",
                    model_id=model_id,
                    bytes=len(response.content),
                )
                log(f"Real audio generated and saved to {audio_path}", "ELEVENLABS")
                return audio_path
            else:
                self.last_used_real_tts = False
                log_structured(
                    "elevenlabs",
                    "API error — mock audio",
                    "warn",
                    path="fallback",
                    status=response.status_code,
                    body=(response.text or "")[:200],
                )
                log(f"API error: {response.status_code} - {response.text[:100]}", "ELEVENLABS")
                self._create_mock_audio(audio_path)
                return audio_path

        except RuntimeError:
            self.last_used_real_tts = False
            raise
        except Exception as e:
            self.last_used_real_tts = False
            log_error("ELEVENLABS", str(e))
            log_structured("elevenlabs", "Exception — mock audio", "warn", path="fallback", error=str(e)[:200])
            self._create_mock_audio(audio_path)
            return audio_path

    def _create_mock_audio(self, path: str):
        """Create silent or dummy audio file."""
        try:
            with open(path, "wb") as f:
                # Minimal MP3 header for dummy
                f.write(b'\xff\xfb\x90\xc4' + b'\x00' * 100)
            log_structured("elevenlabs", "Wrote fallback mock audio file", "info", path="fallback")
            log("Created fallback audio file", "ELEVENLABS")
        except Exception:
            Path(path).touch()


elevenlabs_client = ElevenLabsClient()
