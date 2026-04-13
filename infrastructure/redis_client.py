"""
KLIPAURA — Upstash / local Redis client.

Originally extracted from legacy `Infrastructure/redis_client.py`; `get_runtime_config` removed —
config comes from environment, optional `config.json` at repo root, and `.env`.
"""

from __future__ import annotations

import json
import os
import typing as t
import urllib.error
import urllib.parse
import urllib.request

ScriptPath = os.path.dirname(os.path.abspath(__file__))
KliporaRoot = os.path.dirname(ScriptPath)


class RedisConfigError(RuntimeError):
    pass


def _ensure_dotenv_loaded() -> None:
    try:
        from pathlib import Path

        env_path = Path(KliporaRoot) / ".env"
        if env_path.exists():
            try:
                from dotenv import load_dotenv

                load_dotenv(env_path, verbose=False)
            except ImportError:
                pass
    except Exception:
        pass


def _config_from_env() -> t.Optional[dict]:
    url = (
        os.environ.get("UPSTASH_REDIS_REST_URL")
        or os.environ.get("REDIS_URL")
        or ""
    ).strip()
    token = (
        os.environ.get("UPSTASH_REDIS_REST_TOKEN")
        or os.environ.get("REDIS_TOKEN")
        or ""
    ).strip()
    if url and (url.startswith("redis://") or url.startswith("rediss://")):
        return {"local_redis_url": url}
    if url and token:
        return {"upstash_url": url.rstrip("/"), "upstash_token": token}
    return None


def _load_config() -> dict:
    _ensure_dotenv_loaded()
    env_config = _config_from_env()
    if env_config:
        return env_config

    config_paths = [
        os.path.join(KliporaRoot, "config.json"),
        os.path.join(ScriptPath, "config.json"),
    ]

    for path in config_paths:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)

    raise RedisConfigError(
        "Redis config not found. Set REDIS_URL=redis://localhost:6379/0 for local Redis, or "
        "UPSTASH_REDIS_REST_URL and UPSTASH_REDIS_REST_TOKEN in .env or environment, or provide config.json at "
        + ", ".join(config_paths)
    )


def _extract_upstash_credentials(config: dict) -> t.Tuple[str, str]:
    if "upstash" in config:
        redis_url = config["upstash"].get("redis_rest_url", "").rstrip("/")
        redis_token = config["upstash"].get("redis_rest_token", "")
    elif "upstash_url" in config:
        redis_url = config["upstash_url"].rstrip("/")
        redis_token = config["upstash_token"]
    else:
        raise RedisConfigError("Upstash credentials not found in config")

    if not redis_url or not redis_token:
        raise RedisConfigError("Upstash URL and token must be non-empty")
    return redis_url, redis_token


REDIS_NAMESPACE = "klipaura:"


