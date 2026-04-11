"""
KLIP-CORE Redis Module
======================
Shared Redis client supporting both Upstash (cloud) and local Redis.
"""

from .client import (
    get_redis_client,
    get_redis_client_optional,
    RedisConfigError,
    UpstashRedis,
    LocalRedis,
    REDIS_NAMESPACE,
)
from .queues import QUEUE_NAMES, QueueNames

__all__ = [
    "get_redis_client",
    "get_redis_client_optional",
    "RedisConfigError",
    "UpstashRedis",
    "LocalRedis",
    "REDIS_NAMESPACE",
    "QUEUE_NAMES",
    "QueueNames",
]
