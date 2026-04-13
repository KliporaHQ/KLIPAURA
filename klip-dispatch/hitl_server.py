"""
FastAPI: enqueue jobs, HITL dashboard, approve/reject/regenerate.

Run from repo root:
  uvicorn hitl_server:app --app-dir klip-dispatch --host 0.0.0.0 --port 8080
"""

from __future__ import annotations

import base64
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
import os
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
_KLIP_SCANNER_ROOT = _REPO / "klip-scanner"
_KLIP_FUNNEL_ROOT = _REPO / "klip-funnel"

_TEMPLATES_PATH = _REPO / "config" / "templates.json"
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
if _KLIP_SCANNER_ROOT.is_dir() and str(_KLIP_SCANNER_ROOT) not in sys.path:
    sys.path.insert(0, str(_KLIP_SCANNER_ROOT))
if _KLIP_FUNNEL_ROOT.is_dir() and str(_KLIP_FUNNEL_ROOT) not in sys.path:
    sys.path.insert(0, str(_KLIP_FUNNEL_ROOT))
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

try:
    from dotenv import load_dotenv

    load_dotenv(_REPO / ".env", override=False)
except ImportError:
    pass

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from infrastructure.db import db_ping
from infrastructure.job_state import (
    JOBS_DIR,
    create_manifest,
    list_recent_job_summaries,
    read_manifest,
    update_manifest,
)
from infrastructure.queue_names import (
    BLACKLIST_PREFIX,
    DLQ,
    HITL_PENDING,
    JOBS_PAUSED,
    JOBS_PENDING,
    QUEUE_GLOBAL_PAUSED_KEY,
    WORKER_AVATAR_HEARTBEAT_KEY,
)
from infrastructure.redis_client import RedisConfigError, get_redis_client, get_redis_client_optional
from infrastructure.affiliate_programs import resolve_affiliate_data_for_job
from infrastructure.avatar_registry import load_avatars_config
from infrastructure.avatar_loader import AvatarLoader

from klip_scanner.csv_scanner import ingest_products_csv
from klip_scanner.discovery_agent import run_discovery_cycle
from klip_scanner.product_visuals import resolve_product_images
from klip_scanner.scanner_service import run_scanner
from publisher import publish_job


def _cors_allow_origins() -> list[str]:
    """
    Production: set ``CORS_ALLOW_ORIGINS`` to comma-separated origins, e.g.
    ``https://app.klipaura.com,https://mc.klipaura.com``.

    - ``CORS_ALLOW_ORIGINS=*`` explicitly allows all origins (not recommended for production).
    - If unset, defaults to local Next.js dev origins only (not ``*``).
    """
    raw = (os.getenv("CORS_ALLOW_ORIGINS") or "").strip()
    if raw == "*":
        return ["*"]
    if raw:
        return [x.strip() for x in raw.split(",") if x.strip()]
    return [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:3001",
        "http://127.0.0.1:3001",
    ]


app = FastAPI(title="KLIPAURA Mission Control", version="0.2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_allow_origins(),
    allow_methods=["*"],
    allow_headers=["*"],
)

_STATIC_DIR = _HERE / "static"
if _STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


def _redis():
    r = get_redis_client_optional()
    if r is None:
        raise HTTPException(503, "Redis not configured (UPSTASH_REDIS_REST_URL + TOKEN or REDIS_URL)")
    return r


class JobCreateBody(BaseModel):
    product_url: str = Field(..., min_length=8)
    avatar_id: str | None = None
    template_id: str | None = None
    product_page_url: str | None = None
    product_image_urls: list[str] | str | None = None
    product_title: str | None = None
    product_bullets: list[str] | str | None = None
    cta_line: str | None = None
    script_system_override: str | None = None
    elevenlabs_voice_id: str | None = None
    affiliate_program_id: str | None = None
    affiliate_fields: dict[str, Any] | None = None
    layout_mode: str | None = None
    generate_funnel: bool = False


class SelectorRunBody(BaseModel):
    limit: int | None = Field(default=None, ge=1, le=50)
    avatar_id: str | None = None


class SentimentBody(BaseModel):
    score: float = Field(..., ge=0.0, le=1.0)
    ttl_seconds: int = Field(default=300, ge=30, le=86400)


class ScannerIngestBody(BaseModel):
    csv_path: str | None = None


class WaitlistBody(BaseModel):
    name: str | None = None
    email: str = Field(..., min_length=5, max_length=255)
    source: str = Field(default="landing_page")
    referred_by: str | None = None


class ScannerRunBody(BaseModel):
    include_amazon: bool = Field(default=True)
    include_clickbank: bool = Field(default=True)
    include_temu: bool = Field(default=True)
    geo_target: str = Field(default="AE")  # UAE default


class QueueBulkDeleteBody(BaseModel):
    job_ids: list[str] = Field(default_factory=list)


class QueueRetryFailedBody(BaseModel):
    hours: int = Field(default=24, ge=1, le=168)
    limit: int = Field(default=50, ge=1, le=200)


def _parse_iso_ts(s: str) -> datetime | None:
    try:
        raw = (s or "").strip()
        if not raw:
            return None
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _safe_job_id(raw: str) -> str:
    return "".join(c for c in (raw or "").strip() if c.isalnum() or c in ("-", "_"))


def _safe_template_id(raw: str) -> str:
    return "".join(c for c in (raw or "").strip().lower() if c.isalnum() or c in ("-", "_"))


def _scan_job_manifests(*, max_files: int = 8000) -> list[dict]:
    """Load all job manifests under JOBS_DIR (best-effort, capped for safety)."""
    out: list[dict] = []
    if not JOBS_DIR.is_dir():
        return out
    dirs = [d for d in JOBS_DIR.iterdir() if d.is_dir()]
    dirs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    for d in dirs[: max(1, int(max_files))]:
        path = d / "manifest.json"
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                out.append(data)
        except Exception:
            continue
    return out


def _template_label(template_id: str, templates_by_id: dict[str, dict]) -> str:
    tid = _safe_template_id(template_id) or "default"
    if tid == "default":
        return "Default"
    t = templates_by_id.get(tid) or {}
    name = (t.get("name") or "").strip()
    return name or tid


@app.get("/api/analytics/summary")
def analytics_summary(request: Request, max_jobs: int = 8000) -> dict:
    """
    Ops analytics: job outcomes + per-template funnel metrics.
    True platform CTR is not available without TikTok/analytics APIs; we expose
    publish rate and ledger post_url rate as shipping-first proxies.
    """
    _require_admin(request)
    templates_list = _load_templates()
    templates_by_id = {t["id"]: t for t in templates_list if isinstance(t, dict) and t.get("id")}

    manifests = _scan_job_manifests(max_files=max_jobs)
    outcomes: dict[str, int] = defaultdict(int)
    by_template: dict[str, dict[str, int]] = defaultdict(
        lambda: defaultdict(int)
    )

    for m in manifests:
        st = (m.get("status") or "UNKNOWN").upper().strip() or "UNKNOWN"
        outcomes[st] += 1
        payload = m.get("payload") if isinstance(m.get("payload"), dict) else {}
        tid = _safe_template_id(str(payload.get("template_id") or payload.get("template") or ""))
        if not tid:
            tid = "default"
        by_template[tid][st] += 1
        by_template[tid]["_jobs"] += 1

    # Ledger: join job_id → template for post_url / publish_status breakdown
    ledger_by_template: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    job_to_template: dict[str, str] = {}
    for m in manifests:
        jid = str(m.get("job_id") or "").strip()
        if not jid:
            continue
        payload = m.get("payload") if isinstance(m.get("payload"), dict) else {}
        tid = _safe_template_id(str(payload.get("template_id") or payload.get("template") or ""))
        job_to_template[jid] = tid or "default"

    try:
        from revenue_tracker import LEDGER

        if LEDGER.is_file():
            for line in LEDGER.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    ent = json.loads(line)
                except json.JSONDecodeError:
                    continue
                jid = str(ent.get("job_id") or "").strip()
                tpl = job_to_template.get(jid, "default")
                ledger_by_template[tpl]["ledger_lines"] += 1
                pu = (ent.get("post_url") or "").strip()
                if pu:
                    ledger_by_template[tpl]["with_post_url"] += 1
                ps = str(ent.get("publish_status") or "").lower()
                if ps:
                    ledger_by_template[tpl][f"pub_{ps}"] += 1
    except Exception:
        pass

    # Per-template rows for UI + charts
    template_ids = sorted(set(by_template.keys()) | set(ledger_by_template.keys()))
    rows: list[dict] = []
    for tid in template_ids:
        agg = by_template.get(tid) or {}
        total = int(agg.get("_jobs") or sum(v for k, v in agg.items() if not k.startswith("_")))
        published = int(agg.get("PUBLISHED") or 0)
        rejected = int(agg.get("REJECTED") or 0)
        pub_failed = int(agg.get("PUBLISH_FAILED") or 0)
        manual = int(agg.get("MANUAL_PUBLISH_REQUIRED") or 0)
        led = ledger_by_template.get(tid) or {}
        led_lines = int(led.get("ledger_lines") or 0)
        with_url = int(led.get("with_post_url") or 0)

        publish_rate = (100.0 * published / total) if total else 0.0
        decided = published + rejected
        approval_rate = (100.0 * published / decided) if decided else 0.0
        post_url_rate = (100.0 * with_url / led_lines) if led_lines else 0.0

        rows.append(
            {
                "template_id": tid,
                "template_name": _template_label(tid, templates_by_id),
                "jobs_total": total,
                "published": published,
                "rejected": rejected,
                "publish_failed": pub_failed,
                "manual_publish_required": manual,
                "preview_or_hitl": int(agg.get("PREVIEW_PENDING") or 0)
                + int(agg.get("HITL_PENDING") or 0)
                + int(agg.get("PUBLISHING_QUEUED") or 0),
                "publish_rate_pct": round(publish_rate, 2),
                "approval_rate_pct": round(approval_rate, 2),
                "ledger_lines": led_lines,
                "ledger_post_url_rate_pct": round(post_url_rate, 2),
            }
        )

    rows.sort(key=lambda r: (-r["jobs_total"], r["template_id"]))

    # Chart-friendly outcome groups (collapse rare technical states)
    outcome_groups = {
        "PUBLISHED": int(outcomes.get("PUBLISHED") or 0),
        "MANUAL_PUBLISH_REQUIRED": int(outcomes.get("MANUAL_PUBLISH_REQUIRED") or 0),
        "REJECTED": int(outcomes.get("REJECTED") or 0),
        "IN_FLIGHT": sum(
            int(outcomes.get(k) or 0)
            for k in (
                "QUEUED",
                "PROCESSING",
                "PREVIEW_PENDING",
                "HITL_PENDING",
                "PUBLISHING_QUEUED",
                "PAUSED",
            )
        ),
        "FAILED": sum(
            int(outcomes.get(k) or 0)
            for k in (
                "PUBLISH_FAILED",
                "DEAD_LETTER",
                "ERROR",
                "RETRYING",
            )
        ),
        "OTHER": 0,
    }
    accounted = sum(outcome_groups.values()) - outcome_groups["OTHER"]
    total_o = sum(int(v) for v in outcomes.values())
    if total_o > accounted:
        outcome_groups["OTHER"] = total_o - accounted

    return {
        "ok": True,
        "jobs_scanned": len(manifests),
        "outcomes_raw": dict(sorted(outcomes.items(), key=lambda x: (-x[1], x[0]))),
        "outcome_groups": outcome_groups,
        "by_template": rows,
        "note": (
            "Platform CTR (clicks/impressions) is not wired. "
            "publish_rate_pct = PUBLISHED / jobs; "
            "ledger_post_url_rate_pct = revenue.jsonl lines with post_url / ledger lines for jobs in that template."
        ),
    }