class UpstashRedis:
    def __init__(
        self,
        redis_url: t.Optional[str] = None,
        redis_token: t.Optional[str] = None,
        config: t.Optional[dict] = None,
        prefix: str = "",
    ) -> None:
        if not (redis_url and redis_token):
            config = config or _load_config()
            redis_url, redis_token = _extract_upstash_credentials(config)

        self._redis_url = redis_url.rstrip("/")
        self._redis_token = redis_token
        self._prefix = REDIS_NAMESPACE + (prefix or "") if REDIS_NAMESPACE else (prefix or "")

    def _key(self, key: str) -> str:
        if not self._prefix:
            return key
        return self._prefix + key

    def command(self, *parts: t.Union[str, int, float]) -> t.Any:
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
            except urllib.error.HTTPError as e:
                _ = e.read()
                return None
            except (OSError, TimeoutError, urllib.error.URLError):
                if attempt == 0:
                    continue
                return None
            except Exception:
                return None
        return None

    def get(self, key: str) -> t.Optional[str]:
        return self.command("GET", self._key(key))

    def set(self, key: str, value: str) -> bool:
        result = self.command("SET", self._key(key), value)
        return result == "OK"

    def setex(self, key: str, value: str, seconds: int) -> bool:
        result = self.command("SET", self._key(key), value, "EX", int(seconds))
        return result == "OK"

    def delete(self, key: str) -> int:
        result = self.command("DEL", self._key(key))
        return int(result or 0)

    def lpush(self, key: str, *values: str) -> int:
        result = self.command("LPUSH", self._key(key), *values)
        return int(result or 0)

    def rpush(self, key: str, *values: str) -> int:
        result = self.command("RPUSH", self._key(key), *values)
        return int(result or 0)

    def lrange(self, key: str, start: int, end: int) -> t.List[str]:
        result = self.command("LRANGE", self._key(key), start, end)
        return list(result or [])

    def llen(self, key: str) -> int:
        result = self.command("LLEN", self._key(key))
        return int(result or 0)

    def lpop(self, key: str) -> t.Optional[str]:
        return self.command("LPOP", self._key(key))

    def rpop(self, key: str) -> t.Optional[str]:
        return self.command("RPOP", self._key(key))

    def blpop(self, keys: list[str], timeout: int = 0) -> t.Optional[tuple[str, str]]:
        """Blocking left-pop. Returns (key, value) or None on timeout / empty (Upstash)."""
        if not keys:
            return None
        key = self._key(keys[0])
        to = max(0, int(timeout))
        result = self.command("BLPOP", key, to)
        if not result:
            return None
        if isinstance(result, (list, tuple)) and len(result) >= 2:
            return (str(result[0]), str(result[1]))
        return None

    def sadd(self, key: str, *members: str) -> int:
        result = self.command("SADD", self._key(key), *members)
        return int(result or 0)

    def srem(self, key: str, *members: str) -> int:
        result = self.command("SREM", self._key(key), *members)
        return int(result or 0)

    def sismember(self, key: str, member: str) -> bool:
        result = self.command("SISMEMBER", self._key(key), member)
        return bool(result)

    def smembers(self, key: str) -> t.List[str]:
        result = self.command("SMEMBERS", self._key(key))
        return list(result or [])

    def scard(self, key: str) -> int:
        result = self.command("SCARD", self._key(key))
        return int(result or 0)

    def incr(self, key: str) -> int:
        result = self.command("INCR", self._key(key))
        return int(result or 0)

    def setnx(self, key: str, value: str) -> int:
        result = self.command("SETNX", self._key(key), value)
        return int(result or 0)

    def exists(self, key: str) -> bool:
        result = self.command("EXISTS", self._key(key))
        return bool(result)

    def expire(self, key: str, seconds: int) -> bool:
        result = self.command("EXPIRE", self._key(key), int(seconds))
        return bool(result)

    def ltrim(self, key: str, start: int, end: int) -> bool:
        result = self.command("LTRIM", self._key(key), start, end)
        return result == "OK"

    def lrem(self, key: str, count: int, value: str) -> int:
        result = self.command("LREM", self._key(key), int(count), value)
        return int(result or 0)

    def ping(self) -> bool:
        result = self.command("PING")
        return str(result).upper() == "PONG"

    def set_json(self, key: str, obj: t.Mapping[str, t.Any]) -> bool:
        return self.set(key, json.dumps(obj))

    def get_json(self, key: str) -> t.Optional[dict]:
        raw = self.get(key)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None


def _get_local_redis_client(redis_url: str, prefix: str = "") -> t.Any:
    try:
        import redis as redis_py
    except ImportError as e:
        raise RedisConfigError(
            "Local Redis requested but redis package not installed. Run: pip install redis"
        ) from e
    raw = redis_py.from_url(redis_url, decode_responses=True)
    return LocalRedis(raw, prefix=prefix)


