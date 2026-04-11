"""
KLIP-CORE Redis Client
======================
Upstash + Local Redis support with automatic detection.
Extracted and enhanced from KLIPAURA infrastructure.
"""

from __future__ import annotations

import json
import os
import typing as t
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional, Union

# =============================================================================
# Constants
# =============================================================================

REDIS_NAMESPACE = "klipaura:"


class RedisConfigError(RuntimeError):
    """Raised when Redis configuration is invalid or missing."""
    pass


# =============================================================================
# Configuration Loading
# =============================================================================

def _find_repo_root() -> Path:
    """Find repository root."""
    current = Path(__file__).resolve().parent
    for _ in range(10):
        if any((current / m).exists() for m in ["CLAUDE.md", ".git", ".env"]):
            return current
        parent = current.parent
        if parent == current:
            break
        current = parent
    return Path.cwd()


def _load_dotenv() -> None:
    """Load .env file."""
    env_path = _find_repo_root() / ".env"
    if env_path.exists():
        try:
            from dotenv import load_dotenv
            load_dotenv(env_path, override=False)
        except ImportError:
            pass


def _config_from_env() -> Optional[dict]:
    """Get Redis config from environment variables."""
    _load_dotenv()
    
    use_upstash = (os.environ.get("USE_UPSTASH") or "true").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    
    # Check for local / hosted TCP Redis (includes Upstash TLS: rediss://)
    url = os.environ.get("REDIS_URL", "").strip()
    if url and (url.startswith("redis://") or url.startswith("rediss://")):
        return {"local_redis_url": url}
    
    # Check for Upstash
    upstash_url = os.environ.get("UPSTASH_REDIS_REST_URL", "").strip()
    upstash_token = os.environ.get("UPSTASH_REDIS_REST_TOKEN", "").strip()
    
    if use_upstash and upstash_url and upstash_token:
        return {
            "upstash_url": upstash_url.rstrip("/"),
            "upstash_token": upstash_token,
        }
    
    return None


def _load_config() -> dict:
    """Load Redis config from env or config file."""
    env_config = _config_from_env()
    if env_config:
        return env_config
    
    # Try config.json
    config_paths = [
        _find_repo_root() / "config.json",
        _find_repo_root().parent / "config.json",
    ]
    
    for path in config_paths:
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                pass
    
    raise RedisConfigError(
        "Redis config not found. Set REDIS_URL=redis:// or rediss:// (TCP) or "
        "UPSTASH_REDIS_REST_URL + UPSTASH_REDIS_REST_TOKEN in .env"
    )


def _extract_upstash_credentials(config: dict) -> tuple[str, str]:
    """Extract Upstash credentials from config."""
    if "upstash" in config:
        return (
            config["upstash"]["redis_rest_url"].rstrip("/"),
            config["upstash"]["redis_rest_token"],
        )
    if "upstash_url" in config:
        return config["upstash_url"].rstrip("/"), config["upstash_token"]
    
    raise RedisConfigError("Upstash credentials not found in config")


# =============================================================================
# Upstash Redis Client
# =============================================================================

