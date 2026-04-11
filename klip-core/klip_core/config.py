"""
KLIP-CORE Configuration
======================
Centralized environment variable loading with validation.
"""

from __future__ import annotations
import os
from pathlib import Path
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, validator
import json


# =============================================================================
# Path Setup
# =============================================================================

def _find_repo_root() -> Path:
    """Find the repository root by looking for marker files."""
    current = Path(__file__).resolve().parent
    markers = ["CLAUDE.md", ".git", "docker-compose.yml", ".env"]
    
    for _ in range(10):  # Max 10 levels up
        if any((current / marker).exists() for marker in markers):
            return current
        parent = current.parent
        if parent == current:
            break
        current = parent
    
    return Path.cwd()


REPO_ROOT = _find_repo_root()


def load_env_file(env_path: Optional[Path] = None) -> None:
    """Load .env file if it exists."""
    if env_path is None:
        env_path = REPO_ROOT / ".env"
    
    if env_path.exists():
        try:
            from dotenv import load_dotenv
            load_dotenv(env_path, override=False)
        except ImportError:
            # Manual .env loading fallback
            _load_env_manual(env_path)


def _load_env_manual(env_path: Path) -> None:
    """Manual .env loading without python-dotenv."""
    try:
        content = env_path.read_text(encoding="utf-8")
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    except Exception:
        pass


# Load .env on module import
load_env_file()


# =============================================================================
# Settings Models
# =============================================================================

class Settings(BaseSettings):
    """Main settings class. All environment variables are validated here."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )
    
    # Master MC
    master_mc_url: str = Field(
        default="http://mission-control:8000",
        description="URL of the Master Mission Control API"
    )
    master_mc_ui_url: str = Field(
        default="http://mission-control-ui:3000",
        description="URL of the Master Mission Control UI"
    )
    
    # Module URLs (can be overridden in docker-compose)
    scanner_api_url: str = Field(
        default="http://klip-scanner:8007",
        env="SCANNER_API_URL"
    )
    selector_api_url: str = Field(
        default="http://klip-selector:8001",
        env="SELECTOR_API_URL"
    )
    avatar_api_url: str = Field(
        default="http://klip-avatar:8002",
        env="AVATAR_API_URL"
    )
    avatar_mc_url: str = Field(
        default="http://avatar-mc:8011",
        env="AVATAR_MC_URL"
    )
    hitl_api_url: str = Field(
        default="http://hitl-ui:8021",
        env="HITL_API_URL"
    )
    funnel_api_url: str = Field(
        default="http://klip-funnel:8003",
        env="FUNNEL_API_URL"
    )
    aventure_api_url: str = Field(
        default="http://klip-aventure:8004",
        env="AVENTURE_API_URL"
    )
    
    # Redis
    redis_url: Optional[str] = Field(
        default=None,
        env="REDIS_URL"
    )
    upstash_redis_rest_url: Optional[str] = Field(
        default=None,
        env="UPSTASH_REDIS_REST_URL"
    )
    upstash_redis_rest_token: Optional[str] = Field(
        default=None,
        env="UPSTASH_REDIS_REST_TOKEN"
    )
    
    # Database
    database_url: Optional[str] = Field(
        default=None,
        env="DATABASE_URL"
    )
    
    # AI APIs
    groq_api_key: Optional[str] = Field(
        default=None,
        env="GROQ_API_KEY"
    )
    wavespeed_api_key: Optional[str] = Field(
        default=None,
        env="WAVESPEED_API_KEY"
    )
    elevenlabs_api_key: Optional[str] = Field(
        default=None,
        env="ELEVENLABS_API_KEY"
    )
    getlate_api_key: Optional[str] = Field(
        default=None,
        env="GETLATE_API_KEY"
    )
    
    # R2 Storage
    r2_account_id: Optional[str] = Field(
        default=None,
        env="R2_ACCOUNT_ID"
    )
    r2_access_key_id: Optional[str] = Field(
        default=None,
        env="R2_ACCESS_KEY_ID"
    )
    r2_secret_access_key: Optional[str] = Field(
        default=None,
        env="R2_SECRET_ACCESS_KEY"
    )
    r2_bucket: str = Field(
        default="klipaura",
        env="R2_BUCKET"
    )
    # S3 API endpoint (override for MinIO local or Cloudflare R2)
    r2_endpoint: Optional[str] = Field(
        default=None,
        env="R2_ENDPOINT",
        description="S3-compatible endpoint URL, e.g. https://<id>.r2.cloudflarestorage.com or http://minio:9000",
    )
    r2_public_url: Optional[str] = Field(
        default=None,
        env="R2_PUBLIC_URL",
        description="Public base URL for objects (R2 custom domain or r2.dev)",
    )
    
    # Auth
    jwt_secret: str = Field(
        default="change-this-to-secure-random-string-32chars",
        env="JWT_SECRET"
    )
    admin_user: str = Field(
        default="klipaura2026",
        env="ADMIN_USER"
    )
    admin_password: str = Field(
        default="klipaura123",
        env="ADMIN_PASSWORD"
    )
    
    # Security
    cors_origins: str = Field(
        default="http://localhost:3000",
        env="CORS_ORIGINS"
    )
    
    # Module identity
    module_name: str = Field(
        default="unknown",
        env="MODULE_NAME"
    )
    module_port: int = Field(
        default=8000,
        env="MODULE_PORT"
    )


# Singleton instance
_settings: Optional[Settings] = None


def get_settings() -> Settings:
    """Get the settings singleton."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reload_settings() -> Settings:
    """Reload settings from environment."""
    global _settings
    _settings = Settings()
    return _settings


# =============================================================================
# Config File Support (JSON)
# =============================================================================

def load_config_json(config_path: Optional[Path] = None) -> dict:
    """Load configuration from JSON file."""
    if config_path is None:
        config_path = REPO_ROOT / "config.json"
    
    if config_path.exists():
        try:
            return json.loads(config_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    
    return {}


# =============================================================================
# Environment Helpers
# =============================================================================

def is_production() -> bool:
    """Check if running in production."""
    return os.getenv("ENVIRONMENT", "development") == "production"


def is_docker() -> bool:
    """Check if running inside Docker."""
    return Path("/.dockerenv").exists() or os.getenv("DOCKER_CONTAINER", "false") == "true"


def get_module_name() -> str:
    """Get the current module name."""
    return os.getenv("MODULE_NAME", "unknown")


def get_module_url() -> str:
    """Get the URL for this module."""
    settings = get_settings()
    module = get_module_name()
    
    url_map = {
        "scanner": settings.scanner_api_url,
        "selector": settings.selector_api_url,
        "avatar": settings.avatar_api_url,
        "avatar-mc": settings.avatar_mc_url,
        "hitl": settings.hitl_api_url,
        "funnel": settings.funnel_api_url,
        "aventure": settings.aventure_api_url,
        "mc-api": settings.master_mc_url,
        "mc-ui": settings.master_mc_ui_url,
    }
    
    return url_map.get(module, f"http://localhost:{settings.module_port}")
