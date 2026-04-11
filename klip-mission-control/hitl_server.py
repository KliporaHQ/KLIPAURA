from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel

from job_state_mc import JOBS_DIR, list_recent_job_summaries, read_manifest, update_manifest
from klip_mc.security import (
    create_access_token,
    login_password_configured,
    require_mc_operator,
    verify_login_password,
    verify_login_user,
)
from klip_avatar.publisher import publish_job
from klip_core.redis.client import get_redis_client, get_redis_client_optional
from klip_core.redis.queues import QUEUE_NAMES

app = FastAPI(title="KLIPAURA HITL")


class LoginBody(BaseModel):
    username: str = "admin"
    password: str


JOBS_PENDING = QUEUE_NAMES.jobs_pending
HITL_PENDING = QUEUE_NAMES.hitl_pending
DLQ = QUEUE_NAMES.dlq
QUEUE_GLOBAL_PAUSED_KEY = "klipaura:queue:paused"
JOBS_PAUSED = "klipaura:jobs:paused"
BLACKLIST_PREFIX = "klipaura:blacklist:"


def _redis():
    r = get_redis_client_optional()
    if r is not None:
        return r
    return get_redis_client()


def _decode_json(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def _video_url_for_job(job_id: str, payload: dict[str, Any], manifest: dict[str, Any]) -> str | None:
    for candidate in (
        manifest.get("r2_url"),
        payload.get("r2_url"),
        payload.get("video_url"),
        payload.get("public_video_url"),
    ):
        if isinstance(candidate, str) and candidate.strip().startswith("http"):
            return candidate.strip()
    final_path = (manifest.get("final_video_path") or payload.get("final_video_path") or "").strip()
    if final_path:
        p = Path(final_path)
        if p.is_file():
            return f"/api/video/{job_id}"
    return None


def _safe_publish_result(result: dict[str, Any]) -> dict[str, Any]:
    out = {k: v for k, v in result.items() if k != "response"}
    resp = result.get("response")
    try:
        if isinstance(resp, dict) and len(json.dumps(resp)) > 12000:
            out["response"] = {"_truncated": True}
        else:
            out["response"] = resp
    except Exception:
        out["response"] = {"_truncated": True}
    return out


@app.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True, "service": "hitl_server", "jobs_dir": str(JOBS_DIR)}


@app.post("/api/auth/login")
def auth_login(body: LoginBody) -> dict[str, Any]:
    if not login_password_configured():
        raise HTTPException(
            status_code=503,
            detail="Server misconfigured: set MC_ADMIN_PASSWORD or ADMIN_PASSWORD in the environment",
        )
    if not verify_login_user(body.username) or not verify_login_password(body.password):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    return {"access_token": create_access_token(subject=body.username), "token_type": "bearer"}


@app.get("/", response_class=HTMLResponse)
def dashboard() -> str:
    return """<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>KLIPAURA HITL</title>
  <style>
    :root {
      color-scheme: dark;
      --klipaura-primary: #6670f4;
      --klipaura-soft: #a4b8fc;
      --surface: #0f172a;
    }
    body { margin: 0; font-family: Inter, Segoe UI, Arial, sans-serif; background: linear-gradient(135deg, #0f172a 0%, #1e293b 45%, #0f172a 100%); color: #e8ecf4; }
    .wrap { max-width: 1180px; margin: 24px auto; padding: 0 16px; }
    .card { background: rgba(255,255,255,0.06); border: 1px solid rgba(255,255,255,0.14); backdrop-filter: blur(10px); border-radius: 16px; padding: 16px; }
    .grid { display: grid; grid-template-columns: 360px 1fr; gap: 16px; }
    .player { width: 100%; max-width: 360px; aspect-ratio: 9/16; border-radius: 12px; background: #090c17; border: 1px solid rgba(255,255,255,0.2); }
    .row { display: flex; gap: 10px; flex-wrap: wrap; margin-top: 12px; }
    button { border: 0; border-radius: 10px; padding: 10px 14px; font-weight: 700; cursor: pointer; }
    .approve { background: #1f9d55; color: #fff; }
    .reject { background: #d64545; color: #fff; }
    .regen { background: var(--klipaura-primary); color: #fff; box-shadow: 0 0 20px rgba(102, 112, 244, 0.25); }
    .next { background: #6b7280; color: #fff; }
    pre { background: rgba(0,0,0,0.3); border-radius: 10px; padding: 10px; white-space: pre-wrap; word-wrap: break-word; }
    table { width: 100%; border-collapse: collapse; font-size: 13px; }
    th, td { border-bottom: 1px solid rgba(255,255,255,0.12); padding: 8px; text-align: left; }
  </style>
</head>
<body>
  <div class="wrap">
    <h2>KLIPAURA HITL Dashboard</h2>
    <div class="grid">
      <div class="card">
        <video id="v" class="player" controls playsinline></video>
        <div class="row">
          <button class="approve" onclick="act('approve')">APPROVE</button>
          <button class="reject" onclick="act('reject')">REJECT</button>
          <button class="regen" onclick="act('regenerate')">REGENERATE</button>
          <button class="next" onclick="loadNext()">NEXT JOB</button>
        </div>
      </div>
      <div class="card">
        <h3 style="margin-top:0">Current Job</h3>
        <pre id="meta">Loading...</pre>
        <h3>Recent Jobs</h3>
        <table id="recent"><thead><tr><th>Job</th><th>Status</th><th>Updated</th><th>Video</th><th>Error</th></tr></thead><tbody></tbody></table>
      </div>
    </div>
  </div>
  <script>
    let current = null;
    async function j(url, opts){ const r = await fetch(url, opts); const d = await r.json().catch(() => ({})); if(!r.ok){ throw new Error(d.detail || d.error || r.statusText); } return d; }
    function setMeta(obj){ document.getElementById('meta').textContent = JSON.stringify(obj, null, 2); }
    async function loadRecent(){
      const data = await j('/api/recent-jobs?limit=20');
      const tb = document.querySelector('#recent tbody');
      tb.innerHTML = '';
      for (const row of (data.jobs || [])){
        const tr = document.createElement('tr');
        tr.innerHTML = `<td>${row.job_id || ''}</td><td>${row.status || ''}</td><td>${row.updated_at || ''}</td><td>${row.has_video ? 'yes' : 'no'}</td><td>${row.error || ''}</td>`;
        tb.appendChild(tr);
      }
    }
    async function loadNext(){
      try{
        const data = await j('/api/next-job');
        current = data.job || null;
        const v = document.getElementById('v');
        if(current && current.video_url){ v.src = current.video_url; } else { v.removeAttribute('src'); v.load(); }
        setMeta(data);
      }catch(e){
        setMeta({error: String(e)});
      }
      await loadRecent();
    }
    async function act(kind){
      if(!current || !current.job_id){ alert('No current job loaded'); return; }
      try{
        const d = await j(`/api/${kind}/${current.job_id}`, {method:'POST'});
        setMeta(d);
        await loadRecent();
      }catch(e){
        setMeta({error: String(e)});
      }
    }
    loadNext();
    setInterval(loadRecent, 15000);
  </script>
</body>
</html>"""