def _load_templates() -> list[dict]:
    try:
        if not _TEMPLATES_PATH.is_file():
            return []
        data = json.loads(_TEMPLATES_PATH.read_text(encoding="utf-8"))
        if isinstance(data, list):
            out = []
            for t in data:
                if not isinstance(t, dict):
                    continue
                tid = _safe_template_id(str(t.get("id") or ""))
                name = str(t.get("name") or tid or "").strip()
                scene_types = t.get("scene_types")
                knobs = t.get("knobs")
                if not tid or not name:
                    continue
                if not isinstance(scene_types, list) or not all(isinstance(x, str) for x in scene_types):
                    scene_types = []
                if not isinstance(knobs, dict):
                    knobs = {}
                out.append({"id": tid, "name": name, "scene_types": scene_types, "knobs": knobs})
            return out
    except Exception:
        return []


@app.get("/api/templates")
def templates_list() -> dict:
    """Public list of available templates (no secrets)."""
    ts = _load_templates()
    return {"ok": True, "templates": ts}


def _list_remove_job(r, key: str, job_id: str, *, scan_limit: int = 2000) -> tuple[int, str | None]:
    """Remove a job payload from a Redis list by job_id. Returns (removed_count, removed_raw)."""
    safe = _safe_job_id(job_id)
    if not safe:
        return 0, None
    try:
        items = r.lrange(key, 0, int(scan_limit) - 1)
    except Exception:
        return 0, None
    if not items:
        return 0, None
    found_raw: str | None = None
    for raw in items:
        try:
            obj = json.loads(raw) if isinstance(raw, str) else None
        except Exception:
            obj = None
        if isinstance(obj, dict) and (obj.get("job_id") or "") == safe:
            found_raw = raw
            break
    if not found_raw:
        return 0, None
    try:
        removed = int(r.lrem(key, 1, found_raw) or 0)
    except Exception:
        removed = 0
    return removed, found_raw if removed else None


def _list_move_job(r, src: str, dst: str, job_id: str) -> bool:
    removed, raw = _list_remove_job(r, src, job_id)
    if not removed or not raw:
        return False
    try:
        r.rpush(dst, raw)
        return True
    except Exception:
        try:
            r.rpush(src, raw)
        except Exception:
            pass
        return False


def _delete_job_artifacts(job_id: str, manifest: dict) -> dict:
    """Best-effort delete for local file + R2 object. Never raises."""
    out: dict = {"local_deleted": False, "r2_deleted": False}
    fp = (manifest.get("final_video_path") or "").strip()
    if fp:
        try:
            p = Path(fp)
            if p.is_file():
                p.unlink()
                out["local_deleted"] = True
        except Exception:
            pass
    try:
        from infrastructure.storage import create_r2_store, r2_configured

        if r2_configured():
            store = create_r2_store()
            key = f"jobs/{job_id}/FINAL_VIDEO.mp4"
            out["r2_deleted"] = bool(store.delete(key))
    except Exception:
        pass
    return out


def _url_hash(url: str) -> str:
    return hashlib.sha256(url.strip().encode("utf-8")).hexdigest()


def _ops_authorized(request: Request) -> bool:
    key = (os.getenv("OPS_API_KEY") or "").strip()
    if not key:
        return False
    got = (request.headers.get("X-Ops-Key") or request.query_params.get("ops_key") or "").strip()
    return got == key


def _admin_authorized(request: Request) -> bool:
    key = (os.getenv("ADMIN_API_KEY") or "").strip()
    if not key:
        return False
    got = (request.headers.get("X-Admin-Key") or request.query_params.get("admin_key") or "").strip()
    return got == key


def _require_admin(request: Request) -> None:
    if not (os.getenv("ADMIN_API_KEY") or "").strip():
        raise HTTPException(503, "ADMIN_API_KEY not set")
    if not _admin_authorized(request):
        raise HTTPException(401, "Send header X-Admin-Key matching ADMIN_API_KEY")


@app.get("/api/db/status")
def db_status(request: Request) -> JSONResponse:
    _require_admin(request)
    return JSONResponse(db_ping())


def _core_v1_root() -> Path:
    return _REPO / "klip-avatar" / "core_v1"


def _avatars_root() -> Path:
    return _core_v1_root() / "data" / "avatars"


def _safe_avatar_id(raw: str) -> str:
    aid = "".join(c for c in (raw or "").strip() if c.isalnum() or c in ("_", "-"))
    return aid


