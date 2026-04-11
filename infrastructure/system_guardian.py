"""Lightweight health signals for scheduler / ops (no external deps)."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


def health_snapshot() -> dict[str, Any]:
    out: dict[str, Any] = {"ts": time.time(), "ok": True, "checks": {}}
    try:
        from infrastructure.redis_client import get_redis_client_optional

        r = get_redis_client_optional()
        if r is None:
            out["checks"]["redis"] = {"ok": False, "reason": "not_configured"}
            out["ok"] = False
        else:
            out["checks"]["redis"] = {"ok": bool(r.ping())}
            if not out["checks"]["redis"]["ok"]:
                out["ok"] = False
    except Exception as e:
        out["checks"]["redis"] = {"ok": False, "error": str(e)[:200]}
        out["ok"] = False

    core = _REPO / "klip-avatar" / "core_v1"
    out["checks"]["core_v1"] = {"exists": core.is_dir()}
    if not core.is_dir():
        out["ok"] = False

    jobs = Path(os.getenv("JOBS_DIR", str(_REPO / "jobs")))
    out["checks"]["jobs_dir"] = {"path": str(jobs), "writable": os.access(jobs.parent, os.W_OK) if jobs.parent.is_dir() else False}
    return out


def log_guardian_event() -> None:
    snap = health_snapshot()
    line = f"[guardian] ok={snap['ok']} redis={snap['checks'].get('redis')}\n"
    print(line, flush=True)