@app.get("/api/recent-jobs")
def recent_jobs(limit: int = 20) -> dict[str, Any]:
    return {"jobs": list_recent_job_summaries(limit=max(1, min(200, int(limit))))}


@app.get("/api/video/{job_id}")
def stream_video(job_id: str):
    try:
        m = read_manifest(job_id)
    except Exception:
        raise HTTPException(status_code=404, detail="job not found")
    p = Path((m.get("final_video_path") or "").strip())
    if not p.is_file():
        raise HTTPException(status_code=404, detail="video not found")
    return FileResponse(str(p), media_type="video/mp4", filename=f"{job_id}.mp4")


@app.get("/api/next-job")
def next_job() -> dict[str, Any]:
    r = _redis()
    raw = r.lpop(HITL_PENDING)
    if not raw:
        return {"ok": True, "job": None}
    payload = _decode_json(raw)
    job_id = str(payload.get("job_id") or "").strip()
    if not job_id:
        return {"ok": False, "error": "invalid HITL payload, missing job_id"}
    try:
        manifest = read_manifest(job_id)
    except Exception:
        manifest = {"job_id": job_id, "payload": payload}
    video_url = _video_url_for_job(job_id, payload, manifest)
    return {
        "ok": True,
        "job": {
            "job_id": job_id,
            "payload": payload,
            "manifest": manifest,
            "video_url": video_url,
        },
    }


@app.post("/api/approve/{job_id}", dependencies=[Depends(require_mc_operator)])
def approve(job_id: str) -> dict[str, Any]:
    try:
        m = read_manifest(job_id)
    except Exception:
        raise HTTPException(status_code=404, detail="job not found")

    payload = m.get("payload") or {}
    avatar_id = (payload.get("avatar_id") or "theanikaglow").strip()
    product_url = (payload.get("product_url") or "").strip()
    r2 = (m.get("r2_url") or "").strip()
    final_path = (m.get("final_video_path") or "").strip()

    update_manifest(job_id, status="PUBLISHING_QUEUED")
    result = publish_job(
        avatar_id,
        job_id,
        r2,
        title=(payload.get("product_title") or "UGC"),
        description=product_url[:4000],
        final_video_path=final_path or None,
        product_url=product_url,
        payload_avatar_id=str(payload.get("avatar_id") or "") or None,
    )
    mode = result.get("publish_mode")
    if mode == "getlate" and result.get("ok"):
        final_status = "PUBLISHED"
    elif mode == "manual":
        final_status = "MANUAL_PUBLISH_REQUIRED"
    else:
        final_status = "PUBLISH_FAILED"
    safe = _safe_publish_result(result)
    update_manifest(job_id, status=final_status, publish_result=safe)
    return {"ok": True, "job_id": job_id, "result": result}


@app.post("/api/reject/{job_id}", dependencies=[Depends(require_mc_operator)])
def reject(job_id: str) -> dict[str, Any]:
    try:
        m = read_manifest(job_id)
    except Exception:
        raise HTTPException(status_code=404, detail="job not found")
    payload = m.get("payload") or {}
    product_url = (payload.get("product_url") or "").strip()
    update_manifest(job_id, status="REJECTED")
    if product_url:
        h = hashlib.sha1(product_url.encode("utf-8")).hexdigest()
        key = f"{BLACKLIST_PREFIX}{h}"
        _redis().setex(key, "1", 7 * 24 * 3600)
    return {"ok": True, "job_id": job_id, "blacklisted": bool(product_url)}


@app.post("/api/regenerate/{job_id}", dependencies=[Depends(require_mc_operator)])
def regenerate(job_id: str) -> dict[str, Any]:
    try:
        m = read_manifest(job_id)
    except Exception:
        raise HTTPException(status_code=404, detail="job not found")
    payload = m.get("payload") or {}
    if not payload:
        raise HTTPException(status_code=400, detail="manifest missing payload")
    payload["retry_count"] = 0
    _redis().lpush(JOBS_PENDING, json.dumps(payload))
    update_manifest(job_id, status="REQUEUED")
    return {"ok": True, "job_id": job_id}


@app.get("/api/scanner/status")
def scanner_status() -> JSONResponse:
    # Scanner module is not wired in this reboot phase.
    return JSONResponse({"error": "scanner not available", "detail": "klip-scanner not wired in this service"}, status_code=503)
