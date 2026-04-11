"""Per-job manifest on disk (JOBS_DIR). Used by HITL and dispatch endpoints."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from klip_core.redis.client import get_redis_client  # noqa: F401

_REPO_ROOT = Path(__file__).resolve().parents[1]
JOBS_DIR = Path(os.getenv("JOBS_DIR", str(_REPO_ROOT / "jobs"))).resolve()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_manifest(job_id: str, payload: Mapping[str, Any]) -> None:
    d = JOBS_DIR / job_id
    d.mkdir(parents=True, exist_ok=True)
    data = {
        "job_id": job_id,
        "status": "QUEUED",
        "created_at": _now(),
        "payload": dict(payload),
        "retry_count": int(payload.get("retry_count", 0)),
        "r2_url": None,
        "final_video_path": None,
        "updated_at": _now(),
    }
    (d / "manifest.json").write_text(json.dumps(data, indent=2), encoding="utf-8")


def update_manifest(job_id: str, **kwargs: Any) -> None:
    path = JOBS_DIR / job_id / "manifest.json"
    if not path.is_file():
        create_manifest(job_id, {"retry_count": 0})
    data = json.loads(path.read_text(encoding="utf-8"))
    for k, v in kwargs.items():
        if k == "payload" and isinstance(v, dict):
            data["payload"] = {**data.get("payload", {}), **v}
        else:
            data[k] = v
    data["updated_at"] = _now()
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def read_manifest(job_id: str) -> dict[str, Any]:
    path = JOBS_DIR / job_id / "manifest.json"
    return json.loads(path.read_text(encoding="utf-8"))


def list_recent_job_summaries(limit: int = 20) -> list[dict[str, Any]]:
    """Newest-first job rows for ops dashboard (no large payloads)."""
    if not JOBS_DIR.is_dir():
        return []
    rows: list[tuple[float, dict[str, Any]]] = []
    for d in JOBS_DIR.iterdir():
        if not d.is_dir():
            continue
        path = d / "manifest.json"
        if not path.is_file():
            continue
        try:
            mtime = path.stat().st_mtime
            data = json.loads(path.read_text(encoding="utf-8"))
            fp = (data.get("final_video_path") or "").strip()
            err = (data.get("error") or "").strip()
            if len(err) > 160:
                err = err[:157] + "..."
            rows.append(
                (
                    mtime,
                    {
                        "job_id": data.get("job_id") or d.name,
                        "status": data.get("status"),
                        "updated_at": data.get("updated_at"),
                        "has_video": bool(fp and Path(fp).is_file()),
                        "error": err or None,
                    },
                )
            )
        except Exception:
            continue
    rows.sort(key=lambda x: x[0], reverse=True)
    return [r[1] for r in rows[: max(1, limit)]]
