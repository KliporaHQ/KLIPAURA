"""
KLIP-CORE — Shared Library for KLIPAURA OS
==========================================

This package provides common utilities for all KLIPAURA modules:
- Redis client (Upstash + local)
- Event publishing
- Kill switch checking
- Structured logging
- Configuration management

USAGE:
    from klip_core import is_killed, publish_event, redis_get, redis_set

VERSION: 1.0.0
"""

__version__ = "1.0.0"
__author__ = "KLIPAURA"

# Core utilities
from .config import get_settings, Settings

# Redis utilities
from .redis.client import (
    get_redis_client,
    get_redis_client_optional,
    RedisConfigError,
)

# Event utilities
from .events.publisher import publish_event, publish_event_sync, EventSeverity
from .events.schemas import KlipEvent

# Kill switch
from .kill_switch import is_killed, trigger_kill_switch, clear_kill_switch

# Logging
from .logging.structured import get_logger, log_structured

# Storage
try:
    from .storage.r2 import (
        r2_configured,
        upload_to_r2,
        download_from_r2,
        get_r2_client,
    )
except ImportError:
    # R2 optional
    pass

# Queue names (shared constants)
from .redis.queues import QUEUE_NAMES

__all__ = [
    # Version
    "__version__",
    # Config
    "get_settings",
    "Settings",
    # Redis
    "get_redis_client",
    "get_redis_client_optional",
    "RedisConfigError",
    "QUEUE_NAMES",
    # Events
    "publish_event",
    "publish_event_sync",
    "EventSeverity",
    "KlipEvent",
    # Kill switch
    "is_killed",
    "trigger_kill_switch",
    "clear_kill_switch",
    # Logging
    "get_logger",
    "log_structured",
]
