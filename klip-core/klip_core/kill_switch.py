"""
KLIP-CORE Kill Switch
======================
Emergency stop functionality for all modules.
"""

from __future__ import annotations
import json
import os
import asyncio
from typing import Optional

from .redis.client import get_redis_client_optional


# ─── Kill Switch Keys ─────────────────────────────────────────────────────────

def _get_kill_key(module: Optional[str] = None) -> str:
    """Get the Redis key for a module's kill switch."""
    if module:
        return f"klipaura:kill:{module}"
    return "klipaura:kill:global"


def _value_indicates_kill(val: Optional[str]) -> bool:
    """True if Redis value means the kill switch is active (JSON or legacy \"1\")."""
    if val is None:
        return False
    if val == "1":
        return True
    try:
        parsed = json.loads(val)
        if isinstance(parsed, dict):
            if parsed.get("killed") is True:
                return True
            if parsed.get("active") is True:
                return True
    except (json.JSONDecodeError, TypeError):
        pass
    return False


# ─── Check Kill Status ─────────────────────────────────────────────────────────

async def is_killed(module: Optional[str] = None) -> bool:
    """
    Check if a module is killed (emergency stop).
    
    Args:
        module: The module name to check. None = global kill switch.
    
    Returns:
        True if killed (stop everything), False otherwise.
    
    Example:
        if await is_killed("avatar"):
            return "Avatar module paused by kill switch"
    """
    redis = get_redis_client_optional()
    
    if redis is None:
        # No Redis = dev mode, never killed
        return False
    
    try:
        global_kill = redis.get(_get_kill_key(None))
        if _value_indicates_kill(global_kill):
            return True

        if module:
            module_kill = redis.get(_get_kill_key(module))
            if _value_indicates_kill(module_kill):
                return True

        return False
    except Exception:
        # Redis error = fail safe (don't kill)
        return False


def is_killed_sync(module: Optional[str] = None) -> bool:
    """Synchronous version of is_killed."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.create_task(is_killed(module))
            return False  # Assume not killed for sync calls
        return loop.run_until_complete(is_killed(module))
    except RuntimeError:
        return False


# ─── Trigger Kill Switch ──────────────────────────────────────────────────────

async def trigger_kill_switch(
    module: Optional[str] = None,
    triggered_by: str = "system",
    reason: str = "",
) -> bool:
    """
    Trigger the kill switch for a module or globally.
    
    Args:
        module: The module to kill. None = global kill.
        triggered_by: Who/what triggered the kill.
        reason: Why the kill was triggered.
    
    Returns:
        True if triggered successfully.
    
    Example:
        await trigger_kill_switch(module="avatar", triggered_by="admin", reason="Emergency maintenance")
    """
    redis = get_redis_client_optional()
    
    if redis is None:
        print(f"[KILL] Cannot trigger kill - Redis not available")
        return False
    
    try:
        key = _get_kill_key(module)
        value = json.dumps({
            "active": True,
            "triggered_by": triggered_by,
            "triggered_at": asyncio.get_event_loop().time() if asyncio.get_event_loop().is_running() else 0,
            "reason": reason,
        })
        redis.set(key, value)
        
        print(f"[KILL] Kill switch triggered for {module or 'GLOBAL'} by {triggered_by}: {reason}")
        return True
    except Exception as e:
        print(f"[KILL] Failed to trigger kill switch: {e}")
        return False


def trigger_kill_switch_sync(
    module: Optional[str] = None,
    triggered_by: str = "system",
    reason: str = "",
) -> bool:
    """Synchronous version of trigger_kill_switch."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.create_task(trigger_kill_switch(module, triggered_by, reason))
            return True
        return loop.run_until_complete(trigger_kill_switch(module, triggered_by, reason))
    except RuntimeError:
        return False


# ─── Clear Kill Switch ────────────────────────────────────────────────────────

async def clear_kill_switch(module: Optional[str] = None, cleared_by: str = "system") -> bool:
    """
    Clear the kill switch for a module or globally.
    
    Args:
        module: The module to unkill. None = global.
        cleared_by: Who cleared the kill.
    
    Returns:
        True if cleared successfully.
    
    Example:
        await clear_kill_switch(module="avatar", cleared_by="admin")
    """
    redis = get_redis_client_optional()
    
    if redis is None:
        return False
    
    try:
        key = _get_kill_key(module)
        redis.delete(key)
        print(f"[KILL] Kill switch cleared for {module or 'GLOBAL'} by {cleared_by}")
        return True
    except Exception as e:
        print(f"[KILL] Failed to clear kill switch: {e}")
        return False


def clear_kill_switch_sync(module: Optional[str] = None, cleared_by: str = "system") -> bool:
    """Synchronous version of clear_kill_switch."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.create_task(clear_kill_switch(module, cleared_by))
            return True
        return loop.run_until_complete(clear_kill_switch(module, cleared_by))
    except RuntimeError:
        return False


# ─── Get Kill Switch Status ────────────────────────────────────────────────────

async def get_kill_status(module: Optional[str] = None) -> dict:
    """
    Get detailed kill switch status.
    
    Returns:
        Dict with active, triggered_by, triggered_at, reason.
    """
    redis = get_redis_client_optional()
    
    if redis is None:
        return {"active": False, "module": module or "global"}
    
    try:
        # Check global
        global_data = redis.get_json(_get_kill_key(None))
        if global_data:
            global_data["module"] = "global"
            return global_data
        
        # Check module
        if module:
            module_data = redis.get_json(_get_kill_key(module))
            if module_data:
                module_data["module"] = module
                return module_data
        
        return {"active": False, "module": module or "global"}
    except Exception:
        return {"active": False, "module": module or "global", "error": True}