class LocalRedis:
    def __init__(self, client: t.Any, prefix: str = "") -> None:
        self._client = client
        self._prefix = REDIS_NAMESPACE + (prefix or "") if REDIS_NAMESPACE else (prefix or "")

    def _key(self, key: str) -> str:
        return self._prefix + key if self._prefix else key

    def command(self, *parts: t.Union[str, int, float]) -> t.Any:
        if not parts:
            return None
        cmd = str(parts[0]).upper()
        args = [str(p) for p in parts[1:]]
        if cmd in ("GET", "SET", "SETEX", "DEL", "EXISTS", "EXPIRE", "SETNX", "INCR", "KEYS"):
            if args:
                args[0] = self._key(args[0])
        elif cmd in ("LPUSH", "RPUSH", "LRANGE", "LLEN", "LPOP", "RPOP", "LTRIM", "LREM"):
            if args:
                args[0] = self._key(args[0])
        elif cmd in ("SADD", "SISMEMBER", "SMEMBERS", "SCARD"):
            if args:
                args[0] = self._key(args[0])
        try:
            return self._client.execute_command(cmd, *args)
        except Exception:
            return None

    def get(self, key: str) -> t.Optional[str]:
        return self._client.get(self._key(key))

    def set(self, key: str, value: str) -> bool:
        return bool(self._client.set(self._key(key), value))

    def setex(self, key: str, value: str, seconds: int) -> bool:
        return bool(self._client.setex(self._key(key), seconds, value))

    def delete(self, key: str) -> int:
        return int(self._client.delete(self._key(key)) or 0)

    def lpush(self, key: str, *values: str) -> int:
        return int(self._client.lpush(self._key(key), *values) or 0)

    def rpush(self, key: str, *values: str) -> int:
        return int(self._client.rpush(self._key(key), *values) or 0)

    def lrange(self, key: str, start: int, end: int) -> t.List[str]:
        return list(self._client.lrange(self._key(key), start, end) or [])

    def llen(self, key: str) -> int:
        return int(self._client.llen(self._key(key)) or 0)

    def lpop(self, key: str) -> t.Optional[str]:
        return self._client.lpop(self._key(key))

    def rpop(self, key: str) -> t.Optional[str]:
        return self._client.rpop(self._key(key))

    def blpop(self, keys: list[str], timeout: int = 0) -> t.Optional[tuple[str, str]]:
        if not keys:
            return None
        pk = [self._key(k) for k in keys]
        try:
            res = self._client.blpop(pk, timeout=timeout or None)
        except Exception:
            return None
        if not res:
            return None
        return (str(res[0]), str(res[1]))

    def sadd(self, key: str, *members: str) -> int:
        return int(self._client.sadd(self._key(key), *members) or 0)

    def srem(self, key: str, *members: str) -> int:
        return int(self._client.srem(self._key(key), *members) or 0)

    def sismember(self, key: str, member: str) -> bool:
        return bool(self._client.sismember(self._key(key), member))

    def smembers(self, key: str) -> t.List[str]:
        return list(self._client.smembers(self._key(key)) or [])

    def scard(self, key: str) -> int:
        return int(self._client.scard(self._key(key)) or 0)

    def incr(self, key: str) -> int:
        return int(self._client.incr(self._key(key)) or 0)

    def setnx(self, key: str, value: str) -> int:
        return int(self._client.setnx(self._key(key), value) or 0)

    def exists(self, key: str) -> bool:
        return bool(self._client.exists(self._key(key)))

    def expire(self, key: str, seconds: int) -> bool:
        return bool(self._client.expire(self._key(key), seconds))

    def ltrim(self, key: str, start: int, end: int) -> bool:
        return bool(self._client.ltrim(self._key(key), start, end))

    def lrem(self, key: str, count: int, value: str) -> int:
        return int(self._client.lrem(self._key(key), int(count), value) or 0)

    def ping(self) -> bool:
        try:
            return self._client.ping()
        except Exception:
            return False

    def set_json(self, key: str, obj: t.Mapping[str, t.Any]) -> bool:
        return self.set(key, json.dumps(obj))

    def get_json(self, key: str) -> t.Optional[dict]:
        raw = self.get(key)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None


def get_redis_client(prefix: str = "") -> t.Union[UpstashRedis, LocalRedis]:
    config = _load_config()
    if config.get("local_redis_url"):
        return _get_local_redis_client(config["local_redis_url"], prefix=prefix)
    redis_url, redis_token = _extract_upstash_credentials(config)
    return UpstashRedis(redis_url=redis_url, redis_token=redis_token, prefix=prefix)


def get_redis_client_optional(prefix: str = "") -> t.Optional[t.Union[UpstashRedis, LocalRedis]]:
    try:
        return get_redis_client(prefix=prefix)
    except RedisConfigError:
        return None


__all__ = [
    "UpstashRedis",
    "LocalRedis",
    "get_redis_client",
    "get_redis_client_optional",
    "RedisConfigError",
    "REDIS_NAMESPACE",
]