class UpstashRedis:
    """
    Upstash Redis REST API client.
    Works with Upstash's REST API for serverless environments.
    """
    
    def __init__(
        self,
        redis_url: Optional[str] = None,
        redis_token: Optional[str] = None,
        config: Optional[dict] = None,
        prefix: str = "",
    ) -> None:
        if not (redis_url and redis_token):
            config = config or _load_config()
            redis_url, redis_token = _extract_upstash_credentials(config)
        
        self._redis_url = redis_url.rstrip("/")
        self._redis_token = redis_token
        self._prefix = REDIS_NAMESPACE + prefix if REDIS_NAMESPACE else prefix
    
    def _key(self, key: str) -> str:
        # Allow callers to pass full logical keys (e.g. QUEUE_NAMES.* already includes ``klipaura:``).
        if key.startswith("klipaura:"):
            return key
        return self._prefix + key if self._prefix else key
    
    def _command(self, *parts: t.Union[str, int, float]) -> t.Any:
        """Execute a Redis command via REST API."""
        if not parts:
            return None
        
        path_segments = [urllib.parse.quote(str(parts[0]).lower(), safe="")]
        path_segments += [urllib.parse.quote(str(p), safe="") for p in parts[1:]]
        url = f"{self._redis_url}/" + "/".join(path_segments)
        
        req = urllib.request.Request(
            url,
            method="POST",
            headers={
                "Authorization": f"Bearer {self._redis_token}",
                "Content-Type": "application/json",
            },
        )
        
        for attempt in range(2):
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    payload = json.loads(resp.read())
                return payload.get("result", None)
            except (urllib.error.HTTPError, OSError, TimeoutError):
                if attempt == 0:
                    continue
                return None
            except Exception:
                return None
        return None
    
    # ─── Basic Operations ────────────────────────────────────────────────────
    
    def get(self, key: str) -> Optional[str]:
        return self._command("GET", self._key(key))
    
    def set(self, key: str, value: str) -> bool:
        result = self._command("SET", self._key(key), value)
        return result == "OK"
    
    def setex(self, key: str, value: str, seconds: int) -> bool:
        result = self._command("SET", self._key(key), value, "EX", int(seconds))
        return result == "OK"
    
    def delete(self, key: str) -> int:
        result = self._command("DEL", self._key(key))
        return int(result or 0)
    
    def exists(self, key: str) -> bool:
        result = self._command("EXISTS", self._key(key))
        return bool(result)
    
    def expire(self, key: str, seconds: int) -> bool:
        result = self._command("EXPIRE", self._key(key), int(seconds))
        return bool(result)
    
    def setnx(self, key: str, value: str) -> int:
        result = self._command("SETNX", self._key(key), value)
        return int(result or 0)
    
    def incr(self, key: str) -> int:
        result = self._command("INCR", self._key(key))
        return int(result or 0)
    
    # ─── List Operations ─────────────────────────────────────────────────────
    
    def lpush(self, key: str, *values: str) -> int:
        result = self._command("LPUSH", self._key(key), *values)
        return int(result or 0)
    
    def rpush(self, key: str, *values: str) -> int:
        result = self._command("RPUSH", self._key(key), *values)
        return int(result or 0)
    
    def lpop(self, key: str) -> Optional[str]:
        return self._command("LPOP", self._key(key))
    
    def blpop(self, keys: list[str], timeout: int = 0) -> Optional[tuple[str, str]]:
        """
        Upstash REST does not support blocking pop natively.
        Use polling via lpop() in a loop, or switch to direct Redis with USE_UPSTASH=false.
        """
        raise NotImplementedError(
            "blpop() is not supported on UpstashRedis. "
            "Set USE_UPSTASH=false to use LocalRedis with blocking support, "
            "or replace the worker's blpop call with a polling loop."
        )
    
    def rpop(self, key: str) -> Optional[str]:
        return self._command("RPOP", self._key(key))
    
    def lrange(self, key: str, start: int, end: int) -> list[str]:
        result = self._command("LRANGE", self._key(key), start, end)
        return list(result or [])
    
    def llen(self, key: str) -> int:
        result = self._command("LLEN", self._key(key))
        return int(result or 0)
    
    def ltrim(self, key: str, start: int, end: int) -> bool:
        result = self._command("LTRIM", self._key(key), start, end)
        return result == "OK"
    
    def xadd(self, key: str, fields: dict[str, str]) -> Optional[str]:
        """Append to a Redis Stream (``*`` auto ID). Used for MC SSE + ingest parity."""
        if not fields:
            return None
        parts: list[t.Union[str, int]] = [self._key(key), "*"]
        for fk, fv in fields.items():
            parts.extend([str(fk), str(fv)])
        return self._command("XADD", *parts)
    
    def lrem(self, key: str, count: int, value: str) -> int:
        result = self._command("LREM", self._key(key), int(count), value)
        return int(result or 0)
    
    # ─── Set Operations ──────────────────────────────────────────────────────
    
    def sadd(self, key: str, *members: str) -> int:
        result = self._command("SADD", self._key(key), *members)
        return int(result or 0)
    
    def sismember(self, key: str, member: str) -> bool:
        result = self._command("SISMEMBER", self._key(key), member)
        return bool(result)
    
    def smembers(self, key: str) -> list[str]:
        result = self._command("SMEMBERS", self._key(key))
        return list(result or [])
    
    def scard(self, key: str) -> int:
        result = self._command("SCARD", self._key(key))
        return int(result or 0)
    
    # ─── Utility ────────────────────────────────────────────────────────────
    
    def ping(self) -> bool:
        result = self._command("PING")
        return str(result).upper() == "PONG"
    
    def set_json(self, key: str, obj: t.Mapping[str, t.Any]) -> bool:
        return self.set(key, json.dumps(obj))
    
    def get_json(self, key: str) -> Optional[dict]:
        raw = self.get(key)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None


# =============================================================================
# Local Redis Client (using redis-py)
# =============================================================================

