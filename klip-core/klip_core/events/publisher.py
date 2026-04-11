"""
KLIP-CORE Event Publisher
=========================
Publish events to the Master Mission Control.
Uses HTTP POST to the MC API or Redis pub/sub.
"""

from __future__ import annotations
import json
import os
import asyncio
from datetime import datetime
from typing import Optional, Dict, Any
from enum import Enum
import uuid

from ..config import get_settings
from ..redis.client import get_redis_client_optional
from ..redis.queues import QUEUE_NAMES


class EventSeverity(str, Enum):
    DEBUG = "debug"
    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


# ─── Module Detection ─────────────────────────────────────────────────────────

def _get_module_name() -> str:
    """Get the current module name from environment."""
    return os.getenv("MODULE_NAME", "unknown")


# ─── Event Publishing ─────────────────────────────────────────────────────────

async def publish_event(
    module: str,
    event_type: str,
    severity: str,
    message: str,
    data: Optional[Dict[str, Any]] = None,
    job_id: Optional[str] = None,
) -> bool:
    """
    Publish an event to the Master Mission Control.
    
    This is the main event publishing function. Use it in all modules.
    
    Args:
        module: The module name (scanner, selector, avatar, funnel, aventure, trader, system)
        event_type: What happened (e.g., "job_completed", "scan_started")
        severity: debug, info, success, warning, error, critical
        message: Human-readable summary
        data: Structured data payload
        job_id: Optional job reference
    
    Returns:
        True if published successfully, False otherwise
    
    Example:
        await publish_event(
            module="avatar",
            event_type="job_completed",
            severity="success",
            message="Video completed for product 'AI Writer Pro'",
            data={"job_id": "123", "product": "AI Writer Pro", "video_url": "..."},
            job_id="123"
        )

    **Provider usage / credits (Mission Control):** use ``event_type="provider_usage"``
    with ``data`` containing ``provider``, ``operation``, ``amount_usd`` (and optional
    ``estimate``, ``units``, ``avatar_id``), or ``data["provider_usage"]`` as a list of
    those dicts. MC ingest appends rows to ``cost_events_store`` (JSONL) for the Credits UI.
    """
    settings = get_settings()
    data = data or {}
    
    event = {
        "id": str(uuid.uuid4()),
        "timestamp": datetime.utcnow().isoformat(),
        "module": module,
        "event_type": event_type,
        "severity": severity,
        "message": message,
        "data": data,
        "job_id": job_id,
    }
    
    # Primary: POST to Mission Control (persists to Redis stream + log inside MC)
    mc_url = (os.getenv("MASTER_MC_URL") or settings.master_mc_url or "").rstrip("/")
    
    try:
        import httpx
        if mc_url:
            headers: Dict[str, str] = {}
            svc = (os.getenv("MC_SERVICE_TOKEN") or "").strip()
            ev = (os.getenv("MC_EVENTS_INGEST_SECRET") or "").strip()
            if ev:
                headers["X-Events-Token"] = ev
            elif svc:
                headers["X-Service-Token"] = svc
            async with httpx.AsyncClient(timeout=2.0) as client:
                response = await client.post(
                    f"{mc_url}/api/events/ingest",
                    json=event,
                    headers=headers or None,
                )
                if response.status_code == 200:
                    return True
    except Exception:
        pass
    
    # Fallback: mirror Mission Control ingest (ring log + Redis Stream for SSE)
    try:
        redis = get_redis_client_optional()
        if redis:
            raw = json.dumps(event)
            redis.lpush(QUEUE_NAMES.events_log, raw)
            redis.ltrim(QUEUE_NAMES.events_log, 0, 99)
            if hasattr(redis, "xadd"):
                try:
                    redis.xadd(QUEUE_NAMES.events_stream, {"data": raw})
                except Exception:
                    pass
            return True
    except Exception:
        pass
    
    # Last resort: Print to stdout (for local debugging)
    print(f"[EVENT] {module}/{event_type}: {message}")
    return False


def publish_event_sync(
    module: str,
    event_type: str,
    severity: str,
    message: str,
    data: Optional[Dict[str, Any]] = None,
    job_id: Optional[str] = None,
) -> bool:
    """
    Synchronous version of publish_event.
    Use this when you can't use async.
    """
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Create a new task
            asyncio.create_task(
                publish_event(module, event_type, severity, message, data, job_id)
            )
            return True
        else:
            return loop.run_until_complete(
                publish_event(module, event_type, severity, message, data, job_id)
            )
    except RuntimeError:
        # No event loop, create one
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(
                publish_event(module, event_type, severity, message, data, job_id)
            )
        finally:
            loop.close()


def emit_event(*args, **kwargs) -> bool:
    """
    Short alias for publish_event_sync.
    Use this for simple event emission.
    
    Example:
        emit_event("avatar", "job_completed", "success", "Video done!")
    """
    return publish_event_sync(*args, **kwargs)


# ─── Convenience Functions ─────────────────────────────────────────────────────

async def event_debug(module: str, message: str, **kwargs) -> bool:
    """Publish a debug event."""
    return await publish_event(module, f"{module}_debug", "debug", message, kwargs)


async def event_info(module: str, message: str, **kwargs) -> bool:
    """Publish an info event."""
    return await publish_event(module, f"{module}_info", "info", message, kwargs)


async def event_success(module: str, message: str, **kwargs) -> bool:
    """Publish a success event."""
    return await publish_event(module, f"{module}_completed", "success", message, kwargs)


async def event_warning(module: str, message: str, **kwargs) -> bool:
    """Publish a warning event."""
    return await publish_event(module, f"{module}_warning", "warning", message, kwargs)


async def event_error(module: str, message: str, **kwargs) -> bool:
    """Publish an error event."""
    return await publish_event(module, f"{module}_error", "error", message, kwargs)


# ─── Module-Specific Shortcuts ────────────────────────────────────────────────

async def avatar_event(event_type: str, message: str, **kwargs) -> bool:
    """Publish an avatar event."""
    return await publish_event("avatar", event_type, kwargs.get("severity", "info"), message, kwargs)


async def scanner_event(event_type: str, message: str, **kwargs) -> bool:
    """Publish a scanner event."""
    return await publish_event("scanner", event_type, kwargs.get("severity", "info"), message, kwargs)


async def selector_event(event_type: str, message: str, **kwargs) -> bool:
    """Publish a selector event."""
    return await publish_event("selector", event_type, kwargs.get("severity", "info"), message, kwargs)


async def funnel_event(event_type: str, message: str, **kwargs) -> bool:
    """Publish a funnel event."""
    return await publish_event("funnel", event_type, kwargs.get("severity", "info"), message, kwargs)


async def aventure_event(event_type: str, message: str, **kwargs) -> bool:
    """Publish an aventure event."""
    return await publish_event("aventure", event_type, kwargs.get("severity", "info"), message, kwargs)


async def system_event(event_type: str, message: str, **kwargs) -> bool:
    """Publish a system event."""
    return await publish_event("system", event_type, kwargs.get("severity", "info"), message, kwargs)
