"""
Influencer Engine — Health checks.

Service health for Mission Control / worker runtime.
"""

from __future__ import annotations

from typing import Any, Dict, List

SERVICE_ID = "influencer_engine"


def health_check() -> Dict[str, Any]:
    """Return service health: status, version, checks."""
    checks = dependency_checks()
    all_ok = all(c.get("ok") for c in checks.values())
    return {
        "service": SERVICE_ID,
        "status": "healthy" if all_ok else "degraded",
        "checks": checks,
    }


def dependency_checks() -> Dict[str, Dict[str, Any]]:
    """Check Redis, optional storage, etc."""
    checks: Dict[str, Dict[str, Any]] = {}
    # Redis
    try:
        from klipaura_core.infrastructure.redis_client import get_redis_client
        r = get_redis_client()
        r.ping()
        checks["redis"] = {"ok": True}
    except Exception as e:
        checks["redis"] = {"ok": False, "error": str(e)}
    # Asset store (filesystem fallback always ok)
    checks["asset_store"] = {"ok": True}
    return checks
