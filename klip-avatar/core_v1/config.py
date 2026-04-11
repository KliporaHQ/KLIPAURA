import os
from pathlib import Path
from dotenv import load_dotenv
from typing import Dict, Any

_BASE = Path(__file__).resolve().parent
load_dotenv(_BASE / ".env", encoding="utf-8-sig", override=False)


def validate_required_environment() -> None:
    required_vars = ["GROQ_API_KEY", "ELEVENLABS_API_KEY"]
    for var in required_vars:
        if not os.getenv(var):
            raise RuntimeError(f"Missing required env var: {var}")


def _resolve_output_dir() -> Path:
    raw = (os.getenv("OUTPUT_DIR") or "").strip()
    if not raw:
        return _BASE / "outputs"
    p = Path(raw)
    return p if p.is_absolute() else (_BASE / p)


class Config:
    """Feature flags and configuration for Core V1.5 bridge."""

    USE_REDIS = os.getenv("USE_REDIS", "false").lower() in ("true", "1", "yes")
    USE_WORKERS = os.getenv("USE_WORKERS", "false").lower() in ("true", "1", "yes")
    USE_GETLATE = os.getenv("USE_GETLATE", "false").lower() in ("true", "1", "yes")
    USE_AVATAR_CONFIG = os.getenv("USE_AVATAR_CONFIG", "true").lower() in ("true", "1", "yes")

    ACTIVE_AVATAR_ID = os.getenv("ACTIVE_AVATAR_ID") or os.getenv("AVATAR_ID") or "default"

    BASE_DIR = _BASE
    AVATARS_DIR = _BASE / "data" / "avatars" / ACTIVE_AVATAR_ID
    OUTPUT_DIR = _resolve_output_dir()

    GROQ_API_KEY = os.getenv("GROQ_API_KEY")
    ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")

    @classmethod
    def get_avatar_persona(cls) -> Dict[str, Any]:
        """Safely load avatar persona.json with fallback."""
        persona_path = cls.AVATARS_DIR / "persona.json"
        default_persona = {
            "name": "Default Avatar",
            "tone": "professional",
            "voice_id": "21m00Tcm4TlvDq8ikWAM",
            "style": "review",
            "affiliate_links": {}
        }

        if persona_path.exists():
            try:
                import json
                with open(persona_path, "r", encoding="utf-8") as f:
                    persona = json.load(f)
                return {**default_persona, **persona}
            except Exception:
                pass

        return default_persona

    @classmethod
    def is_direct_mode(cls) -> bool:
        """True if running in simple direct execution mode."""
        return not cls.USE_REDIS and not cls.USE_WORKERS


config = Config()

USE_REDIS = Config.USE_REDIS
USE_GETLATE = Config.USE_GETLATE
is_direct_mode = Config.is_direct_mode

BASE_DIR = Config.BASE_DIR
OUTPUT_DIR = Config.OUTPUT_DIR