class LocalRedis:
    """
    Local Redis client using redis-py.
    Use for local development or self-hosted Redis.
    """
    
    def __init__(self, client: t.Any, prefix: str = "") -> None:
        self._client = client
        self._prefix = REDIS_NAMESPACE + prefix if REDIS_NAMESPACE else prefix
    
    def _key(self, key: str) -> str:
        if key.startswith("klipaura:"):
            return key
        return self._prefix + key if self._prefix else key
    
    def _cmd(self, cmd: str, *args: t.Any) -> t.Any:
        try:
            return self._client.execute_command(cmd.upper(), 
                *(self._key(a) if cmd.upper() in ("GET", "SET", "DEL", "EXISTS", "EXPIRE", 
                    "LPUSH", "RPUSH", "LRANGE", "LLEN", "LPOP", "RPOP", "LTRIM", "LREM",
                    "SADD", "SISMEMBER", "SMEMBERS", "SCARD", "SETNX", "INCR", "PING")
                    else a for a in args))
        except Exception:
            return None
    
    # ─── Basic Operations ────────────────────────────────────────────────────
    
    def get(self, key: str) -> Optional[str]:
        return self._client.get(self._key(key))
    
    def set(self, key: str, value: str) -> bool:
        return bool(self._client.set(self._key(key), value))
    
    def setex(self, key: str, value: str, seconds: int) -> bool:
        return bool(self._client.setex(self._key(key), seconds, value))
    
    def delete(self, key: str) -> int:
        return int(self._client.delete(self._key(key)) or 0)
    
    def exists(self, key: str) -> bool:
        return bool(self._client.exists(self._key(key)))
    
    def expire(self, key: str, seconds: int) -> bool:
        return bool(self._client.expire(self._key(key), seconds))
    
    def setnx(self, key: str, value: str) -> int:
        return int(self._client.setnx(self._key(key), value) or 0)
    
    def incr(self, key: str) -> int:
        return int(self._client.incr(self._key(key)) or 0)
    
    # ─── List Operations ─────────────────────────────────────────────────────
    
    def lpush(self, key: str, *values: str) -> int:
        return int(self._client.lpush(self._key(key), *values) or 0)
    
    def rpush(self, key: str, *values: str) -> int:
        return int(self._client.rpush(self._key(key), *values) or 0)
    
    def lpop(self, key: str) -> Optional[str]:
        return self._client.lpop(self._key(key))
    
    def blpop(self, keys: list[str], timeout: int = 0) -> Optional[tuple[str, str]]:
        """Blocking left-pop from the first non-empty list. Pass ``QUEUE_NAMES.*`` or suffix keys; ``_key`` avoids double-prefix."""
        if not keys:
            return None
        prefixed = [self._key(k) for k in keys]
        out = self._client.blpop(prefixed, timeout=timeout)
        if not out:
            return None
        k, v = out[0], out[1]
        return (str(k), str(v))
    
    def rpop(self, key: str) -> Optional[str]:
        return self._client.rpop(self._key(key))
    
    def lrange(self, key: str, start: int, end: int) -> list[str]:
        return list(self._client.lrange(self._key(key), start, end) or [])
    
    def llen(self, key: str) -> int:
        return int(self._client.llen(self._key(key)) or 0)
    
    def ltrim(self, key: str, start: int, end: int) -> bool:
        return bool(self._client.ltrim(self._key(key), start, end))
    
    def xadd(self, key: str, fields: dict[str, str]) -> Optional[str]:
        """Append to a Redis Stream (``*`` auto ID)."""
        if not fields:
            return None
        try:
            return str(self._client.xadd(self._key(key), fields))  # type: ignore[attr-defined]
        except Exception:
            return None
    
    def lrem(self, key: str, count: int, value: str) -> int:
        return int(self._client.lrem(self._key(key), int(count), value) or 0)
    
    # ─── Set Operations ──────────────────────────────────────────────────────
    
    def sadd(self, key: str, *members: str) -> int:
        return int(self._client.sadd(self._key(key), *members) or 0)
    
    def sismember(self, key: str, member: str) -> bool:
        return bool(self._client.sismember(self._key(key), member))
    
    def smembers(self, key: str) -> list[str]:
        return list(self._client.smembers(self._key(key)) or [])
    
    def scard(self, key: str) -> int:
        return int(self._client.scard(self._key(key)) or 0)
    
    # ─── Utility ────────────────────────────────────────────────────────────
    
    def ping(self) -> bool:
        try:
            return self._client.ping()
        except Exception:
            return False
    
    def set_json(self, key: str, obj: t.Mapping[str, t.Any]) -> bool:
        return self.set(key, json.dumps(obj))
    
    def get_json(self, key: str) -> Optional[dict]:
        raw = self.get(key)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None


# =============================================================================
# Factory Functions
# =============================================================================

def get_redis_client(prefix: str = "") -> Union[UpstashRedis, LocalRedis]:
    """
    Get a Redis client. Auto-detects Upstash vs local.
    
    Usage:
        r = get_redis_client()
        r.set("key", "value")
        value = r.get("key")
    """
    config = _load_config()
    
    if config.get("local_redis_url"):
        try:
            import redis as redis_py
            client = redis_py.from_url(config["local_redis_url"], decode_responses=True)
            return LocalRedis(client, prefix=prefix)
        except ImportError:
            raise RedisConfigError(
                "Local Redis requested but redis package not installed. "
                "Run: pip install redis"
            )
    
    redis_url, redis_token = _extract_upstash_credentials(config)
    return UpstashRedis(redis_url=redis_url, redis_token=redis_token, prefix=prefix)


def get_redis_client_optional(prefix: str = "") -> Optional[Union[UpstashRedis, LocalRedis]]:
    """
    Get Redis client if available, return None on error.
    Use this when Redis is optional.
    """
    try:
        return get_redis_client(prefix=prefix)
    except RedisConfigError:
        return None