def _read_json_file(path: Path) -> dict:
    try:
        if not path.is_file():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_json_file(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")


def _mask_secret(v: str) -> str:
    s = (v or "").strip()
    if not s:
        return ""
    if len(s) <= 8:
        return "***"
    return s[:3] + "…" + s[-3:]


def _is_secret_key(k: str) -> bool:
    key = (k or "").strip().lower()
    if not key:
        return False
    if key in ("elevenlabs_voice_id", "voice_id", "tiktok_account_id"):
        return False
    return any(t in key for t in ("api_key", "secret", "token", "password", "bearer"))


def _redact_social_config(sc: dict) -> tuple[dict, dict]:
    """Return (safe_config, secret_presence_map)."""
    if not isinstance(sc, dict):
        return {}, {}
    safe = dict(sc)
    present: dict[str, bool] = {}
    for k, v in list(sc.items()):
        if _is_secret_key(k):
            present[k] = bool(str(v or "").strip())
            safe[k] = "***" if present[k] else ""
    return safe, present


def _merge_social_config(existing: dict, updates: dict) -> dict:
    """Merge updates into existing while preserving secrets unless explicitly replaced."""
    if not isinstance(existing, dict):
        existing = {}
    if not isinstance(updates, dict):
        return dict(existing)
    out = dict(existing)
    for k, v in updates.items():
        if v is None:
            out.pop(k, None)
            continue
        if _is_secret_key(k):
            sv = str(v or "")
            if not sv.strip() or sv.strip() == "***" or "…" in sv:
                continue
            out[k] = sv
            continue
        out[k] = v
    return out


def _r2_optional():
    try:
        from infrastructure.storage import create_r2_store, r2_configured

        if not r2_configured():
            return None
        return create_r2_store()
    except Exception:
        return None


def _r2_download_avatar_bundle(store, avatar_id: str, *, include_social_config: bool = True) -> list[str]:
    d = _avatars_root() / avatar_id
    d.mkdir(parents=True, exist_ok=True)
    downloaded: list[str] = []
    for name in ("persona.json", "portrait.png", "face.png"):
        dest = d / name
        if store.download_to_path(f"avatars/{avatar_id}/{name}", str(dest)):
            downloaded.append(name)
    if include_social_config:
        dest = d / "social_config.json"
        if store.download_to_path(f"avatars/{avatar_id}/social_config.json", str(dest)):
            downloaded.append("social_config.json")
    return downloaded


class AvatarCreateBody(BaseModel):
    avatar_id: str = Field(..., min_length=2)
    persona: dict = Field(default_factory=dict)
    portrait_png_b64: str | None = None
    face_png_b64: str | None = None
    social_config: dict | None = None


class AvatarUpdateBody(BaseModel):
    persona: dict | None = None
    portrait_png_b64: str | None = None
    face_png_b64: str | None = None
    social_config: dict | None = None


class R2SyncBody(BaseModel):
    include_social_config: bool = Field(default=True)


class AvatarGenerateBody(BaseModel):
    description: str = Field(..., min_length=4)
    visual_profile: dict | None = None
    style_consistency_id: str | None = None


class AvatarVoiceTestBody(BaseModel):
    text: str | None = None
    voice_id: str | None = None


class AvatarLipsyncTestBody(BaseModel):
    """Body for ``POST /api/avatars/test-lipsync`` — sample ElevenLabs audio (lip-sync prep)."""

    avatar_id: str = Field(..., min_length=2)
    text: str | None = Field(default=None, max_length=500)
    voice_id: str | None = None


def _elevenlabs_tts_bytes(voice_id: str, text: str) -> bytes:
    key = (os.environ.get("ELEVENLABS_API_KEY") or os.environ.get("XI_API_KEY") or "").strip()
    if not key:
        raise HTTPException(503, "ELEVENLABS_API_KEY not set")
    t = (text or "Quick voice check for this avatar.").strip()
    if len(t) > 400:
        t = t[:400]
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    payload = {
        "text": t,
        "model_id": (os.environ.get("ELEVENLABS_MODEL_ID") or "eleven_multilingual_v2").strip()
        or "eleven_multilingual_v2",
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "audio/mpeg",
            "xi-api-key": key,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode("utf-8", "replace")
        except Exception:
            detail = str(e)
        raise HTTPException(502, f"ElevenLabs HTTP {getattr(e, 'code', '?')}: {detail[:300]}") from e


def _resolve_voice_id_for_avatar(aid: str, body_voice: str | None) -> str:
    from infrastructure.avatar_registry import resolve_elevenlabs_voice_id

    if body_voice and str(body_voice).strip():
        return str(body_voice).strip()
    rv = resolve_elevenlabs_voice_id(aid)
    if rv:
        return rv
    d = _avatars_root() / aid
    social = _read_json_file(d / "social_config.json") if d.is_dir() else {}
    if isinstance(social, dict):
        v = social.get("elevenlabs_voice_id") or social.get("ELEVENLABS_VOICE_ID")
        if v and str(v).strip():
            return str(v).strip()
    return ""


def _is_blacklisted(r, url: str) -> bool:
    u = (url or "").strip()
    if not u:
        return False
    return bool(r.exists(BLACKLIST_PREFIX + _url_hash(u)))


@app.get("/health")
def health() -> dict:
    """Liveness for Railway/load balancers — no Redis; must respond immediately."""
    return {"status": "ok", "service": "klipaura-hitl-api"}


@app.get("/api/ops/summary")
def ops_summary() -> dict:
    """Queues, recent jobs, revenue rollup, R2 flag — single pane for Mission Control."""
    out: dict = {
        "redis_ok": False,
        "queues": {},
        "worker": {},
        "r2_configured": False,
        "revenue": {},
        "recent_jobs": [],
        "admin_api_configured": bool((os.getenv("ADMIN_API_KEY") or "").strip()),
        "ops_api_configured": bool((os.getenv("OPS_API_KEY") or "").strip()),
        "public_site_url": (os.getenv("PUBLIC_SITE_URL") or "").strip(),
        "mission_control_url": (os.getenv("MISSION_CONTROL_URL") or "").strip(),
        "jobs_dir": str(JOBS_DIR),
    }
    try:
        from infrastructure.storage import r2_configured

        out["r2_configured"] = bool(r2_configured())
    except Exception:
        pass
    try:
        from revenue_tracker import get_summary

        out["revenue"] = get_summary()
    except Exception:
        pass
    r = get_redis_client_optional()
    if r is not None:
        out["redis_ok"] = True
        try:
            out["queues"] = {
                "jobs_pending": r.llen(JOBS_PENDING),
                "jobs_paused": r.llen(JOBS_PAUSED),
                "hitl_pending": r.llen(HITL_PENDING),
                "dlq": r.llen(DLQ),
            }
        except Exception:
            out["queues"] = {}
        try:
            out["queue_global_paused"] = bool((r.get(QUEUE_GLOBAL_PAUSED_KEY) or "").strip())
        except Exception:
            out["queue_global_paused"] = False
        try:
            raw_hb = r.get(WORKER_AVATAR_HEARTBEAT_KEY)
            hb = json.loads(raw_hb) if raw_hb else None
            out["worker"] = hb if isinstance(hb, dict) else {"ok": False}
        except Exception:
            out["worker"] = {"ok": False}
    try:
        out["recent_jobs"] = list_recent_job_summaries(15)
    except Exception:
        out["recent_jobs"] = []
    try:
        from infrastructure.autopilot_info import uae_hours_to_utc_strings
        from infrastructure.scheduler_budget import get_budget_snapshot

        out["budget"] = get_budget_snapshot()
        m_utc, e_utc, uae_h = uae_hours_to_utc_strings()
        out["selector_schedule_utc"] = {"morning": m_utc, "evening": e_utc, "uae_hours": uae_h}
        out["autopilot_mode"] = (os.getenv("AUTOPILOT_MODE") or "0").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
    except Exception:
        pass
    try:
        from infrastructure.ops_runtime import get_providers_snapshot

        out["providers"] = get_providers_snapshot()
    except Exception:
        out["providers"] = {}
    out["pipeline_hint"] = _compute_pipeline_hint(out)
    return out


@app.get("/api/queue/summary")
def queue_summary(request: Request) -> dict:
    _require_admin(request)
    r = _redis()
    queues: dict = {
        "jobs_pending": int(r.llen(JOBS_PENDING) or 0),
        "jobs_paused": int(r.llen(JOBS_PAUSED) or 0),
        "hitl_pending": int(r.llen(HITL_PENDING) or 0),
        "dlq": int(r.llen(DLQ) or 0),
    }
    paused = bool((r.get(QUEUE_GLOBAL_PAUSED_KEY) or "").strip())
    counts: dict[str, int] = {"QUEUED": 0, "PROCESSING": 0, "HITL_PENDING": 0, "PUBLISHED": 0, "ERROR": 0}
    try:
        for row in list_recent_job_summaries(200):
            st = (row.get("status") or "").upper().strip()
            if st in counts:
                counts[st] += 1
            elif st in ("DEAD_LETTER", "RETRYING", "PUBLISH_FAILED"):
                counts["ERROR"] += 1
    except Exception:
        pass
    return {"ok": True, "queues": queues, "global_paused": paused, "status_counts_recent": counts}


@app.get("/api/queue/list")
def queue_list(request: Request, limit: int = 60) -> dict:
    _require_admin(request)
    lim = max(1, min(int(limit or 60), 200))
    out = []
    try:
        for row in list_recent_job_summaries(lim):
            jid = row.get("job_id")
            if not jid:
                continue
            try:
                m = read_manifest(str(jid))
            except Exception:
                m = {}
            payload = m.get("payload") if isinstance(m.get("payload"), dict) else {}
            out.append(
                {
                    "job_id": str(jid),
                    "status": row.get("status"),
                    "updated_at": row.get("updated_at"),
                    "product_url": payload.get("product_url") or payload.get("product_page_url"),
                    "avatar_id": payload.get("avatar_id"),
                    "template": payload.get("template") or payload.get("template_id"),
                    "has_video": bool(row.get("has_video")),
                    "error": row.get("error"),
                }
            )
    except Exception:
        out = []
    return {"ok": True, "jobs": out}


@app.post("/api/queue/pause/global")
def queue_pause_global(request: Request) -> dict:
    _require_admin(request)
    r = _redis()
    r.set(QUEUE_GLOBAL_PAUSED_KEY, "1")
    return {"ok": True, "paused": True}


@app.post("/api/queue/resume/global")
def queue_resume_global(request: Request) -> dict:
    _require_admin(request)
    r = _redis()
    r.delete(QUEUE_GLOBAL_PAUSED_KEY)
    return {"ok": True, "paused": False}


@app.post("/api/queue/pause/{job_id}")
def queue_pause_job(request: Request, job_id: str) -> dict:
    _require_admin(request)
    jid = _safe_job_id(job_id)
    if not jid:
        raise HTTPException(400, "invalid job_id")
    m = None
    try:
        m = read_manifest(jid)
    except Exception:
        m = None
    if isinstance(m, dict):
        st = (m.get("status") or "").upper()
        if st == "PROCESSING":
            raise HTTPException(409, "job is PROCESSING (cannot pause)")
    r = _redis()
    moved = _list_move_job(r, JOBS_PENDING, JOBS_PAUSED, jid)
    if not moved:
        raise HTTPException(404, "job not found in pending queue")
    try:
        update_manifest(jid, status="PAUSED")
    except Exception:
        pass
    return {"ok": True, "job_id": jid, "status": "PAUSED"}


@app.post("/api/queue/resume/{job_id}")
def queue_resume_job(request: Request, job_id: str) -> dict:
    _require_admin(request)
    jid = _safe_job_id(job_id)
    if not jid:
        raise HTTPException(400, "invalid job_id")
    r = _redis()
    moved = _list_move_job(r, JOBS_PAUSED, JOBS_PENDING, jid)
    if not moved:
        raise HTTPException(404, "job not found in paused queue")
    try:
        update_manifest(jid, status="QUEUED")
    except Exception:
        pass
    return {"ok": True, "job_id": jid, "status": "QUEUED"}


@app.delete("/api/queue/{job_id}")
def queue_delete_job(request: Request, job_id: str) -> dict:
    _require_admin(request)
    jid = _safe_job_id(job_id)
    if not jid:
        raise HTTPException(400, "invalid job_id")
    r = _redis()

    removed_total = 0
    for key in (JOBS_PENDING, JOBS_PAUSED, DLQ, HITL_PENDING):
        removed, _raw = _list_remove_job(r, key, jid)
        removed_total += int(removed or 0)

    try:
        m = read_manifest(jid)
    except Exception:
        m = {}

    st = (m.get("status") or "").upper().strip()
    if st == "PROCESSING":
        raise HTTPException(409, "job is PROCESSING (cannot delete safely)")

    artifacts = _delete_job_artifacts(jid, m if isinstance(m, dict) else {})
    try:
        update_manifest(jid, status="DELETED", deleted_artifacts=artifacts)
    except Exception:
        pass
    return {"ok": True, "job_id": jid, "removed_from_queues": removed_total, "artifacts": artifacts}


@app.post("/api/queue/bulk-delete")
def queue_bulk_delete(request: Request, body: QueueBulkDeleteBody) -> dict:
    _require_admin(request)
    ids = [
        _safe_job_id(x)
        for x in (body.job_ids or [])
        if _safe_job_id(x)
    ]
    ids = list(dict.fromkeys(ids))
    if not ids:
        return {"ok": True, "deleted": [], "errors": []}
    deleted: list[str] = []
    errors: list[dict] = []
    for jid in ids:
        try:
            _ = queue_delete_job(request, jid)
            deleted.append(jid)
        except HTTPException as e:
            errors.append({"job_id": jid, "error": str(e.detail)})
        except Exception as e:
            errors.append({"job_id": jid, "error": f"{type(e).__name__}: {str(e)[:200]}"})
    return {"ok": True, "deleted": deleted, "errors": errors}


@app.post("/api/queue/retry-failed")
def queue_retry_failed(request: Request, body: QueueRetryFailedBody = QueueRetryFailedBody()) -> dict:
    _require_admin(request)
    r = _redis()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=int(body.hours or 24))
    requeued: list[str] = []
    skipped: list[dict] = []

    try:
        recent = list_recent_job_summaries(400)
    except Exception:
        recent = []

    for row in recent:
        if len(requeued) >= int(body.limit or 50):
            break
        jid = _safe_job_id(str(row.get("job_id") or ""))
        if not jid:
            continue
        try:
            m = read_manifest(jid)
        except Exception:
            continue

        st = (m.get("status") or "").upper().strip()
        if st not in ("DEAD_LETTER", "PUBLISH_FAILED"):
            continue

        ts = _parse_iso_ts(str(m.get("updated_at") or ""))
        if ts is not None and ts < cutoff:
            continue

        payload = m.get("payload") if isinstance(m.get("payload"), dict) else {}
        if not isinstance(payload, dict):
            continue
        if not (payload.get("product_url") or payload.get("product_page_url")):
            skipped.append({"job_id": jid, "reason": "missing product_url"})
            continue

        job_payload = dict(payload)
        job_payload["job_id"] = jid
        job_payload["retry_count"] = 0

        try:
            for key in (DLQ, JOBS_PAUSED, JOBS_PENDING):
                _list_remove_job(r, key, jid)
            r.rpush(JOBS_PENDING, json.dumps(job_payload))
            update_manifest(jid, status="QUEUED", retry_count=0, requeued_at=datetime.now(timezone.utc).isoformat())
            requeued.append(jid)
        except Exception as e:
            skipped.append({"job_id": jid, "reason": f"{type(e).__name__}: {str(e)[:200]}"})

    return {"ok": True, "requeued": requeued, "skipped": skipped}


def _compute_pipeline_hint(summary: dict) -> str:
    """Short UX hint when jobs look stuck (no secrets)."""
    q = summary.get("queues") or {}
    jp = int(q.get("jobs_pending") or 0)
    worker = summary.get("worker") if isinstance(summary.get("worker"), dict) else {}
    worker_online = bool(worker and worker.get("state"))
    recent = summary.get("recent_jobs") or []
    for j in recent:
        st = (j.get("status") or "").upper()
        if st == "PROCESSING" and not j.get("has_video"):
            if jp == 0:
                return (
                    "A job is still PROCESSING with no video yet. The worker runs the full pipeline "
                    "(often 5–20+ minutes): scraping, LLM script, WaveSpeed/ElevenLabs when configured, "
                    "then FFmpeg render. Check Railway → klip-api → Logs if it stays stuck for 30+ minutes."
                )
            break
        if st == "QUEUED" and jp > 0:
            if not worker_online:
                return (
                    f"There are {jp} job(s) waiting, but the worker appears offline. "
                    "On Railway, ensure the service is running the worker process and check Logs for worker errors."
                )
            return f"There are {jp} job(s) waiting for the worker — pipeline will start when the worker picks them up."
    if jp > 0:
        if not worker_online:
            return (
                f"{jp} job(s) in the render queue, but the worker appears offline. "
                "Start/restart the worker and re-check Logs."
            )
        return f"{jp} job(s) in the render queue — worker should pick them up shortly."
    prov = summary.get("providers") or {}
    ak = prov.get("api_keys_present") or {}
    if not ak.get("wavespeed"):
        return "WaveSpeed API key not set in this environment — I2V/T2I may use Ken Burns fallbacks only."
    return ""


@app.get("/api/providers/credits")
def providers_credits(request: Request) -> dict:
    _require_admin(request)

    out: dict = {"ok": True, "elevenlabs": {"ok": False}}

    el_key = (os.getenv("ELEVENLABS_API_KEY") or "").strip()
    if el_key:
        try:
            import httpx

            with httpx.Client(timeout=10) as client:
                r = client.get(
                    "https://api.elevenlabs.io/v1/user/subscription",
                    headers={"xi-api-key": el_key},
                )
                if r.status_code >= 400:
                    out["elevenlabs"] = {
                        "ok": False,
                        "error": f"HTTP {r.status_code}",
                    }
                else:
                    data = r.json() if r.text else {}
                    out["elevenlabs"] = {
                        "ok": True,
                        "character_count": data.get("character_count"),
                        "character_limit": data.get("character_limit"),
                        "next_character_count_reset_unix": data.get("next_character_count_reset_unix"),
                        "tier": data.get("tier"),
                    }
        except Exception as e:
            out["elevenlabs"] = {
                "ok": False,
                "error": f"{type(e).__name__}: {str(e)[:200]}",
            }
    else:
        out["elevenlabs"] = {"ok": False, "error": "ELEVENLABS_API_KEY not set"}

    return out


@app.get("/api/autopilot/status")
def autopilot_status() -> dict:
    """Phase 4 snapshot: budget, UAE→UTC selector times, guardian interval."""
    from infrastructure.autopilot_info import uae_hours_to_utc_strings
    from infrastructure.scheduler_budget import get_budget_snapshot

    morning_utc, evening_utc, uae = uae_hours_to_utc_strings()
    autopilot = (os.getenv("AUTOPILOT_MODE") or "0").strip().lower() in ("1", "true", "yes", "on")
    try:
        g = int(os.getenv("GUARDIAN_INTERVAL_MIN", "30"))
    except ValueError:
        g = 30
    return {
        "autopilot_enabled": autopilot,
        "selector_morning_utc": morning_utc,
        "selector_evening_utc": evening_utc,
        "uae_hours": uae,
        "guardian_interval_min": g,
        "budget": get_budget_snapshot(),
        "ops_api_configured": bool((os.getenv("OPS_API_KEY") or "").strip()),
        "products_csv": (os.getenv("PRODUCTS_CSV") or "products.csv").strip(),
    }


@app.post("/api/selector/run")
def selector_run(request: Request, body: SelectorRunBody = SelectorRunBody()) -> dict:
    """Phase 3: run `products.csv` → filter → score → queue jobs (requires OPS_API_KEY + X-Ops-Key)."""
    if not (os.getenv("OPS_API_KEY") or "").strip():
        raise HTTPException(
            503,
            "OPS_API_KEY not set — add it to the server env to enable selector runs from the API",
        )
    if not _ops_authorized(request):
        raise HTTPException(401, "Send header X-Ops-Key matching OPS_API_KEY")
    from infrastructure.scheduler_budget import video_budget_allows

    if not video_budget_allows():
        raise HTTPException(429, "Daily video budget exhausted (see MAX_DAILY_VIDEOS / budget snapshot)")
    from klip_selector.selector_worker import run_cycle

    rc = run_cycle(limit=body.limit, avatar_id=body.avatar_id)
    if rc != 0:
        raise HTTPException(500, "selector run failed (check server logs / Redis)")
    return {"ok": True, "status": "selector_cycle_completed"}


@app.post("/api/scanner/ingest")
def scanner_ingest(request: Request, body: ScannerIngestBody = ScannerIngestBody()) -> dict:
    if not (os.getenv("OPS_API_KEY") or "").strip():
        raise HTTPException(503, "OPS_API_KEY not set")
    if not _ops_authorized(request):
        raise HTTPException(401, "Send header X-Ops-Key matching OPS_API_KEY")
    try:
        return ingest_products_csv(csv_path=body.csv_path)
    except Exception as e:
        raise HTTPException(500, f"scanner ingest failed: {type(e).__name__}: {str(e)[:200]}")


@app.post("/api/scanner/run")
def scanner_run(request: Request, body: ScannerRunBody = ScannerRunBody()) -> dict:
    if not (os.getenv("OPS_API_KEY") or "").strip():
        raise HTTPException(503, "OPS_API_KEY not set")
    if not _ops_authorized(request):
        raise HTTPException(401, "Send header X-Ops-Key matching OPS_API_KEY")
    try:
        return run_scanner(
            include_amazon=bool(body.include_amazon),
            include_clickbank=bool(body.include_clickbank),
            include_temu=bool(body.include_temu),
            geo_target=(body.geo_target or "AE").strip(),
        )
    except Exception as e:
        raise HTTPException(500, f"scanner run failed: {type(e).__name__}: {str(e)[:200]}")


@app.post("/api/scanner/discover")
def scanner_discover(request: Request) -> dict:
    """Run discovery agent to check affiliate network health and persist to Postgres. Requires OPS_API_KEY."""
    if not (os.getenv("OPS_API_KEY") or "").strip():
        raise HTTPException(503, "OPS_API_KEY not set")
    if not _ops_authorized(request):
        raise HTTPException(401, "Send header X-Ops-Key matching OPS_API_KEY")
    try:
        return run_discovery_cycle()
    except Exception as e:
        raise HTTPException(500, f"scanner discovery failed: {type(e).__name__}: {str(e)[:200]}")


@app.post("/api/market/sentiment")
def market_sentiment(request: Request, body: SentimentBody) -> dict:
    """Phase 5: push trader/market sentiment to Redis (selector boost). Requires OPS_API_KEY."""
    if not (os.getenv("OPS_API_KEY") or "").strip():
        raise HTTPException(503, "OPS_API_KEY not set")
    if not _ops_authorized(request):
        raise HTTPException(401, "Send header X-Ops-Key matching OPS_API_KEY")
    from klip_trader.signal_emitter import emit_market_sentiment

    ok = emit_market_sentiment(body.score, body.ttl_seconds)
    return {"ok": ok, "score": body.score}


@app.post("/api/jobs")
def post_job(body: JobCreateBody) -> dict:
    r = _redis()
    url = body.product_url.strip()
    if _is_blacklisted(r, url):
        raise HTTPException(400, "product_url is blacklisted (reject cooldown)")
    job_id = str(uuid.uuid4())
    template_id = _safe_template_id(str(body.template_id or ""))
    payload: dict = {
        "product_url": url,
        "avatar_id": (body.avatar_id or "").strip(),
        "retry_count": 0,
    }
    if template_id:
        payload["template_id"] = template_id
    # Resolve Temu product images automatically if not provided
    if not body.product_image_urls and ("temu.com" in url.lower() or "temu.to" in url.lower()):
        title2, imgs, _meta = resolve_product_images(url, body.product_title)
        if imgs:
            payload["product_image_urls"] = imgs
            if title2 and not body.product_title:
                payload["product_title"] = title2
    if body.product_page_url:
        payload["product_page_url"] = body.product_page_url
    if body.product_image_urls is not None:
        payload["product_image_urls"] = body.product_image_urls
    if body.product_title:
        payload["product_title"] = body.product_title
    if body.product_bullets is not None:
        payload["product_bullets"] = body.product_bullets
    if body.cta_line:
        payload["cta_line"] = body.cta_line
    if body.script_system_override:
        payload["script_system_override"] = body.script_system_override
    if body.elevenlabs_voice_id:
        payload["elevenlabs_voice_id"] = body.elevenlabs_voice_id
    if body.layout_mode and str(body.layout_mode).strip():
        payload["layout_mode"] = str(body.layout_mode).strip()
    if body.affiliate_program_id and str(body.affiliate_program_id).strip():
        aid = str(body.affiliate_program_id).strip()
        extra = body.affiliate_fields if isinstance(body.affiliate_fields, dict) else {}
        payload["affiliate_data"] = resolve_affiliate_data_for_job(
            aid,
            product_url=url,
            extra_fields=extra,
        )
    if body.generate_funnel:
        payload["generate_funnel"] = True
    create_manifest(job_id, {"job_id": job_id, **payload})
    r.rpush(JOBS_PENDING, json.dumps({"job_id": job_id, **payload}))
    return {"job_id": job_id, "status": "queued"}


@app.get("/api/dashboard/recent-jobs")
def dashboard_recent_jobs(limit: int = 25) -> dict:
    """Lightweight job rows for Mission Control UI: video URL, funnel link, avatar, affiliate."""
    lim = max(1, min(int(limit or 25), 100))
    out: list[dict[str, Any]] = []
    for row in list_recent_job_summaries(lim):
        jid = row.get("job_id")
        if not jid:
            continue
        try:
            m = read_manifest(str(jid))
        except Exception:
            m = {}
        pl = m.get("payload") if isinstance(m.get("payload"), dict) else {}
        ad = pl.get("affiliate_data") if isinstance(pl.get("affiliate_data"), dict) else {}
        video = (m.get("r2_url") or "").strip() or None
        out.append(
            {
                "job_id": str(jid),
                "status": m.get("status") or row.get("status"),
                "updated_at": m.get("updated_at") or row.get("updated_at"),
                "avatar_id": pl.get("avatar_id"),
                "product_url": pl.get("product_url"),
                "video_url": video,
                "funnel_url": (m.get("funnel_url") or "").strip() or None,
                "affiliate_program_id": ad.get("affiliate_program_id"),
                "generate_funnel": bool(pl.get("generate_funnel")),
            }
        )
    return {"ok": True, "jobs": out}


@app.get("/api/jobs/{job_id}/manifest")
def job_manifest(job_id: str) -> dict:
    try:
        return read_manifest(job_id)
    except Exception:
        raise HTTPException(404, "job not found")


@app.post("/api/jobs/{job_id}/generate-funnel")
def post_job_generate_funnel(job_id: str) -> dict:
    """Build HTML funnel from manifest payload and set ``funnel_url`` (R2 or local under ``jobs/``)."""
    try:
        m = read_manifest(job_id)
    except Exception:
        raise HTTPException(404, "job not found")
    pl = dict(m.get("payload") or {})
    if not pl.get("product_url"):
        raise HTTPException(400, "manifest payload missing product_url")
    pl.setdefault("job_id", job_id)
    try:
        from klip_funnel.funnel_job import build_and_attach_funnel

        url, err = build_and_attach_funnel(_REPO, job_id, pl)
    except Exception as e:
        raise HTTPException(500, f"funnel build failed: {type(e).__name__}: {str(e)[:200]}") from e
    if url:
        update_manifest(job_id, funnel_url=url)
        return {"ok": True, "job_id": job_id, "funnel_url": url}
    update_manifest(job_id, funnel_error=(err or "unknown")[:500])
    raise HTTPException(502, err or "funnel publish failed")


@app.get("/api/avatars")
def list_avatars() -> dict:
    """Disk-backed avatars merged with ``config/avatars.json`` registry (voice hints, display names)."""
    cfg = load_avatars_config()
    reg = cfg.get("avatars") if isinstance(cfg.get("avatars"), dict) else {}
    root = _avatars_root()
    out = []
    seen: set[str] = set()
    if root.is_dir():
        for d in sorted(root.iterdir()):
            if not d.is_dir():
                continue
            persona = _read_json_file(d / "persona.json")
            if not persona:
                continue
            aid = d.name
            seen.add(aid)
            has_portrait = (d / "portrait.png").is_file()
            has_face = (d / "face.png").is_file()
            r_ent = reg.get(aid) if isinstance(reg.get(aid), dict) else {}
            out.append(
                {
                    "avatar_id": aid,
                    "name": persona.get("name") or d.name,
                    "registry_display_name": r_ent.get("display_name") or None,
                    "registry_voice_configured": bool(str(r_ent.get("elevenlabs_voice_id") or "").strip()),
                    "niche": persona.get("niche"),
                    "render_layout": persona.get("render_layout"),
                    "has_portrait": has_portrait,
                    "has_face": has_face,
                    "has_social_config": (d / "social_config.json").is_file(),
                    "portrait_url": f"/api/avatars/{aid}/portrait.png" if has_portrait else None,
                    "face_url": f"/api/avatars/{aid}/face.png" if has_face else None,
                }
            )
    for rid, rmeta in sorted(reg.items()):
        if rid in seen or rid.startswith("_"):
            continue
        ent = rmeta if isinstance(rmeta, dict) else {}
        out.append(
            {
                "avatar_id": rid,
                "name": None,
                "registry_only": True,
                "registry_display_name": ent.get("display_name"),
                "registry_voice_configured": bool(str(ent.get("elevenlabs_voice_id") or "").strip()),
                "niche": None,
                "render_layout": None,
                "has_portrait": False,
                "has_face": False,
                "has_social_config": False,
                "portrait_url": None,
                "face_url": None,
                "notes": ent.get("notes"),
            }
        )
    return {
        "default_avatar_id": str(cfg.get("default_avatar_id") or "").strip() or "",
        "avatars": out,
    }


@app.get("/api/avatars/{avatar_id}")
def get_avatar(avatar_id: str) -> dict:
    aid = _safe_avatar_id(avatar_id)
    if not aid:
        raise HTTPException(400, "invalid avatar_id")
    d = _avatars_root() / aid
    if not d.is_dir():
        raise HTTPException(404, "avatar not found")
    persona = _read_json_file(d / "persona.json")
    if not persona:
        raise HTTPException(404, "persona.json missing")
    social = _read_json_file(d / "social_config.json")
    safe_social, secret_presence = _redact_social_config(social)
    has_portrait = (d / "portrait.png").is_file()
    has_face = (d / "face.png").is_file()
    return {
        "avatar_id": aid,
        "persona": persona,
        "social_config": safe_social,
        "social_config_secrets_present": secret_presence,
        "has_portrait": has_portrait,
        "has_face": has_face,
        "has_social_config": (d / "social_config.json").is_file(),
        "portrait_url": f"/api/avatars/{aid}/portrait.png" if has_portrait else None,
        "face_url": f"/api/avatars/{aid}/face.png" if has_face else None,
    }


@app.get("/api/avatars/{avatar_id}/portrait.png")
def get_avatar_portrait(avatar_id: str):
    aid = _safe_avatar_id(avatar_id)
    if not aid:
        raise HTTPException(400, "invalid avatar_id")
    d = _avatars_root() / aid
    p = d / "portrait.png"
    if not p.is_file():
        raise HTTPException(404, "portrait not found")
    return FileResponse(str(p), media_type="image/png", filename="portrait.png")


@app.get("/api/avatars/{avatar_id}/face.png")
def get_avatar_face(avatar_id: str):
    aid = _safe_avatar_id(avatar_id)
    if not aid:
        raise HTTPException(400, "invalid avatar_id")
    d = _avatars_root() / aid
    p = d / "face.png"
    if not p.is_file():
        raise HTTPException(404, "face not found")
    return FileResponse(str(p), media_type="image/png", filename="face.png")


@app.post("/api/avatars")
def create_avatar(request: Request, body: AvatarCreateBody) -> dict:
    _require_admin(request)
    aid = _safe_avatar_id(body.avatar_id)
    if not aid:
        raise HTTPException(400, "invalid avatar_id")
    d = _avatars_root() / aid
    d.mkdir(parents=True, exist_ok=True)
    persona = dict(body.persona or {})
    persona.setdefault("avatar_id", aid)
    _write_json_file(d / "persona.json", persona)
    if body.social_config and isinstance(body.social_config, dict):
        _write_json_file(d / "social_config.json", dict(body.social_config))
    if body.portrait_png_b64:
        try:
            (d / "portrait.png").write_bytes(base64.b64decode(body.portrait_png_b64))
        except Exception:
            raise HTTPException(400, "invalid portrait_png_b64")
    if body.face_png_b64:
        try:
            (d / "face.png").write_bytes(base64.b64decode(body.face_png_b64))
        except Exception:
            raise HTTPException(400, "invalid face_png_b64")
    return {"ok": True, "avatar_id": aid}


@app.put("/api/avatars/{avatar_id}")
def update_avatar(request: Request, avatar_id: str, body: AvatarUpdateBody) -> dict:
    _require_admin(request)
    aid = _safe_avatar_id(avatar_id)
    if not aid:
        raise HTTPException(400, "invalid avatar_id")
    d = _avatars_root() / aid
    if not d.is_dir():
        raise HTTPException(404, "avatar not found")
    if body.persona is not None:
        persona = dict(body.persona or {})
        persona.setdefault("avatar_id", aid)
        _write_json_file(d / "persona.json", persona)
    if body.social_config is not None:
        if not isinstance(body.social_config, dict):
            raise HTTPException(400, "social_config must be an object")
        existing = _read_json_file(d / "social_config.json")
        merged = _merge_social_config(existing, dict(body.social_config))
        _write_json_file(d / "social_config.json", merged)
    if body.portrait_png_b64:
        try:
            (d / "portrait.png").write_bytes(base64.b64decode(body.portrait_png_b64))
        except Exception:
            raise HTTPException(400, "invalid portrait_png_b64")
    if body.face_png_b64:
        try:
            (d / "face.png").write_bytes(base64.b64decode(body.face_png_b64))
        except Exception:
            raise HTTPException(400, "invalid face_png_b64")
    return {"ok": True, "avatar_id": aid}


@app.delete("/api/avatars/{avatar_id}")
def delete_avatar(request: Request, avatar_id: str, delete_r2: bool = False) -> dict:
    _require_admin(request)
    aid = _safe_avatar_id(avatar_id)
    if not aid:
        raise HTTPException(400, "invalid avatar_id")
    d = _avatars_root() / aid
    if not d.is_dir():
        raise HTTPException(404, "avatar not found")
    import shutil
    shutil.rmtree(d)
    deleted_r2: list[str] = []
    if delete_r2:
        store = _r2_optional()
        if store is not None:
            for name in ("persona.json", "portrait.png", "face.png", "social_config.json"):
                try:
                    key = f"avatars/{aid}/{name}"
                    if store.delete(key):
                        deleted_r2.append(name)
                except Exception:
                    continue
    return {"ok": True, "avatar_id": aid, "deleted_r2": deleted_r2}


@app.post("/api/avatars/{avatar_id}/voice-test")
def avatar_voice_test(request: Request, avatar_id: str, body: AvatarVoiceTestBody = AvatarVoiceTestBody()) -> dict:
    _require_admin(request)
    aid = _safe_avatar_id(avatar_id)
    if not aid:
        raise HTTPException(400, "invalid avatar_id")
    d = _avatars_root() / aid
    if not d.is_dir():
        raise HTTPException(404, "avatar not found")
    voice_id = _resolve_voice_id_for_avatar(aid, body.voice_id)
    if not voice_id:
        raise HTTPException(400, "No voice_id provided and none in config/avatars.json or social_config.json")
    text = (body.text or "Hey! This is a quick voice test for this avatar.").strip()
    audio = _elevenlabs_tts_bytes(voice_id, text)

    return {
        "ok": True,
        "avatar_id": aid,
        "voice_id": voice_id,
        "mime": "audio/mpeg",
        "audio_b64": base64.b64encode(audio).decode("ascii"),
    }


@app.post("/api/avatars/test-lipsync")
def avatar_test_lipsync(request: Request, body: AvatarLipsyncTestBody) -> dict:
    """
    Sample ElevenLabs audio for an avatar (same audio used before WaveSpeed lip-sync in the pipeline).
    Requires ``ADMIN_API_KEY`` when set (same as voice-test).
    """
    _require_admin(request)
    aid = _safe_avatar_id(body.avatar_id)
    if not aid:
        raise HTTPException(400, "invalid avatar_id")
    voice_id = _resolve_voice_id_for_avatar(aid, body.voice_id)
    if not voice_id:
        raise HTTPException(400, "No voice_id: set elevenlabs_voice_id in config/avatars.json or social_config.json")
    text = (body.text or "This is a lip-sync prep sample — short and clear.").strip()
    audio = _elevenlabs_tts_bytes(voice_id, text)
    return {
        "ok": True,
        "avatar_id": aid,
        "voice_id": voice_id,
        "mime": "audio/mpeg",
        "audio_b64": base64.b64encode(audio).decode("ascii"),
        "hint": "Use portrait/face assets with this voice in the UGC pipeline; full lip-sync uses WaveSpeed in worker.",
    }


@app.post("/api/avatars/{avatar_id}/sync-to-r2")
def sync_avatar_to_r2(request: Request, avatar_id: str, body: R2SyncBody = R2SyncBody()) -> dict:
    _require_admin(request)
    aid = _safe_avatar_id(avatar_id)
    if not aid:
        raise HTTPException(400, "invalid avatar_id")
    d = _avatars_root() / aid
    if not d.is_dir():
        raise HTTPException(404, "avatar not found")
    store = _r2_optional()
    if store is None:
        raise HTTPException(503, "R2 not configured")
    uploaded = []
    for name in ("persona.json", "portrait.png", "face.png"):
        p = d / name
        if p.is_file():
            if store.upload_file(str(p), f"avatars/{aid}/{name}"):
                uploaded.append(name)
    if body.include_social_config:
        sc = d / "social_config.json"
        if sc.is_file():
            if store.upload_file(str(sc), f"avatars/{aid}/social_config.json"):
                uploaded.append("social_config.json")
    return {"ok": True, "avatar_id": aid, "uploaded": uploaded}


@app.get("/api/avatars/r2")
def list_avatars_in_r2() -> dict:
    store = _r2_optional()
    if store is None:
        return {"r2_configured": False, "avatars": []}
    out: set[str] = set()
    for row in store.list_(prefix="avatars/", limit=5000):
        key = (row.get("key") or "")
        if not key.startswith("avatars/"):
            continue
        rest = key[len("avatars/") :]
        if not rest:
            continue
        aid = rest.split("/", 1)[0].strip()
        if aid:
            out.add(aid)
    return {"r2_configured": True, "avatars": sorted(out)}


@app.post("/api/avatars/{avatar_id}/hydrate-from-r2")
def hydrate_avatar_from_r2(request: Request, avatar_id: str, body: R2SyncBody = R2SyncBody()) -> dict:
    _require_admin(request)
    aid = _safe_avatar_id(avatar_id)
    if not aid:
        raise HTTPException(400, "invalid avatar_id")
    store = _r2_optional()
    if store is None:
        raise HTTPException(503, "R2 not configured")
    downloaded = _r2_download_avatar_bundle(store, aid, include_social_config=body.include_social_config)
    return {"ok": True, "avatar_id": aid, "downloaded": downloaded}


def _load_avatar_visual_generator():
    p = _core_v1_root() / "services" / "influencer_engine" / "avatar" / "avatar_visual_generator.py"
    if not p.is_file():
        raise RuntimeError("core_v1 avatar_visual_generator.py not found")
    spec = importlib.util.spec_from_file_location("klip_avatar_visual_generator", str(p))
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load avatar_visual_generator")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _generate_avatar_image_to(path: Path, description: str, visual_profile: dict | None, style_consistency_id: str | None) -> dict:
    mod = _load_avatar_visual_generator()
    fn = getattr(mod, "generate_avatar_image", None)
    if fn is None:
        raise RuntimeError("generate_avatar_image missing")
    return fn(
        visual_profile or {},
        output_path=str(path),
        style_consistency_id=style_consistency_id,
        description=description,
    )


@app.post("/api/avatars/{avatar_id}/generate-face")
def generate_avatar_face(request: Request, avatar_id: str, body: AvatarGenerateBody) -> dict:
    _require_admin(request)
    aid = _safe_avatar_id(avatar_id)
    if not aid:
        raise HTTPException(400, "invalid avatar_id")
    d = _avatars_root() / aid
    if not d.is_dir():
        raise HTTPException(404, "avatar not found")
    out = d / "face.png"
    try:
        res = _generate_avatar_image_to(
            out,
            body.description,
            body.visual_profile,
            body.style_consistency_id or f"{aid}_seed",
        )
    except Exception as e:
        raise HTTPException(500, f"generate failed: {type(e).__name__}: {str(e)[:200]}")
    return {"ok": True, "avatar_id": aid, "path": str(out), "result": res}


@app.post("/api/avatars/{avatar_id}/generate-portrait")
def generate_avatar_portrait(request: Request, avatar_id: str, body: AvatarGenerateBody) -> dict:
    _require_admin(request)
    aid = _safe_avatar_id(avatar_id)
    if not aid:
        raise HTTPException(400, "invalid avatar_id")
    d = _avatars_root() / aid
    if not d.is_dir():
        raise HTTPException(404, "avatar not found")
    out = d / "portrait.png"
    try:
        res = _generate_avatar_image_to(
            out,
            body.description,
            body.visual_profile,
            body.style_consistency_id or f"{aid}_seed",
        )
    except Exception as e:
        raise HTTPException(500, f"generate failed: {type(e).__name__}: {str(e)[:200]}")
    return {"ok": True, "avatar_id": aid, "path": str(out), "result": res}


@app.get("/api/next-job")
def next_job() -> JSONResponse:
    r = _redis()
    popped = r.blpop([HITL_PENDING], timeout=10)
    if not popped:
        return JSONResponse({"job": None})
    try:
        data = json.loads(popped[1])
    except json.JSONDecodeError:
        raise HTTPException(500, "bad queue payload")
    return JSONResponse({"job": data})


@app.get("/api/jobs/{job_id}/video")
def job_video(job_id: str):
    try:
        m = read_manifest(job_id)
    except Exception:
        raise HTTPException(404, "job not found")
    path = m.get("final_video_path") or ""
    if not path or not Path(path).is_file():
        r2 = (m.get("r2_url") or "").strip()
        if r2.startswith("http://") or r2.startswith("https://"):
            return RedirectResponse(r2)
        raise HTTPException(404, "video not ready")
    return FileResponse(path, media_type="video/mp4", filename="FINAL_VIDEO.mp4")


@app.post("/api/approve/{job_id}")
def approve(job_id: str) -> dict:
    try:
        m = read_manifest(job_id)
    except Exception:
        raise HTTPException(404, "job not found")
    payload = m.get("payload") or {}
    avatar_id = (payload.get("avatar_id") or "").strip()
    product_url = (payload.get("product_url") or "").strip()
    r2 = (m.get("r2_url") or "").strip()
    final_path = (m.get("final_video_path") or "").strip()
    update_manifest(job_id, status="PUBLISHING_QUEUED")
    result = publish_job(
        avatar_id,
        job_id,
        r2,
        title="UGC",
        description=product_url[:4000],
        final_video_path=final_path or None,
        product_url=product_url,
    )
    mode = result.get("publish_mode")
    if mode == "getlate" and result.get("ok"):
        final_status = "PUBLISHED"
    elif mode == "manual":
        final_status = "MANUAL_PUBLISH_REQUIRED"
    else:
        final_status = "PUBLISH_FAILED"
    safe = {k: v for k, v in result.items() if k != "response"}
    if isinstance(result.get("response"), dict) and len(json.dumps(result.get("response"))) > 12000:
        safe["response"] = {"_truncated": True}
    else:
        safe["response"] = result.get("response")
    update_manifest(job_id, status=final_status, publish_result=safe)
    return {"ok": True, "job_id": job_id, "result": result}


@app.post("/api/reject/{job_id}")
def reject(job_id: str) -> dict:
    try:
        m = read_manifest(job_id)
    except Exception:
        raise HTTPException(404, "job not found")
    product_url = (m.get("payload") or {}).get("product_url") or ""
    r = _redis()
    bl_key = None
    if product_url.strip():
        h = hashlib.sha256(product_url.encode("utf-8")).hexdigest()
        bl_key = BLACKLIST_PREFIX + h
        r.setex(bl_key, "1", 7 * 86400)
    update_manifest(job_id, status="REJECTED")
    return {"ok": True, "job_id": job_id, "blacklist_key": bl_key}


@app.post("/api/regenerate/{job_id}")
def regenerate(job_id: str) -> dict:
    try:
        m = read_manifest(job_id)
    except Exception:
        raise HTTPException(404, "job not found")
    p = m.get("payload") or {}
    product_url = (p.get("product_url") or "").strip()
    avatar_id = (p.get("avatar_id") or "").strip()
    if not product_url:
        raise HTTPException(400, "no product_url in manifest")
    r = _redis()
    if _is_blacklisted(r, product_url):
        raise HTTPException(400, "product_url is blacklisted (reject cooldown)")
    new_id = str(uuid.uuid4())
    payload = {"product_url": product_url, "avatar_id": avatar_id, "retry_count": 0}
    create_manifest(new_id, {"job_id": new_id, **payload})
    r.rpush(JOBS_PENDING, json.dumps({"job_id": new_id, **payload}))
    return {"ok": True, "new_job_id": new_id}


_DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>KLIPAURA Mission Control</title>
  <style>
    :root {
      --bg: #0a0e14;
      --glass: rgba(255,255,255,.06);
      --border: rgba(255,255,255,.12);
      --text: #e8eef7;
      --muted: #8b9cb3;
      --accent: #3d9eff;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0; min-height: 100vh; font-family: system-ui, sans-serif;
      background: radial-gradient(1200px 800px at 20% 0%, #122030 0%, var(--bg) 55%);
      color: var(--text);
    }
    .wrap { max-width: 920px; margin: 0 auto; padding: 28px 18px 48px; }
    h1 { font-size: 1.25rem; font-weight: 600; letter-spacing: .02em; margin: 0 0 6px; }
    p.sub { margin: 0 0 22px; color: var(--muted); font-size: .9rem; }
    .panel {
      background: var(--glass); backdrop-filter: blur(14px);
      border: 1px solid var(--border); border-radius: 16px;
      padding: 16px; margin-bottom: 14px;
    }
    video {
      width: 100%; max-height: 70vh; border-radius: 12px;
      background: #000; border: 1px solid var(--border);
    }
    .row { display: flex; gap: 10px; flex-wrap: wrap; margin-top: 12px; }
    button {
      flex: 1; min-width: 120px; padding: 12px 14px; border-radius: 10px;
      border: 1px solid var(--border); background: rgba(255,255,255,.08);
      color: var(--text); cursor: pointer; font-weight: 600;
    }
    button:hover { background: rgba(255,255,255,.12); }
    button.primary { background: linear-gradient(180deg, #4fa8ff, var(--accent)); color: #061018; border: none; }
    button.danger { border-color: rgba(255,100,100,.4); color: #ffb4b4; }
    .meta { font-size: .8rem; color: var(--muted); word-break: break-all; margin-top: 8px; }
    .err { color: #ff8a8a; font-size: .85rem; margin-top: 8px; }
    .ops-head { display: flex; justify-content: space-between; align-items: center; gap: 10px; margin-bottom: 12px; flex-wrap: wrap; }
    .ops-head strong { font-size: 0.95rem; }
    .ops-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
      gap: 10px;
      margin-bottom: 14px;
    }
    .stat {
      background: rgba(0,0,0,.22);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 12px 14px;
    }
    .stat .n { font-size: 1.5rem; font-weight: 700; letter-spacing: -0.02em; }
    .stat .l { font-size: 0.72rem; color: var(--muted); text-transform: uppercase; letter-spacing: .06em; margin-top: 4px; }
    .pill-row { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 12px; }
    .pill {
      font-size: 0.78rem; padding: 6px 10px; border-radius: 999px;
      border: 1px solid var(--border); background: rgba(0,0,0,.2);
    }
    .pill.ok { border-color: rgba(80,200,140,.45); color: #9ee8c0; }
    .pill.warn { border-color: rgba(255,180,80,.45); color: #ffd49a; }
    .pill.bad { border-color: rgba(255,100,100,.4); color: #ffb4b4; }
    .section-title { font-size: 0.75rem; color: var(--muted); text-transform: uppercase; letter-spacing: .08em; margin: 14px 0 8px; }
    .hint {
      font-size: 0.85rem; line-height: 1.45; color: #c5d4e8;
      background: rgba(61, 158, 255, .12);
      border: 1px solid rgba(61, 158, 255, .25);
      border-radius: 12px; padding: 12px 14px; margin-bottom: 12px;
    }
    table.jobs { width: 100%; border-collapse: collapse; font-size: 0.82rem; }
    table.jobs th { text-align: left; color: var(--muted); font-weight: 600; padding: 8px 6px; border-bottom: 1px solid var(--border); }
    table.jobs td { padding: 10px 6px; border-bottom: 1px solid rgba(255,255,255,.06); vertical-align: top; }
    table.jobs code { font-size: 0.78rem; color: #b8d4ff; }
    .mono-sm { font-family: ui-monospace, monospace; font-size: 0.75rem; color: var(--muted); }
    .raw-json { font-family: ui-monospace, monospace; font-size: 0.7rem; color: var(--muted); white-space: pre-wrap; max-height: 220px; overflow: auto; margin-top: 10px; padding: 10px; background: rgba(0,0,0,.25); border-radius: 8px; display: none; }
    .raw-json.show { display: block; }
    a.link-muted { color: var(--accent); font-size: 0.8rem; }
    label.mc-label { display: block; font-size: 0.75rem; color: var(--muted); margin-bottom: 6px; margin-top: 10px; }
    label.mc-label:first-of-type { margin-top: 0; }
    input.mc-in {
      width: 100%; padding: 10px 12px; border-radius: 10px; border: 1px solid var(--border);
      background: rgba(0,0,0,.28); color: var(--text); font-size: 0.9rem;
    }
    .ok { color: #7dffb2; font-size: 0.85rem; margin-top: 8px; }
  </style>
</head>
<body>
  <div class="wrap">
    <h1>Mission Control</h1>
    <p class="sub">Queues, revenue rollup, and HITL review. <span id="mc-link"></span></p>
    <div class="panel">
      <div class="ops-head">
        <strong>Ops snapshot</strong>
        <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;">
          <button type="button" onclick="loadOps()">Refresh</button>
          <a href="#" class="link-muted" id="toggle-raw" onclick="toggleRawJson(); return false;">Show raw JSON</a>
        </div>
      </div>
      <div id="ops">Loading…</div>
      <pre class="raw-json" id="ops-raw"></pre>
    </div>
    <h2 style="font-size:1rem;font-weight:600;margin:20px 0 8px;letter-spacing:.02em;">Enqueue video job</h2>
    <p class="sub" style="margin-bottom:14px;">Paste an affiliate product page URL — worker runs the pipeline (extract → script → render). Requires worker + Redis running.</p>
    <div class="panel">
      <label class="mc-label" for="product-url">Product URL</label>
      <input class="mc-in" id="product-url" type="url" placeholder="https://www.amazon.com/dp/..." autocomplete="off"/>
      <label class="mc-label" for="avatar-id">Avatar ID</label>
      <input class="mc-in" id="avatar-id" type="text" value="" placeholder="avatar-id (leave blank for default)"/>
      <div class="row" style="margin-top:14px;">
        <button type="button" class="primary" onclick="enqueueJob()">Enqueue job</button>
      </div>
      <div class="err" id="enqueue-err"></div>
      <div class="ok" id="enqueue-ok"></div>
    </div>
    <h2 style="font-size:1rem;font-weight:600;margin:20px 0 8px;letter-spacing:.02em;">Phase 3 — Selector (products.csv)</h2>
    <p class="sub" style="margin-bottom:14px;">Scores rows from <code>products.csv</code> and enqueues top N jobs (same as <code>python -m klip_selector.selector_worker</code>). Requires <code>OPS_API_KEY</code> on the server and the key below.</p>
    <div class="panel">
      <label class="mc-label" for="ops-key">X-Ops-Key</label>
      <input class="mc-in" id="ops-key" type="password" placeholder="matches server OPS_API_KEY" autocomplete="off"/>
      <div class="row" style="margin-top:14px;">
        <button type="button" onclick="runSelector()">Run selector now</button>
        <button type="button" onclick="loadAutopilot()">Autopilot status</button>
      </div>
      <div id="ap-status" class="mono-sm" style="margin-top:10px;"></div>
      <div class="err" id="sel-err"></div>
      <div class="ok" id="sel-ok"></div>
    </div>
    <h2 style="font-size:1rem;font-weight:600;margin:20px 0 8px;letter-spacing:.02em;">HITL review</h2>
    <p class="sub" style="margin-bottom:14px;">Load next job from the queue. 9:16 preview — approve or reject.</p>
    <div class="panel">
      <video id="v" playsinline controls></video>
      <div class="meta" id="meta"></div>
      <div class="err" id="err"></div>
      <div class="row">
        <button type="button" onclick="loadNext()">Load next</button>
        <button type="button" class="primary" onclick="approve()">Approve</button>
        <button type="button" class="danger" onclick="reject()">Reject</button>
        <button type="button" onclick="regenerate()">Regenerate</button>
      </div>
    </div>
  </div>
  <script>
    let job = null;
    let lastOpsJson = null;
    function esc(s) {
      if (s == null || s === undefined) return '';
      return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
    }
    function pill(ok, label) {
      var c = ok ? 'ok' : 'warn';
      return '<span class="pill ' + c + '">' + esc(label) + '</span>';
    }
    function toggleRawJson() {
      var pre = document.getElementById('ops-raw');
      var a = document.getElementById('toggle-raw');
      pre.classList.toggle('show');
      a.textContent = pre.classList.contains('show') ? 'Hide raw JSON' : 'Show raw JSON';
    }
    async function loadOps() {
      const el = document.getElementById('ops');
      const raw = document.getElementById('ops-raw');
      const linkEl = document.getElementById('mc-link');
      el.innerHTML = '<p class="meta">Loading…</p>';
      try {
        const r = await fetch('/api/ops/summary');
        const j = await r.json();
        lastOpsJson = j;
        raw.textContent = JSON.stringify(j, null, 2);
        linkEl.textContent = '';
        if (j.mission_control_url) {
          const a = document.createElement('a');
          a.href = j.mission_control_url;
          a.target = '_blank';
          a.rel = 'noopener noreferrer';
          a.textContent = 'Open Core V1 UI';
          linkEl.appendChild(document.createTextNode(' '));
          linkEl.appendChild(a);
          linkEl.appendChild(document.createTextNode(' · '));
        }
        const site = j.public_site_url;
        if (site) {
          const a2 = document.createElement('a');
          a2.href = site;
          a2.target = '_blank';
          a2.rel = 'noopener noreferrer';
          a2.textContent = 'klipaura.com';
          linkEl.appendChild(a2);
        }
        var q = j.queues || {};
        var jp = q.jobs_pending != null ? q.jobs_pending : '—';
        var hp = q.hitl_pending != null ? q.hitl_pending : '—';
        var dq = q.dlq != null ? q.dlq : '—';
        var redisOk = j.redis_ok;
        var r2Ok = j.r2_configured;
        var prov = j.providers || {};
        var ff = prov.ffmpeg || {};
        var ak = prov.api_keys_present || {};
        var ffmpegOk = ff.on_path;
        var budget = j.budget || {};
        var rev = j.revenue || {};
        var html = '';
        if (j.pipeline_hint) {
          html += '<div class="hint">' + esc(j.pipeline_hint) + '</div>';
        }
        html += '<div class="pill-row">';
        html += pill(redisOk, 'Redis connected');
        html += pill(r2Ok, 'R2 configured');
        html += pill(ffmpegOk, ffmpegOk ? 'FFmpeg on PATH' : 'FFmpeg missing');
        html += pill(!!ak.wavespeed, ak.wavespeed ? 'WaveSpeed key set' : 'WaveSpeed key missing');
        html += pill(!!ak.elevenlabs, ak.elevenlabs ? 'ElevenLabs key set' : 'ElevenLabs key missing');
        html += pill(!!ak.groq, ak.groq ? 'Groq key set' : 'Groq key missing');
        html += '</div>';
        if (ff.version_line) {
          html += '<p class="mono-sm" style="margin:0 0 10px;">' + esc(ff.version_line) + '</p>';
        } else if (!ffmpegOk) {
          html += '<p class="err" style="margin-top:0;">FFmpeg not found on PATH. On Railway it is installed via Nixpacks (see repo <code>nixpacks.toml</code>). You do not need <code>FFMPEG_PATH</code> in <code>.env</code> unless you use a custom binary.</p>';
        }
        html += '<div class="section-title">Queues</div><div class="ops-grid">';
        html += '<div class="stat"><div class="n">' + esc(jp) + '</div><div class="l">Jobs pending</div></div>';
        html += '<div class="stat"><div class="n">' + esc(hp) + '</div><div class="l">HITL pending</div></div>';
        html += '<div class="stat"><div class="n">' + esc(dq) + '</div><div class="l">Dead letter</div></div>';
        if (budget.max_daily != null) {
          html += '<div class="stat"><div class="n">' + esc(String(budget.count_today != null ? budget.count_today : '—')) + '/' + esc(String(budget.max_daily)) + '</div><div class="l">Videos today</div></div>';
        }
        html += '</div>';
        html += '<div class="section-title">Revenue ledger</div>';
        html += '<p class="meta" style="margin:0 0 10px;">Entries: ' + esc(rev.total_entries != null ? rev.total_entries : 0) +
          ' · Est. USD: ' + esc(rev.est_revenue_usd_sum != null ? rev.est_revenue_usd_sum : 0) + '</p>';
        html += '<div class="section-title">Recent jobs</div>';
        var jobs = j.recent_jobs || [];
        if (!jobs.length) {
          html += '<p class="meta">No job manifests yet under <code>' + esc(j.jobs_dir || '') + '</code>.</p>';
        } else {
          html += '<table class="jobs"><thead><tr><th>Job</th><th>Status</th><th>Video</th><th>Updated</th></tr></thead><tbody>';
          for (var i = 0; i < jobs.length; i++) {
            var row = jobs[i];
            var vid = row.has_video ? 'Yes' : 'No';
            var st = row.status || '—';
            var jid = row.job_id || '—';
            html += '<tr><td><code>' + esc(jid) + '</code></td><td>' + esc(st) + '</td><td>' + esc(vid) + '</td><td class="mono-sm">' + esc(row.updated_at || '') + '</td></tr>';
            if (row.error) {
              html += '<tr><td colspan="4" class="err">' + esc(row.error) + '</td></tr>';
            }
          }
          html += '</tbody></table>';
        }
        html += '<p class="meta" style="margin-top:12px;">Jobs in <strong>PROCESSING</strong> mean the worker is running the pipeline (scraping → LLM → optional WaveSpeed I2V / ElevenLabs voice → FFmpeg). Empty queues with PROCESSING usually means work is in progress—watch Railway logs if it exceeds ~30 minutes.</p>';
        el.innerHTML = html;
      } catch (e) {
        el.textContent = String(e);
      }
    }
    document.addEventListener('DOMContentLoaded', function () { loadOps(); loadAutopilot(); });
    async function loadAutopilot() {
      const el = document.getElementById('ap-status');
      el.innerHTML = 'Loading…';
      try {
        const r = await fetch('/api/autopilot/status');
        const j = await r.json();
        var b = j.budget || {};
        var lines = [];
        lines.push('Autopilot: ' + (j.autopilot_enabled ? 'ON' : 'off'));
        lines.push('Selector UTC: ' + (j.selector_morning_utc || '') + ' / ' + (j.selector_evening_utc || ''));
        lines.push('Guardian: every ' + (j.guardian_interval_min || 30) + ' min');
        lines.push('Budget: ' + (b.count_today != null ? b.count_today : '—') + '/' + (b.max_daily != null ? b.max_daily : '—') + ' today');
        lines.push('OPS API: ' + (j.ops_api_configured ? 'configured' : 'not set'));
        el.innerHTML = '<div class="hint" style="margin:0;">' + lines.map(function (x) { return esc(x); }).join('<br/>') + '</div>';
      } catch (e) {
        el.textContent = String(e);
      }
    }
    async function runSelector() {
      const err = document.getElementById('sel-err');
      const ok = document.getElementById('sel-ok');
      const k = document.getElementById('ops-key').value.trim();
      err.textContent = '';
      ok.textContent = '';
      if (!k) { err.textContent = 'Enter X-Ops-Key (set OPS_API_KEY on the server first).'; return; }
      try {
        const r = await fetch('/api/selector/run', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-Ops-Key': k },
          body: '{}',
        });
        const raw = await r.text();
        let j = {};
        try { j = JSON.parse(raw); } catch (e) { err.textContent = raw || ('HTTP ' + r.status); return; }
        if (!r.ok) {
          err.textContent = typeof j.detail === 'string' ? j.detail : raw;
          return;
        }
        ok.textContent = 'Selector finished — check ops snapshot for queue depth.';
        loadOps();
        loadAutopilot();
      } catch (e) {
        err.textContent = String(e);
      }
    }
    async function enqueueJob() {
      const url = document.getElementById('product-url').value.trim();
      const avatar = (document.getElementById('avatar-id').value || '').trim();
      const err = document.getElementById('enqueue-err');
      const ok = document.getElementById('enqueue-ok');
      err.textContent = '';
      ok.textContent = '';
      if (!url || url.length < 8) { err.textContent = 'Enter a valid product URL (at least 8 characters).'; return; }
      try {
        const r = await fetch('/api/jobs', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ product_url: url, avatar_id: avatar }),
        });
        const raw = await r.text();
        let j = {};
        try { j = JSON.parse(raw); } catch (e) { err.textContent = raw || ('HTTP ' + r.status); return; }
        if (!r.ok) {
          err.textContent = typeof j.detail === 'string' ? j.detail : (Array.isArray(j.detail) ? j.detail.map(function (x) { return x.msg || JSON.stringify(x); }).join(' ') : raw);
          return;
        }
        ok.textContent = 'Queued job ' + j.job_id + ' — ensure worker is running; refresh ops in ~1 min.';
        document.getElementById('product-url').value = '';
        loadOps();
      } catch (e) {
        err.textContent = String(e);
      }
    }
    async function loadNext() {
      document.getElementById('err').textContent = '';
      const r = await fetch('/api/next-job');
      const j = await r.json();
      job = j.job;
      const v = document.getElementById('v');
      const meta = document.getElementById('meta');
      if (!job) { v.removeAttribute('src'); meta.textContent = 'No jobs in HITL queue (timeout).'; return; }
      meta.textContent = 'Job ' + job.job_id + ' — ' + (job.product_url || '');
      v.src = '/api/jobs/' + encodeURIComponent(job.job_id) + '/video';
    }
    async function approve() {
      if (!job) return;
      document.getElementById('err').textContent = '';
      const r = await fetch('/api/approve/' + encodeURIComponent(job.job_id), { method: 'POST' });
      if (!r.ok) { document.getElementById('err').textContent = await r.text(); return; }
      job = null;
      document.getElementById('meta').textContent = 'Approved.';
      document.getElementById('v').removeAttribute('src');
    }
    async function reject() {
      if (!job) return;
      document.getElementById('err').textContent = '';
      const r = await fetch('/api/reject/' + encodeURIComponent(job.job_id), { method: 'POST' });
      if (!r.ok) { document.getElementById('err').textContent = await r.text(); return; }
      job = null;
      document.getElementById('meta').textContent = 'Rejected + URL blacklisted 7d.';
      document.getElementById('v').removeAttribute('src');
    }
    async function regenerate() {
      if (!job) return;
      document.getElementById('err').textContent = '';
      const r = await fetch('/api/regenerate/' + encodeURIComponent(job.job_id), { method: 'POST' });
      const j = await r.json();
      if (!r.ok) { document.getElementById('err').textContent = await r.text(); return; }
      job = null;
      document.getElementById('meta').textContent = 'Re-queued as ' + j.new_job_id;
      document.getElementById('v').removeAttribute('src');
    }
  </script>
</body>
</html>
"""


@app.get("/")
def serve_landing():
    """Serve marketing landing page at root."""
    from fastapi.responses import FileResponse
    landing_path = os.path.join(os.path.dirname(__file__), "..", "landing-page", "index.html")
    if os.path.isfile(landing_path):
        return FileResponse(landing_path, media_type="text/html")
    # Fallback to mission control if landing page missing
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "index.html"), media_type="text/html")


@app.post("/api/waitlist")
def add_waitlist_lead(body: WaitlistBody) -> dict:
    """Add email to waitlist with name and source tracking."""
    try:
        from infrastructure.db import get_session
        from infrastructure.db_models import WaitlistLead
        import uuid
        
        with get_session() as sess:
            # Check if email already exists
            existing = sess.query(WaitlistLead).filter(
                WaitlistLead.email == body.email.strip().lower()
            ).first()
            
            if existing:
                return {
                    "ok": True,
                    "message": "Already on waitlist",
                    "status": existing.status
                }
            
            # Create new lead
            lead = WaitlistLead(
                id=uuid.uuid4(),
                name=body.name.strip() if body.name else None,
                email=body.email.strip().lower(),
                source=body.source or "landing_page",
                referred_by=body.referred_by,
                status="pending"
            )
            
            sess.add(lead)
            sess.commit()
            
            return {
                "ok": True,
                "message": "Added to waitlist successfully",
                "lead_id": str(lead.id),
                "status": "pending"
            }
            
    except Exception as e:
        raise HTTPException(500, f"Failed to add to waitlist: {str(e)}")


@app.get("/api/waitlist")
def get_waitlist_leads(request: Request) -> dict:
    """Get waitlist leads (admin only)."""
    _require_admin(request)
    
    try:
        from infrastructure.db import get_session
        from infrastructure.db_models import WaitlistLead
        
        with get_session() as sess:
            leads = sess.query(WaitlistLead).order_by(
                WaitlistLead.created_at.desc()
            ).limit(100).all()
            
            results = []
            for lead in leads:
                results.append({
                    "id": str(lead.id),
                    "name": lead.name,
                    "email": lead.email,
                    "source": lead.source,
                    "referred_by": lead.referred_by,
                    "status": lead.status,
                    "created_at": lead.created_at.isoformat() if lead.created_at else None,
                })
            
            return {
                "ok": True,
                "leads": results,
                "total": len(results)
            }
            
    except Exception as e:
        raise HTTPException(500, f"Failed to get waitlist: {str(e)}")


@app.get("/admin", response_class=HTMLResponse)
def dashboard(request: Request) -> str:
    """Password-protected admin dashboard."""
    _require_admin(request)

    try:
        p = _STATIC_DIR / "index.html"
        if p.is_file():
            return p.read_text(encoding="utf-8")
    except Exception:
        pass
    return _DASHBOARD_HTML


# ── /api/v1/avatars — AvatarLoader-backed management API ─────────────────────

@app.get("/api/v1/avatars")
def v1_list_avatars(request: Request) -> JSONResponse:
    """List all avatars from registry (any status). No auth required."""
    loader = AvatarLoader()
    return JSONResponse({"avatars": loader.list_all()})


@app.get("/api/v1/avatars/{avatar_id}")
def v1_get_avatar(avatar_id: str, request: Request) -> JSONResponse:
    """Return full social_config for avatar. Requires ADMIN_API_KEY."""
    _require_admin(request)
    aid = _safe_avatar_id(avatar_id)
    if not aid:
        raise HTTPException(400, "invalid avatar_id")
    loader = AvatarLoader()
    try:
        cfg = loader.load(aid)
    except KeyError:
        raise HTTPException(404, f"Avatar '{aid}' not found or missing social_config.json")
    # Redact secrets before returning
    safe, _ = _redact_social_config(cfg)
    return JSONResponse({"avatar_id": aid, "social_config": safe})


@app.post("/api/v1/avatars/{avatar_id}/pause")
def v1_pause_avatar(avatar_id: str, request: Request) -> JSONResponse:
    """Set avatar status=paused. Requires ADMIN_API_KEY."""
    _require_admin(request)
    aid = _safe_avatar_id(avatar_id)
    if not aid:
        raise HTTPException(400, "invalid avatar_id")
    loader = AvatarLoader()
    if not loader.update_status(aid, "paused"):
        raise HTTPException(404, f"Avatar '{aid}' not found in registry")
    return JSONResponse({"ok": True, "avatar_id": aid, "status": "paused"})


@app.post("/api/v1/avatars/{avatar_id}/resume")
def v1_resume_avatar(avatar_id: str, request: Request) -> JSONResponse:
    """Set avatar status=active. Requires ADMIN_API_KEY."""
    _require_admin(request)
    aid = _safe_avatar_id(avatar_id)
    if not aid:
        raise HTTPException(400, "invalid avatar_id")
    loader = AvatarLoader()
    if not loader.update_status(aid, "active"):
        raise HTTPException(404, f"Avatar '{aid}' not found in registry")
    return JSONResponse({"ok": True, "avatar_id": aid, "status": "active"})


@app.post("/api/v1/avatars/{avatar_id}/suspend")
def v1_suspend_avatar(avatar_id: str, request: Request) -> JSONResponse:
    """Set avatar status=suspended. Requires ADMIN_API_KEY."""
    _require_admin(request)
    aid = _safe_avatar_id(avatar_id)
    if not aid:
        raise HTTPException(400, "invalid avatar_id")
    loader = AvatarLoader()
    if not loader.update_status(aid, "suspended"):
        raise HTTPException(404, f"Avatar '{aid}' not found in registry")
    return JSONResponse({"ok": True, "avatar_id": aid, "status": "suspended"})


@app.delete("/api/v1/avatars/{avatar_id}")
def v1_delete_avatar(avatar_id: str, request: Request) -> JSONResponse:
    """Soft-delete: set avatar status=suspended (never hard-deletes files). Requires ADMIN_API_KEY."""
    _require_admin(request)
    aid = _safe_avatar_id(avatar_id)
    if not aid:
        raise HTTPException(400, "invalid avatar_id")
    loader = AvatarLoader()
    if not loader.update_status(aid, "suspended"):
        raise HTTPException(404, f"Avatar '{aid}' not found in registry")
    return JSONResponse({"ok": True, "avatar_id": aid, "status": "suspended",
                         "note": "Avatar suspended (files preserved on disk)"})
