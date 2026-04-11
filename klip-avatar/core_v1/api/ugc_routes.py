"""
UGC URL pipeline — threaded run, in-memory job status (same behavior as legacy dashboard/ugc_api).
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services.job_store import (
    acquire_generation_slot,
    create_job,
    get_job,
    release_generation_slot,
    update_job,
)

router = APIRouter(tags=["ugc"])

CORE_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_REL = Path("outputs") / "final_publish" / "FINAL_VIDEO.mp4"


class UGCRequest(BaseModel):
    product_url: str


def _run_pipeline(job_id: str, url: str) -> None:
    script = CORE_ROOT / "pipeline" / "ugc_pipeline.py"
    env = os.environ.copy()
    env["UGC_PRODUCT_URL"] = (url or "").strip()
    try:
        update_job(job_id, status="processing", log="")
        proc = subprocess.run(
            [sys.executable, str(script)],
            cwd=str(CORE_ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=7200,
        )
        tail = ((proc.stdout or "") + "\n" + (proc.stderr or ""))[-4000:]
        out_path = CORE_ROOT / OUTPUT_REL
        ok = proc.returncode == 0 and out_path.is_file()
        if ok:
            update_job(
                job_id,
                status="completed",
                log=tail,
                output=str(out_path.resolve()),
            )
        else:
            update_job(
                job_id,
                status="failed",
                log=tail or f"exit_code={proc.returncode}",
            )
    except subprocess.TimeoutExpired:
        update_job(job_id, status="failed", log="Pipeline timeout (7200s)")
    except Exception as e:
        update_job(job_id, status="failed", log=str(e))
    finally:
        release_generation_slot()


@router.post("/api/ugc/generate")
def generate_ugc(req: UGCRequest) -> dict:
    u = (req.product_url or "").strip()
    if not u.startswith("http"):
        raise HTTPException(status_code=400, detail="Invalid URL")
    if not acquire_generation_slot():
        raise HTTPException(status_code=429, detail="A generation is already running")

    job_id = create_job()
    t = threading.Thread(target=_run_pipeline, args=(job_id, u), daemon=True)
    try:
        t.start()
    except Exception:
        release_generation_slot()
        raise
    return {"job_id": job_id, "status": "started"}


@router.get("/api/ugc/status/{job_id}")
def job_status(job_id: str) -> dict:
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job
