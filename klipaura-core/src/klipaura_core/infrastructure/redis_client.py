"""
Compatibility shim: re-exports redis_client symbols from the real infrastructure/ package.

Consumers (klip-avatar/core_v1 influencer_engine modules) import:
    from klipaura_core.infrastructure.redis_client import get_redis_client
    from klipaura_core.infrastructure.redis_client import RedisConfigError
    from klipaura_core.infrastructure.redis_client import RedisClient  (alias)
    from klipaura_core.infrastructure.redis_client import pre_flight_redis_connectivity
"""

from __future__ import annotations

import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
_INFRA = os.path.join(_REPO_ROOT, "infrastructure")
if os.path.isdir(_INFRA) and _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from infrastructure.redis_client import (  # noqa: E402
    LocalRedis,
    RedisConfigError,
    UpstashRedis,
    get_redis_client,
    get_redis_client_optional,
)

RedisClient = get_redis_client


def pre_flight_redis_connectivity(*, timeout_sec: float = 10.0) -> bool:
    """Quick check that Redis is reachable.  Returns True on success, raises RedisConfigError on failure."""
    try:
        r = get_redis_client()
        r.set("klipaura:preflight", "1")
        return True
    except Exception as e:
        raise RedisConfigError(f"Redis preflight failed: {e}") from e


__all__ = [
    "get_redis_client",
    "get_redis_client_optional",
    "RedisConfigError",
    "RedisClient",
    "pre_flight_redis_connectivity",
    "UpstashRedis",
    "LocalRedis",
]
