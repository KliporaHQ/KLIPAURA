"""
KLIPAURA MISSION CONTROL - Master Backend
==========================================

FastAPI backend for the KLIPAURA OS Mission Control dashboard.
Provides real-time event streaming, job management, and module orchestration.

Endpoints:
    GET  /health                          - Health check
    GET  /api/v1/events                   - List events (SSE streaming)
    GET  /api/v1/jobs                     - List all jobs
    POST /api/v1/jobs                     - Create new job
    GET  /api/v1/jobs/{job_id}            - Get job details
    PUT  /api/v1/jobs/{job_id}            - Update job
    POST /api/v1/jobs/{job_id}/approve    - HITL approval
    POST /api/v1/jobs/{job_id}/reject     - HITL rejection
    GET  /api/v1/modules                 - List module status
    GET  /api/v1/modules/{name}           - Module details
    PUT  /api/v1/modules/{name}/toggle    - Enable/disable module
    POST /api/v1/kill-switch              - Trigger kill switch
    DELETE /api/v1/kill-switch            - Clear kill switch
    GET  /api/v1/metrics                  - System metrics
    WS   /ws/events                       - WebSocket for real-time updates

Author: Matrix Agent (KLIPAURA OS)
"""

import asyncio
import base64
import html as html_stdlib
import json
import logging
import os
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path


def _load_mc_dotenv() -> None:
    """Load repo or package `.env` so MC_ADMIN_* exist when running uvicorn without a shell script."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    here = Path(__file__).resolve().parent
    load_dotenv(here / ".env")
    load_dotenv(here.parent / ".env")


_load_mc_dotenv()
from typing import Any, Dict, List, Optional

import httpx
import redis.asyncio as redis
from fastapi import Body, FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect, Depends, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field, model_validator

from klip_core.redis.queues import QUEUE_NAMES
from klip_mc.compliance_policy import CompliancePolicy
from klip_mc.cost_events_store import append_from_ingest_payload, cap_status, job_cost_trace, per_avatar_spend, provider_summary
from klip_mc.decision_engine import Candidate, DecisionConfig, evaluate_candidate
from klip_mc.job_store import JobRow, close_job_store, init_job_store, job_store_enabled, load_all_job_rows, upsert_job_row
from klip_mc.scheduler import start_scheduler
from klip_mc.security import (
    create_access_token,
    get_effective_operator_username,
    login_password_configured,
    require_events_ingest,
    require_mc_operator,
    verify_login_password,
    verify_login_user,
    write_runtime_operator_creds,
)
from klip_mc.avatar_prompt import build_composite_prompt
from klip_mc.voice_presets import default_env_voice_id, resolve_voice_id_from_tone
from klip_mc.wavespeed_t2i import WaveSpeedT2IError, flux_dev_download_to_path
from klip_mc import wavespeed_lipsync as mc_wavespeed_lipsync
from klip_core.redis.client import get_redis_client_optional

logger = logging.getLogger("klip_mission_control")

def _cors_origins() -> list[str]:
    raw = (os.getenv("CORS_ORIGINS") or "http://localhost:3000").strip()
    return [x.strip() for x in raw.split(",") if x.strip()]

# ============================================================================
# Configuration
# ============================================================================

app = FastAPI(
    title="KLIPAURA Mission Control",
    description="Master orchestration backend for KLIPAURA OS",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS for Next.js frontend (use CORS_ORIGINS in production; avoid * with credentials)
_origins = _cors_origins()
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins if _origins else ["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================================================
# Pydantic Models
# ============================================================================

class ModuleName(str, Enum):
    SELECTOR = "klip-selector"
    AVATAR = "klip-avatar"
    FUNNEL = "klip-funnel"
    AVENTURE = "klip-aventure"
    TRADER = "klip-trader"

class EventSeverity(str, Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"

class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    AWAITING_HITL = "awaiting_hitl"

class KillSwitchScope(str, Enum):
    GLOBAL = "global"
    SELECTOR = "klip-selector"
    AVATAR = "klip-avatar"
    FUNNEL = "klip-funnel"
    AVENTURE = "klip-aventure"
    TRADER = "klip-trader"

# Event Model
class KlipEvent(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    module: ModuleName
    event_type: str
    severity: EventSeverity = EventSeverity.INFO
    message: str
    data: Dict[str, Any] = {}
    job_id: Optional[str] = None

# Job Models
class JobCreate(BaseModel):
    module: ModuleName
    job_type: str
    payload: Dict[str, Any] = {}
    priority: int = 0

class JobUpdate(BaseModel):
    status: Optional[JobStatus] = None
    progress: Optional[int] = None
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None

class Job(BaseModel):
    id: str
    module: ModuleName
    job_type: str
    status: JobStatus = JobStatus.PENDING
    progress: int = 0
    priority: int = 0
    payload: Dict[str, Any] = {}
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    hitl_requested: bool = False
    hitl_approved: Optional[bool] = None
    # Set on create_job when Redis LPUSH fails or succeeds (optional for list responses).
    redis_enqueued: Optional[bool] = None
    warning: Optional[str] = None


class LoginRequest(BaseModel):
    username: str = "admin"
    password: str


class AdminCredentialsUpdate(BaseModel):
    """Rotate MC operator login; persists ``mc_operator_credentials.json`` under ``JOBS_DIR``."""

    current_password: str
    new_username: Optional[str] = None
    new_password: Optional[str] = None


# Module Status
class ModuleStatus(BaseModel):
    name: ModuleName
    enabled: bool = True
    status: str = "unknown"
    last_heartbeat: Optional[datetime] = None
    jobs_processed: int = 0
    jobs_failed: int = 0
    uptime_seconds: int = 0

# Kill Switch
class KillSwitch(BaseModel):
    scope: KillSwitchScope
    active: bool
    triggered_by: Optional[str] = None
    reason: Optional[str] = None
    triggered_at: Optional[datetime] = None

class KillSwitchCreate(BaseModel):
    scope: KillSwitchScope = KillSwitchScope.GLOBAL
    reason: str


class ScannerRunBody(BaseModel):
    """Run affiliate scanner once and optionally LPUSH ranked jobs to ``jobs_pending``."""

    include_amazon: bool = True
    include_clickbank: bool = True
    include_temu: bool = True
    include_live_feeds: bool = False
    queue_limit: int = Field(5, ge=1, le=100)


class SelectorRunBody(BaseModel):
    """Run one selector CSV → score → queue cycle (same as selector worker ``run_cycle``)."""

    limit: int = Field(5, ge=1, le=50)
    avatar_id: str = Field("theanikaglow", min_length=1, max_length=128)


class AvatarCreate(BaseModel):
    avatar_id: str = Field(..., min_length=2, max_length=64)
    display_name: Optional[str] = None
    niche: Optional[str] = None
    voice_id: Optional[str] = None
    cta_line: Optional[str] = None
    script_system_override: Optional[str] = None
    content_tone: Optional[str] = None
    content_style: Optional[str] = None
    language: Optional[str] = None
    getlate_api_key: Optional[str] = None
    zerio_api_key: Optional[str] = None
    platform_profiles: Optional[Dict[str, str]] = None
    affiliate_amazon_tag: Optional[str] = None
    affiliate_tiktok_shop_id: Optional[str] = None


class AvatarSummary(BaseModel):
    avatar_id: str
    display_name: str
    niche: Optional[str] = None
    has_persona: bool = False
    has_social_config: bool = False
    has_portrait: bool = False
    updated_at: Optional[str] = None


class AvatarDetail(AvatarSummary):
    persona: Dict[str, Any] = {}
    social_config: Dict[str, Any] = {}


class AvatarSocialUpdate(BaseModel):
    getlate_api_key: Optional[str] = None
    zerio_api_key: Optional[str] = None
    platform_profiles: Optional[Dict[str, str]] = None
    elevenlabs_voice_id: Optional[str] = None
    affiliate_amazon_tag: Optional[str] = None
    affiliate_tiktok_shop_id: Optional[str] = None


class AvatarPersonaUpdate(BaseModel):
    display_name: Optional[str] = None
    niche: Optional[str] = None
    cta_line: Optional[str] = None


class PreviewScriptBody(BaseModel):
    product_url: str = Field(..., min_length=8)
    avatar_id: Optional[str] = "theanikaglow"
    product_title: Optional[str] = Field(
        None,
        max_length=2000,
        description="Optional override: paste the Amazon product title if preview invents the wrong item.",
    )


class PreviewTtsBody(BaseModel):
    text: str = Field(..., min_length=1, max_length=2000)
    voice_id: Optional[str] = None
    avatar_id: Optional[str] = None


class AvatarStudioGenerateRequest(BaseModel):
    """Avatar Studio: prompt + optional presets → generated portrait + persona record."""

    prompt: str = Field(..., min_length=1, max_length=2000)
    name: Optional[str] = Field(None, max_length=200)
    age: Optional[str] = Field(None, max_length=120)
    look: Optional[str] = Field(None, max_length=200)
    outfit: Optional[str] = Field(None, max_length=200)
    personality: Optional[str] = Field(None, max_length=200)
    voice_tone: Optional[str] = Field(None, max_length=120)
    voice_id: Optional[str] = Field(None, max_length=128)


class AvatarStudioGenerateResponse(BaseModel):
    persona_id: str
    avatar_id: str
    image_url: str
    voice_id: str
    created_at: str


class AvatarStudioTestLipsyncRequest(BaseModel):
    persona_id: Optional[str] = None
    image_url: Optional[str] = None
    text: Optional[str] = Field(None, max_length=2000)
    voice_id: Optional[str] = None

    @model_validator(mode="after")
    def _one_ref(self) -> "AvatarStudioTestLipsyncRequest":
        pid = (self.persona_id or "").strip()
        iu = (self.image_url or "").strip()
        if not pid and not iu:
            raise ValueError("Provide persona_id or image_url")
        return self


class AvatarStudioTestLipsyncResponse(BaseModel):
    clip_url: str


# Thread pool for sync scanner/selector (blocking Redis + HTTP in scanner path)
_PIPELINE_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="mc-pipeline")


def _sync_scanner_run(body: ScannerRunBody) -> Dict[str, Any]:
    from klip_scanner.scanner_service import run_scanner

    return run_scanner(
        include_amazon=body.include_amazon,
        include_clickbank=body.include_clickbank,
        include_temu=body.include_temu,
        include_live_feeds=body.include_live_feeds,
        enqueue=True,
        queue_limit=body.queue_limit,
    )


def _sync_selector_run(body: SelectorRunBody) -> int:
    from klip_selector.selector_worker import run_cycle

    return run_cycle(limit=body.limit, avatar_id=body.avatar_id)


# Metrics
class SystemMetrics(BaseModel):
    total_jobs: int
    jobs_by_status: Dict[str, int]
    jobs_by_module: Dict[str, int]
    events_last_hour: int
    active_kill_switches: List[str]
    redis_connected: bool
    uptime_seconds: int


class WorkerHeartbeatStatus(BaseModel):
    worker: str
    online: bool
    state: Optional[str] = None
    job_id: Optional[str] = None
    last_seen: Optional[str] = None
    seconds_since_last_seen: Optional[int] = None
    queue_depth: int = 0
    note: Optional[str] = None


class DecisionRoute(str, Enum):
    AUTO_APPROVE = "AUTO_APPROVE"
    MANUAL_REVIEW = "MANUAL_REVIEW"
    AUTO_REJECT = "AUTO_REJECT"


class DecisionStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    AUTO_APPROVED = "AUTO_APPROVED"
    AUTO_REJECTED = "AUTO_REJECTED"


class DecisionConfigModel(BaseModel):
    auto_approve_threshold: float = Field(0.8, ge=0.0, le=1.0)
    manual_review_threshold: float = Field(0.5, ge=0.0, le=1.0)
    pregen_hitl_required: bool = True
    remaining_budget: float = Field(100.0, ge=0.0)


class DecisionEvaluateRequest(BaseModel):
    avatar_id: str = Field("theanikaglow", min_length=1, max_length=128)
    product_url: str = Field(..., min_length=1, max_length=2048)
    title: Optional[str] = ""
    category: Optional[str] = ""
    affiliate_tracking_id: Optional[str] = ""
    trend_score: float = Field(0.5, ge=0.0, le=1.0)
    commission_rate: float = Field(0.0, ge=0.0, le=100.0)
    estimated_cost: float = Field(0.0, ge=0.0)
    has_crosspost_risk: bool = False
    metadata: Dict[str, Any] = {}


class DecisionActionRequest(BaseModel):
    note: Optional[str] = ""


class DecisionRecord(BaseModel):
    id: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    status: DecisionStatus
    route: DecisionRoute
    avatar_id: str
    product_url: str
    title: str = ""
    category: str = ""
    affiliate_tracking_id: str = ""
    final_score: float
    component_scores: Dict[str, float] = {}
    hard_gates: Dict[str, Dict[str, Any]] = {}
    explainability: Dict[str, Any] = {}
    config_snapshot: Dict[str, Any] = {}
    metadata: Dict[str, Any] = {}

# ============================================================================
# Application State
# ============================================================================

class AppState:
    def __init__(self):
        self.redis: Optional[redis.Redis] = None
        self.jobs: Dict[str, Job] = {}
        self.modules: Dict[str, ModuleStatus] = {}
        self.kill_switches: Dict[str, KillSwitch] = {}
        # websocket_connections removed — unused (manager.active_connections is the real list)
        self.decision_global_config: DecisionConfigModel = DecisionConfigModel()
        self.decision_avatar_config: Dict[str, DecisionConfigModel] = {}
        self.decisions: Dict[str, DecisionRecord] = {}
        self.started_at = datetime.utcnow()

state = AppState()


def _job_row_to_job(row: JobRow) -> Job:
    return Job(
        id=row.id,
        module=ModuleName(row.module),
        job_type=row.job_type,
        status=JobStatus(row.status),
        progress=row.progress,
        priority=row.priority,
        payload=row.payload or {},
        result=row.result,
        error=row.error,
        created_at=row.created_at,
        updated_at=row.updated_at,
        hitl_requested=row.hitl_requested,
        hitl_approved=row.hitl_approved,
    )


async def _persist_job_model(job: Job) -> None:
    if not job_store_enabled():
        return
    await upsert_job_row(
        job_id=job.id,
        module=job.module.value,
        job_type=job.job_type,
        status=job.status.value,
        progress=job.progress,
        priority=job.priority,
        payload=job.payload,
        result=job.result,
        error=job.error,
        hitl_requested=job.hitl_requested,
        hitl_approved=job.hitl_approved,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


# Queue names (canonical: klip_core.redis.queues.QUEUE_NAMES)
QUEUE_JOBS_PENDING = QUEUE_NAMES.jobs_pending
QUEUE_JOBS_HITL = QUEUE_NAMES.hitl_pending
QUEUE_JOBS_DLQ = QUEUE_NAMES.dlq
KEY_KILL_GLOBAL = QUEUE_NAMES.kill_global
KEY_EVENTS_LOG = QUEUE_NAMES.events_log
KEY_MODULE_STATUS = "klipaura:modules:status"
GLOBAL_QUEUE_PAUSED_KEY = QUEUE_NAMES.global_queue_paused


def _avatar_id_safe(raw: str) -> str:
    return "".join(c for c in (raw or "").strip().lower() if c.isalnum() or c in ("-", "_"))


def _avatar_dirs_candidates() -> list[Path]:
    here = Path(__file__).resolve().parent
    cwd = Path.cwd()
    return [
        Path(os.getenv("AVATAR_DATA_DIR", "")).resolve() if os.getenv("AVATAR_DATA_DIR") else None,  # type: ignore[arg-type]
        here / "klip-avatar" / "klip_avatar" / "core_v1" / "data" / "avatars",
        here.parent / "klip-avatar" / "klip_avatar" / "core_v1" / "data" / "avatars",
        here.parent / "klip-avatar" / "core_v1" / "data" / "avatars",
        cwd / "klip-avatar" / "klip_avatar" / "core_v1" / "data" / "avatars",
        cwd / "klip-avatar" / "core_v1" / "data" / "avatars",
        Path("/app/klip-avatar/klip_avatar/core_v1/data/avatars"),
        Path("/app/klip-avatar/core_v1/data/avatars"),
    ]


def _avatar_root() -> Path:
    for p in _avatar_dirs_candidates():
        if p is None:
            continue
        if p.is_dir():
            return p
    # Last-resort writable fallback for first-run local dev
    p = (Path.cwd() / "klip-avatar" / "klip_avatar" / "core_v1" / "data" / "avatars").resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def _load_json(path: Path) -> Dict[str, Any]:
    try:
        if not path.is_file():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _avatar_summary_from_dir(d: Path) -> AvatarSummary:
    persona = _load_json(d / "persona.json")
    social = _load_json(d / "social_config.json")
    portrait = any((d / n).is_file() for n in ("portrait.png", "face.png", "avatar.png", "profile.png"))
    display_name = (
        str(persona.get("display_name") or persona.get("name") or d.name).strip() or d.name
    )
    niche = str(persona.get("niche") or "").strip() or None
    updated = None
    try:
        updated = datetime.utcfromtimestamp(d.stat().st_mtime).isoformat() + "Z"
    except Exception:
        updated = None
    return AvatarSummary(
        avatar_id=d.name,
        display_name=display_name,
        niche=niche,
        has_persona=(d / "persona.json").is_file(),
        has_social_config=(d / "social_config.json").is_file(),
        has_portrait=portrait,
        updated_at=updated,
    )


def _avatar_dir_or_404(avatar_id: str) -> Path:
    aid = _avatar_id_safe(avatar_id)
    if not aid:
        raise HTTPException(status_code=400, detail="Invalid avatar id")
    d = _avatar_root() / aid
    if not d.is_dir():
        raise HTTPException(status_code=404, detail="Avatar not found")
    return d


def _sync_avatar_assets_to_r2(avatar_id: str, d: Path) -> None:
    """Push avatar files to R2 so ``klip-avatar`` worker can hydrate when it runs in another container."""
    try:
        from klip_core.storage.r2 import r2_configured, create_r2_store

        if not r2_configured():
            return
        store = create_r2_store()
        aid = _avatar_id_safe(avatar_id)
        for name, ct in (
            ("persona.json", "application/json"),
            ("social_config.json", "application/json"),
            ("portrait.png", "image/png"),
            ("face.png", "image/png"),
        ):
            fp = d / name
            if fp.is_file():
                store.upload(str(fp.resolve()), f"avatars/{aid}/{name}", content_type=ct)
    except Exception as e:
        logger.warning("avatar R2 sync failed: %s", e)


def _unique_avatar_slug(proposed: str) -> str:
    """Return a new directory name under ``_avatar_root()`` that does not yet exist."""
    base = _avatar_id_safe(proposed) or "avatar"
    root = _avatar_root()
    if not (root / base).exists():
        return base
    for i in range(2, 10000):
        cand = f"{base}{i}"
        if not (root / cand).exists():
            return cand
    return f"{base}{uuid.uuid4().hex[:8]}"


def _public_avatar_portrait_url(avatar_id: str) -> Optional[str]:
    """HTTPS URL for ``avatars/{avatar_id}/portrait.png`` when R2 is configured."""
    try:
        from klip_core.storage.r2 import create_r2_store, r2_configured

        if not r2_configured():
            return None
        store = create_r2_store()
        aid = _avatar_id_safe(avatar_id)
        return store.get_public_url(f"avatars/{aid}/portrait.png")
    except Exception:
        return None


def _load_avatar_persona_redis(persona_id: str) -> Optional[dict[str, Any]]:
    r = get_redis_client_optional()
    if r is None:
        return None
    raw = r.get(f"avatar:persona:{persona_id.strip()}")
    if not raw:
        return None
    try:
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None


def _list_avatar_studio_personas() -> list[dict[str, Any]]:
    """All saved Avatar Studio personas from Redis index (newest first)."""
    r = get_redis_client_optional()
    if r is None:
        return []
    members = r.smembers("avatar:persona:index")
    out: list[dict[str, Any]] = []
    for pid in members:
        p = str(pid).strip()
        if not p:
            continue
        doc = _load_avatar_persona_redis(p)
        if doc:
            out.append(doc)
    out.sort(key=lambda d: str(d.get("created_at") or ""), reverse=True)
    return out


def _save_avatar_persona_redis(persona_id: str, doc: dict[str, Any]) -> None:
    r = get_redis_client_optional()
    if r is None:
        raise RuntimeError("Redis is not available")
    key = f"avatar:persona:{persona_id.strip()}"
    if not r.set(key, json.dumps(doc, default=str)):
        raise RuntimeError("Failed to write persona to Redis")
    r.sadd("avatar:persona:index", persona_id.strip())


def _avatar_studio_generate_sync(body: AvatarStudioGenerateRequest) -> dict[str, Any]:
    """Blocking path: disk + WaveSpeed + R2 + Redis."""
    from klip_core.storage.r2 import r2_configured

    if not r2_configured():
        raise RuntimeError("Cloud storage is not configured for Avatar Studio")
    if get_redis_client_optional() is None:
        raise RuntimeError("Redis is not available for Avatar Studio")

    display_name = (body.name or "").strip() or "AI Persona"
    avatar_id = _unique_avatar_slug(display_name)
    persona_id = str(uuid.uuid4())
    created_at = datetime.utcnow().isoformat() + "Z"

    voice_id = (body.voice_id or "").strip()
    if not voice_id:
        voice_id = resolve_voice_id_from_tone(body.voice_tone, avatar_id=None) or ""
    if not voice_id:
        voice_id = default_env_voice_id() or ""
    if not voice_id:
        raise RuntimeError("No ElevenLabs voice is configured; set voice_id, voice_tone presets, or ELEVENLABS_VOICE_ID")

    composite = build_composite_prompt(
        body.prompt,
        age=body.age,
        look=body.look,
        outfit=body.outfit,
        personality=body.personality,
        voice_tone=body.voice_tone,
    )

    root = _avatar_root()
    d = root / avatar_id
    d.mkdir(parents=True, exist_ok=True)

    out_png = str((d / "portrait.png").resolve())
    flux_dev_download_to_path(composite, out_png)

    face = d / "face.png"
    if not face.exists():
        try:
            face.write_bytes((d / "portrait.png").read_bytes())
        except Exception:
            pass

    persona_doc: dict[str, Any] = {
        "id": avatar_id,
        "display_name": display_name,
        "name": display_name,
        "niche": "",
        "cta_line": "",
        "created_by": "avatar_studio",
        "created_at": created_at,
        "avatar_studio_persona_id": persona_id,
        "avatar_image_prompt": body.prompt.strip(),
        "avatar_image_prompt_composite": composite,
        "avatar_image_provider": "wavespeed",
        "age": (body.age or "").strip(),
        "look": (body.look or "").strip(),
        "outfit": (body.outfit or "").strip(),
        "personality": (body.personality or "").strip(),
        "voice_tone": (body.voice_tone or "").strip(),
    }
    (d / "persona.json").write_text(json.dumps(persona_doc, indent=2), encoding="utf-8")

    social: dict[str, Any] = {"elevenlabs_voice_id": voice_id}
    (d / "social_config.json").write_text(json.dumps(social, indent=2), encoding="utf-8")

    _sync_avatar_assets_to_r2(avatar_id, d)
    image_url = _public_avatar_portrait_url(avatar_id)
    if not image_url:
        raise RuntimeError("Could not resolve public image URL after upload")

    redis_doc: dict[str, Any] = {
        "persona_id": persona_id,
        "avatar_id": avatar_id,
        "name": display_name,
        "prompt": body.prompt.strip(),
        "prompt_composite": composite,
        "image_url": image_url,
        "voice_id": voice_id,
        "created_at": created_at,
        "age": (body.age or "").strip(),
        "look": (body.look or "").strip(),
        "outfit": (body.outfit or "").strip(),
        "personality": (body.personality or "").strip(),
        "voice_tone": (body.voice_tone or "").strip(),
    }
    _save_avatar_persona_redis(persona_id, redis_doc)

    return {
        "persona_id": persona_id,
        "avatar_id": avatar_id,
        "image_url": image_url,
        "voice_id": voice_id,
        "created_at": created_at,
    }


def _elevenlabs_tts_to_file(text: str, voice_id: str, out_mp3: str) -> None:
    import requests

    el_key = (os.getenv("ELEVENLABS_API_KEY") or os.getenv("XI_API_KEY") or "").strip()
    if not el_key:
        raise RuntimeError("ElevenLabs is not configured")
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    snippet = (text or "").strip()[:1200]
    if not snippet:
        raise RuntimeError("Sample text is empty")
    r = requests.post(
        url,
        headers={"xi-api-key": el_key, "Content-Type": "application/json", "Accept": "audio/mpeg"},
        json={
            "text": snippet,
            "model_id": os.getenv("ELEVENLABS_MODEL_ID", "eleven_multilingual_v2"),
        },
        timeout=90,
    )
    if r.status_code >= 400:
        raise RuntimeError("Voice generation failed; check voice settings")
    if len(r.content or b"") < 100:
        raise RuntimeError("Voice generation returned invalid audio")
    Path(out_mp3).parent.mkdir(parents=True, exist_ok=True)
    Path(out_mp3).write_bytes(r.content)


def _avatar_studio_test_lipsync_sync(body: AvatarStudioTestLipsyncRequest) -> dict[str, Any]:
    from klip_core.storage.r2 import create_r2_store, r2_configured

    ws_key = (os.getenv("WAVESPEED_API_KEY") or "").strip()
    if not ws_key:
        raise RuntimeError("WaveSpeed is not configured")

    image_url = (body.image_url or "").strip()
    voice_id = (body.voice_id or "").strip()
    pid = (body.persona_id or "").strip()

    if pid and not image_url:
        doc = _load_avatar_persona_redis(pid)
        if not doc:
            raise RuntimeError("Persona not found")
        image_url = str(doc.get("image_url") or "").strip()
        if not voice_id:
            voice_id = str(doc.get("voice_id") or "").strip()
    if not image_url:
        raise RuntimeError("No image URL available for lip-sync")
    if not voice_id:
        voice_id = default_env_voice_id() or ""
    if not voice_id:
        raise RuntimeError("No voice is configured for this test")

    default_line = (
        "Hey everyone — here is a quick honest take on a product you might actually use every day."
    )
    text = (body.text or "").strip() or default_line

    import tempfile

    tmp = Path(tempfile.mkdtemp(prefix="mc-lipsync-"))
    mp3 = str(tmp / "sample.mp3")
    mp4 = str(tmp / "clip.mp4")
    try:
        _elevenlabs_tts_to_file(text, voice_id, mp3)
        out, err = mc_wavespeed_lipsync.generate_lipsync_video_to_path(
            image_url,
            mp3,
            ws_key,
            mp4,
            max_wait=int(os.getenv("WAVESPEED_LIPSYNC_MAX_WAIT", "720")),
        )
        if not out or err:
            raise RuntimeError(err or "Lip-sync generation failed")

        clip_url: str
        if r2_configured():
            store = create_r2_store()
            folder = pid if pid else uuid.uuid4().hex[:12]
            key = f"studio/previews/{folder}/{uuid.uuid4().hex[:12]}.mp4"
            clip_url = store.upload(out, key, content_type="video/mp4")
        else:
            raise RuntimeError("Cloud storage is not configured; cannot publish lip-sync preview URL")

        return {"clip_url": clip_url}
    finally:
        try:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)
        except Exception:
            pass


_compliance_policy = CompliancePolicy()


def _decision_config_for_avatar(avatar_id: str) -> DecisionConfigModel:
    aid = _avatar_id_safe(avatar_id) or avatar_id.strip().lower()
    return state.decision_avatar_config.get(aid, state.decision_global_config)


def _mc_redis_url_resolved() -> str:
    return (
        os.getenv("REDIS_URL")
        or os.getenv("UPSTASH_REDIS_URL")
        or os.getenv("KV_URL")
        or "redis://localhost:6379"
    ).strip()


def _redact_redis_url(url: str) -> str:
    try:
        if "@" in url and "://" in url:
            proto, rest = url.split("://", 1)
            if "@" in rest:
                hostpart = rest.split("@")[-1]
                return f"{proto}://***@{hostpart}"
    except Exception:
        pass
    return url if len(url) <= 96 else url[:96] + "…"


def _templates_json_path() -> Path:
    override = (os.getenv("VIDEO_TEMPLATES_JSON") or "").strip()
    if override:
        return Path(override)
    here = Path(__file__).resolve().parent
    root = here.parent
    canonical = root / "config" / "templates.json"
    if canonical.is_file():
        return canonical
    legacy = root / "klip-avatar" / "klip_avatar" / "data" / "templates.json"
    if legacy.is_file():
        return legacy
    return Path("/app/data/video_templates.json")


# ============================================================================
# Redis Connection
# ============================================================================

async def get_redis() -> redis.Redis:
    """Get Redis connection."""
    if state.redis is None:
        # Mission Control async paths require a real Redis URL (redis:// or rediss://).
        # Upstash REST credentials are handled by klip_core for sync workers, not here.
        redis_url = _mc_redis_url_resolved()
        rest_only = (os.getenv("UPSTASH_REDIS_REST_URL") or "").strip()
        if rest_only.lower().startswith("https://") and not (
            (os.getenv("REDIS_URL") or "").strip()
            or (os.getenv("UPSTASH_REDIS_URL") or "").strip()
            or (os.getenv("KV_URL") or "").strip()
        ):
            logger.warning(
                "UPSTASH_REDIS_REST_URL is set but Mission Control requires a TCP Redis URL "
                "(redis:// or rediss:// from Upstash dashboard). Metrics and job queue will fail until REDIS_URL is set."
            )
        logger.info("Mission Control connecting to Redis (redacted): %s", _redact_redis_url(redis_url))
        # Bounded connect/socket timeouts — a hung Redis (wrong URL / network) must not
        # block lifespan startup forever (Next serves /health; API details via /api/health).
        state.redis = redis.from_url(
            redis_url,
            decode_responses=True,
            socket_connect_timeout=float(os.getenv("REDIS_CONNECT_TIMEOUT", "5")),
            socket_timeout=float(os.getenv("REDIS_SOCKET_TIMEOUT", "5")),
        )

    return state.redis


async def reset_redis_connection() -> None:
    """Drop the cached client so the next ``get_redis()`` opens a new TCP connection."""
    if state.redis is None:
        return
    client = state.redis
    state.redis = None
    try:
        await client.aclose()
    except Exception:
        pass


def _redis_error_hint(exc: BaseException) -> str:
    msg = str(exc).strip()
    if len(msg) > 180:
        msg = msg[:177] + "..."
    return f"{type(exc).__name__}: {msg}" if msg else type(exc).__name__

# ============================================================================
# WebSocket Manager
# ============================================================================

class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                pass

manager = ConnectionManager()

# ============================================================================
# Health Check
# ============================================================================

@app.post("/api/events/ingest", dependencies=[Depends(require_events_ingest)])
async def ingest_event(payload: Dict[str, Any] = Body(...)):
    """All modules POST events here. MC stores and streams to dashboard."""
    try:
        r = await get_redis()
        await r.xadd("klipaura:events:stream", {"data": json.dumps(payload)})
        await r.lpush("klipaura:events:log", json.dumps(payload))
        await r.ltrim("klipaura:events:log", 0, 99)
    except Exception as e:
        logger.warning("Event ingest failed — Redis unavailable: %s", e)
    try:
        append_from_ingest_payload(payload)
    except Exception as e:
        logger.debug("cost_events ingest skip: %s", e)
    return {"status": "ok"}


@app.get("/api/events/stream")
async def event_stream():
    """SSE endpoint — dashboard subscribes for live module events."""

    async def generate():
        try:
            r = await get_redis()
            last_id = "$"
            while True:
                try:
                    results = await r.xread(
                        {"klipaura:events:stream": last_id},
                        block=1000,
                        count=10,
                    )
                    if results:
                        for _stream, messages in results:
                            for msg_id, data in messages:
                                last_id = msg_id
                                raw = data.get("data") or "{}"
                                if isinstance(raw, bytes):
                                    raw = raw.decode("utf-8", errors="replace")
                                yield f"data: {raw}\n\n"
                except Exception:
                    await asyncio.sleep(1)
        except Exception:
            yield 'data: {"error": "stream unavailable"}\n\n'

    return StreamingResponse(generate(), media_type="text/event-stream")


@app.post("/api/v1/auth/login")
async def auth_login(body: LoginRequest) -> Dict[str, Any]:
    """Return a JWT when ``MC_ADMIN_USER`` / ``MC_ADMIN_PASSWORD`` match (see ``.env.example``)."""
    if not login_password_configured():
        raise HTTPException(
            status_code=503,
            detail="Server misconfigured: set MC_ADMIN_PASSWORD or ADMIN_PASSWORD in the environment",
        )
    if not verify_login_user(body.username) or not verify_login_password(body.password):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = create_access_token(subject=body.username)
    return {"access_token": token, "token_type": "bearer"}


@app.put("/api/v1/admin/credentials", dependencies=[Depends(require_mc_operator)])
async def admin_update_credentials(body: AdminCredentialsUpdate) -> Dict[str, Any]:
    """
    Update operator username/password stored on the Mission Control filesystem.

    Railway / Docker: mount persistent volume on ``JOBS_DIR`` or changes are lost on redeploy.
    Platform env vars (``MC_ADMIN_*``) remain the bootstrap; runtime file overrides them when present.
    """
    if not body.new_username and not body.new_password:
        raise HTTPException(status_code=400, detail="Provide new_username and/or new_password")
    if not verify_login_password(body.current_password):
        raise HTTPException(status_code=401, detail="current_password does not match")
    user = (body.new_username or "").strip() or get_effective_operator_username()
    pwd = body.new_password if body.new_password is not None else body.current_password
    if len(pwd) < 8:
        raise HTTPException(status_code=400, detail="new_password must be at least 8 characters")
    try:
        path = write_runtime_operator_creds(username=user, password=pwd)
    except ValueError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    return {
        "status": "ok",
        "path": str(path),
        "username": user,
        "note": "Redeploy with matching platform env vars for multi-instance consistency.",
    }


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    redis_connected = False
    try:
        r = await get_redis()
        await r.ping()
        redis_connected = True
    except Exception:
        pass
    
    return {
        "status": "healthy" if redis_connected else "degraded",
        "version": "1.0.0",
        "redis_connected": redis_connected,
        "uptime_seconds": (datetime.utcnow() - state.started_at).total_seconds()
    }


@app.get("/api/health")
async def health_check_api_alias():
    """Same as GET /health. Next.js rewrites and some edges forward ``/api/health`` here (not ``/health``)."""
    return await health_check()


# ============================================================================
# Events API
# ============================================================================

@app.get(
    "/api/v1/events",
    dependencies=[Depends(require_mc_operator)],
)
async def list_events(
    limit: int = Query(100, ge=1, le=1000),
    module: Optional[str] = None,
    severity: Optional[str] = None,
    since: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """List recent events (ring buffer ``klipaura:events:log``, newest first)."""
    try:
        r = await get_redis()
        raw_items = await r.lrange(KEY_EVENTS_LOG, 0, max(0, limit - 1))
        result: List[Dict[str, Any]] = []
        for raw in raw_items or []:
            try:
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8", errors="replace")
                ev = json.loads(raw)
                if not isinstance(ev, dict):
                    continue
                if module and str(ev.get("module", "")).strip().lower() != module.strip().lower():
                    continue
                if severity and str(ev.get("severity", "")).strip().lower() != severity.strip().lower():
                    continue
                if since:
                    ts_raw = ev.get("timestamp")
                    if ts_raw:
                        try:
                            if isinstance(ts_raw, (int, float)):
                                ts_dt = datetime.utcfromtimestamp(float(ts_raw))
                            else:
                                ts_dt = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
                            if ts_dt < since:
                                continue
                        except Exception:
                            pass
                result.append(ev)
            except Exception:
                continue
        return result
    except Exception as e:
        logger.warning("list_events: Redis unavailable: %s", e)
        return []


# NOTE: legacy /api/v1/events/stream handler removed — it was @app.get with a WebSocket
# signature (broken).  The live SSE endpoint is /api/events/stream (above).


# ============================================================================
# Avatars API
# ============================================================================

@app.get("/api/v1/avatars", response_model=List[AvatarSummary], dependencies=[Depends(require_mc_operator)])
async def list_avatars() -> List[AvatarSummary]:
    root = _avatar_root()
    out: List[AvatarSummary] = []
    for p in sorted((x for x in root.iterdir() if x.is_dir()), key=lambda x: x.name.lower()):
        out.append(_avatar_summary_from_dir(p))
    return out


@app.get("/api/v1/templates", dependencies=[Depends(require_mc_operator)])
async def list_video_templates() -> List[Dict[str, Any]]:
    """UGC scene templates from ``klip_avatar/data/templates.json`` (also copied to /app/data/video_templates.json in Docker)."""
    path = _templates_json_path()
    if not path.is_file():
        raise HTTPException(
            status_code=503,
            detail=f"templates.json not found at {path} (set VIDEO_TEMPLATES_JSON or rebuild image with templates).",
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/api/v1/avatars/{avatar_id}", response_model=AvatarDetail, dependencies=[Depends(require_mc_operator)])
async def get_avatar(avatar_id: str) -> AvatarDetail:
    aid = _avatar_id_safe(avatar_id)
    d = _avatar_dir_or_404(avatar_id)
    summary = _avatar_summary_from_dir(d)
    return AvatarDetail(
        **summary.model_dump(),
        persona=_load_json(d / "persona.json"),
        social_config=_load_json(d / "social_config.json"),
    )


@app.get("/api/v1/avatars/{avatar_id}/portrait", dependencies=[Depends(require_mc_operator)])
async def get_avatar_portrait_image(avatar_id: str):
    """First portrait/face image for gallery thumbnails (authenticated fetch)."""
    d = _avatar_dir_or_404(avatar_id)
    for name in ("portrait.png", "face.png", "avatar.png", "profile.png"):
        p = d / name
        if p.is_file():
            ext = p.suffix.lower()
            mt = "image/jpeg" if ext in (".jpg", ".jpeg") else "image/png" if ext == ".png" else "image/webp"
            return FileResponse(p, media_type=mt)
    try:
        for p in sorted(d.iterdir(), key=lambda x: x.name):
            if p.is_file() and p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
                ext = p.suffix.lower()
                mt = "image/jpeg" if ext in (".jpg", ".jpeg") else "image/png" if ext == ".png" else "image/webp"
                return FileResponse(p, media_type=mt)
    except OSError:
        pass
    raise HTTPException(status_code=404, detail="No portrait image for this avatar")


def _looks_like_useless_amazon_shell_title(title: str) -> bool:
    """Amazon often returns a bare storefront title (``Amazon.ae``) to bots — not a product name."""
    t = (title or "").strip()
    if len(t) > 120:
        return False
    if re.match(r"^Amazon(\.[a-z.]+)?(\s*:|\s*$)", t, re.I):
        return True
    tl = t.lower()
    if tl in ("amazon", "amazon.ae", "amazon.com", "amazon.co.uk", "amazon.de"):
        return True
    return False


def _hint_from_amazon_canonical_url(url: str) -> str:
    """
    Derive a minimal product label from the final Amazon URL after redirects.

    - ``.../echo-show-11/dp/B0...`` → humanized slug (Echo Show 11).
    - ``.../dp/B09WX3SX3P`` (no slug — common for short links) → ASIN-only hint.
    """
    u = url or ""

    def _asin_hint(asin: str) -> str:
        return (
            f"Amazon listing ASIN {asin.upper()}. "
            "Describe this exact product from the page or link; do not invent skincare, steamers, "
            "or unrelated categories."
        )[:800]

    # ASIN in path — try several PDP shapes (region sites, mobile /gp/aw/d/, etc.)
    for pat in (
        r"amazon\.[a-z.]+/dp/([A-Z0-9]{10})\b",
        r"amazon\.[a-z.]+/gp/product/([A-Z0-9]{10})\b",
        r"amazon\.[a-z.]+/gp/aw/d/([A-Z0-9]{10})\b",
        r"amazon\.[a-z.]+/d/([A-Z0-9]{10})\b",
    ):
        m_dp = re.search(pat, u, re.I)
        if m_dp:
            return _asin_hint(m_dp.group(1))

    m = re.search(r"amazon\.[a-z.]+/([^/]+)/dp/", u, re.I)
    if not m:
        return ""
    slug = (m.group(1) or "").strip()
    if not slug or slug.lower() in ("gp", "dp", "exec", "b", "s", "hz"):
        return ""
    parts = [p for p in re.split(r"[-_]+", slug) if p]
    words: List[str] = []
    for p in parts:
        if p.isdigit():
            words.append(p)
        else:
            words.append(p[:1].upper() + p[1:].lower() if len(p) > 1 else p.upper())
    return " ".join(words)[:200]


def _extract_product_title_from_html(raw: str) -> str:
    """Best-effort og:title / <title> for preview grounding (Amazon HTML varies by region)."""
    og = re.search(
        r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)',
        raw,
        re.I | re.DOTALL,
    )
    if og:
        return html_stdlib.unescape(og.group(1).strip())[:800]
    tm = re.search(r"<title[^>]*>([^<]+)</title>", raw, re.I | re.DOTALL)
    if tm:
        t = re.sub(r"\s+", " ", tm.group(1)).strip()
        return html_stdlib.unescape(t)[:800]
    return ""


def _urllib_final_url_after_redirects(url: str) -> str:
    """stdlib redirect follower — sometimes reaches /dp/ASIN when httpx gets stuck on bot pages."""
    import urllib.error
    import urllib.request

    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
            },
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=12) as resp:
            return resp.geturl()
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
        return ""


async def _resolve_product_title_hint(product_url: str) -> str:
    """Follow redirects and read page title so short links (amzn.to) map to a real product name."""
    u = (product_url or "").strip()
    if not u.startswith(("http://", "https://")):
        return ""
    # Full Amazon PDP in the field — no network (works when Amazon blocks server GET).
    direct = _hint_from_amazon_canonical_url(u)
    if direct:
        return direct
    # Short links: try stdlib redirect chain → ASIN from final URL (lighter than parsing HTML).
    try:
        final_std = await asyncio.to_thread(_urllib_final_url_after_redirects, u)
    except Exception:
        final_std = ""
    if final_std:
        hint_std = _hint_from_amazon_canonical_url(final_std)
        if hint_std:
            return hint_std
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(18.0),
            follow_redirects=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            },
        ) as client:
            r = await client.get(u)
        final_url = str(r.url)
        # Amazon often returns 403/503 to datacenter IPs AFTER redirects; final URL may still be /dp/ASIN.
        url_hint = _hint_from_amazon_canonical_url(final_url)
        if r.status_code >= 400:
            if url_hint:
                return url_hint
            logger.debug(
                "product hint: HTTP %s and no ASIN in URL for %s",
                r.status_code,
                final_url[:160],
            )
            return ""
        # Canonical ``/dp/ASIN`` (or slug+/dp/) from the final redirect beats og:title — Amazon often
        # serves bot-inconsistent or carousel-skewed titles that mis-name the PDP (e.g. Echo Pop vs Show).
        if url_hint:
            return url_hint
        html_hint = _extract_product_title_from_html(r.text)
        if html_hint and _looks_like_useless_amazon_shell_title(html_hint):
            html_hint = ""
        if html_hint:
            return html_hint
        return ""
    except Exception as e:
        logger.debug("product title fetch failed for %s: %s", u[:96], e)
        # Second chance: light follow-only request (may still land on /dp/ASIN URL).
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(12.0),
                follow_redirects=True,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                    ),
                    "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9",
                },
            ) as client2:
                r2 = await client2.get(u)
            h = _hint_from_amazon_canonical_url(str(r2.url))
            if h:
                return h
        except Exception as e2:
            logger.debug("product hint retry failed: %s", e2)
        return ""


@app.post("/api/v1/preview/script", dependencies=[Depends(require_mc_operator)])
async def preview_ugc_script(body: PreviewScriptBody) -> Dict[str, Any]:
    """Short Groq preview for the Create tab (optional — full script still generated in pipeline)."""
    key = (os.getenv("GROQ_API_KEY") or "").strip()
    if not key:
        raise HTTPException(status_code=503, detail="GROQ_API_KEY not configured on Mission Control")
    aid = _avatar_id_safe(body.avatar_id or "theanikaglow") or "theanikaglow"
    persona: Dict[str, Any] = {}
    try:
        d = _avatar_root() / aid
        if d.is_dir():
            persona = _load_json(d / "persona.json")
    except Exception:
        persona = {}
    niche = str(persona.get("niche") or persona.get("description") or "")
    name = str(persona.get("name") or persona.get("display_name") or aid)
    url = body.product_url.strip()
    manual = (body.product_title or "").strip()
    scraped = "" if manual else await _resolve_product_title_hint(url)
    product_hint = manual or scraped
    prompt = (
        f"You are writing a 45–60 second vertical UGC voiceover script for TikTok. "
        f"Avatar: {name}. Speaking style / niche context: {niche}. "
        f"Affiliate link (may be shortened): {url}. "
    )
    if product_hint:
        prompt += (
            f"PRODUCT TO PROMOTE — use ONLY this item; do not substitute a different product: {product_hint}. "
        )
    else:
        prompt += (
            "You do not have a reliable product name from the page. "
            "Write a short generic hook that sends viewers to check the link for this Amazon find. "
            "Do NOT invent a specific product (no random skincare tools, steamers, or unrelated gadgets). "
        )
    prompt += "Output ONLY the spoken script text, no stage directions, under 900 characters."
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={
                    "model": os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"),
                    "messages": [
                        {"role": "system", "content": "You write punchy short-form UGC scripts."},
                        {"role": "user", "content": prompt},
                    ],
                    "max_tokens": 800,
                    "temperature": 0.7,
                },
            )
        if r.status_code >= 400:
            raise HTTPException(status_code=502, detail=(r.text or r.reason_phrase)[:500])
        out = r.json()
        text = (
            (out.get("choices") or [{}])[0]
            .get("message", {})
            .get("content", "")
        )
        return {
            "script": (text or "").strip(),
            "avatar_id": aid,
            "product_hint": product_hint or None,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/api/v1/preview/tts", dependencies=[Depends(require_mc_operator)])
async def preview_tts(body: PreviewTtsBody) -> Dict[str, Any]:
    """Short ElevenLabs audio preview (base64 mp3) for voice test in Avatar Studio."""
    el_key = (os.getenv("ELEVENLABS_API_KEY") or os.getenv("XI_API_KEY") or "").strip()
    if not el_key:
        raise HTTPException(status_code=503, detail="ELEVENLABS_API_KEY not configured")
    voice = (body.voice_id or "").strip()
    if not voice and (body.avatar_id or "").strip():
        try:
            d = _avatar_dir_or_404(_avatar_id_safe(body.avatar_id or ""))
            soc = _load_json(d / "social_config.json")
            voice = str(soc.get("elevenlabs_voice_id") or "").strip()
        except HTTPException:
            voice = ""
    if not voice:
        voice = (os.getenv("ELEVENLABS_VOICE_ID") or os.getenv("ELEVENLABS_VOICE_ID_ANIKA") or "").strip()
    if not voice:
        raise HTTPException(status_code=400, detail="voice_id required (or set in avatar social_config / env)")
    snippet = body.text.strip()[:400]
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice}"
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(
                url,
                headers={"xi-api-key": el_key, "Content-Type": "application/json", "Accept": "audio/mpeg"},
                json={
                    "text": snippet,
                    "model_id": os.getenv("ELEVENLABS_MODEL_ID", "eleven_multilingual_v2"),
                },
            )
        if r.status_code >= 400:
            raise HTTPException(status_code=502, detail=(r.text or "")[:500])
        b64 = base64.b64encode(r.content).decode("ascii")
        return {"format": "mp3", "audio_base64": b64, "voice_id": voice}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/api/v1/avatars", response_model=AvatarDetail, dependencies=[Depends(require_mc_operator)])
async def create_avatar(body: AvatarCreate) -> AvatarDetail:
    aid = _avatar_id_safe(body.avatar_id)
    if not aid:
        raise HTTPException(status_code=400, detail="avatar_id must contain letters/numbers/-/_")
    root = _avatar_root()
    d = root / aid
    if d.exists():
        raise HTTPException(status_code=409, detail="Avatar already exists")
    try:
        d.mkdir(parents=True, exist_ok=False)
        persona = {
            "id": aid,
            "display_name": (body.display_name or aid).strip(),
            "name": (body.display_name or aid).strip(),
            "niche": (body.niche or "").strip(),
            "cta_line": (body.cta_line or "").strip(),
            "script_system_override": (body.script_system_override or "").strip(),
            "content_tone": (body.content_tone or "").strip(),
            "content_style": (body.content_style or "").strip(),
            "language": (body.language or "").strip(),
            "created_by": "mission_control",
            "created_at": datetime.utcnow().isoformat() + "Z",
        }
        social: Dict[str, Any] = {}
        if (body.voice_id or "").strip():
            social["elevenlabs_voice_id"] = body.voice_id.strip()
        if (body.getlate_api_key or "").strip():
            social["getlate_api_key"] = body.getlate_api_key.strip()
        if (body.zerio_api_key or "").strip():
            social["zerio_api_key"] = body.zerio_api_key.strip()
        if isinstance(body.platform_profiles, dict):
            social["platform_profiles"] = {
                str(k): str(v).strip() for k, v in body.platform_profiles.items() if str(v).strip()
            }
        if (body.affiliate_amazon_tag or "").strip():
            social["affiliate_amazon_tag"] = body.affiliate_amazon_tag.strip()
        if (body.affiliate_tiktok_shop_id or "").strip():
            social["affiliate_tiktok_shop_id"] = body.affiliate_tiktok_shop_id.strip()
        (d / "persona.json").write_text(json.dumps(persona, indent=2), encoding="utf-8")
        if social:
            (d / "social_config.json").write_text(json.dumps(social, indent=2), encoding="utf-8")
        else:
            (d / "social_config.example.json").write_text(
                json.dumps({"elevenlabs_voice_id": ""}, indent=2), encoding="utf-8"
            )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create avatar: {e}") from e
    return await get_avatar(aid)


@app.put("/api/v1/avatars/{avatar_id}/social-config", response_model=AvatarDetail, dependencies=[Depends(require_mc_operator)])
async def update_avatar_social_config(avatar_id: str, body: AvatarSocialUpdate) -> AvatarDetail:
    d = _avatar_dir_or_404(avatar_id)
    social_path = d / "social_config.json"
    social = _load_json(social_path)
    if (body.getlate_api_key or "").strip():
        social["getlate_api_key"] = body.getlate_api_key.strip()
    if (body.zerio_api_key or "").strip():
        social["zerio_api_key"] = body.zerio_api_key.strip()
    if (body.elevenlabs_voice_id or "").strip():
        social["elevenlabs_voice_id"] = body.elevenlabs_voice_id.strip()
    if (body.affiliate_amazon_tag or "").strip():
        social["affiliate_amazon_tag"] = body.affiliate_amazon_tag.strip()
    if (body.affiliate_tiktok_shop_id or "").strip():
        social["affiliate_tiktok_shop_id"] = body.affiliate_tiktok_shop_id.strip()
    if isinstance(body.platform_profiles, dict):
        social["platform_profiles"] = {
            str(k): str(v).strip() for k, v in body.platform_profiles.items() if str(v).strip()
        }
    social_path.write_text(json.dumps(social, indent=2), encoding="utf-8")
    return await get_avatar(_avatar_id_safe(avatar_id))


@app.put("/api/v1/avatars/{avatar_id}/persona", response_model=AvatarDetail, dependencies=[Depends(require_mc_operator)])
async def update_avatar_persona(avatar_id: str, body: AvatarPersonaUpdate) -> AvatarDetail:
    """Merge display fields into ``persona.json`` (Avatar Studio inline edit)."""
    d = _avatar_dir_or_404(avatar_id)
    path = d / "persona.json"
    persona = _load_json(path)
    if not persona:
        persona = {"id": _avatar_id_safe(avatar_id), "avatar_id": _avatar_id_safe(avatar_id)}
    if (body.display_name or "").strip():
        dn = body.display_name.strip()
        persona["display_name"] = dn
        persona["name"] = dn
    if body.niche is not None:
        persona["niche"] = str(body.niche).strip()
    if body.cta_line is not None:
        persona["cta_line"] = str(body.cta_line).strip()
    path.write_text(json.dumps(persona, indent=2), encoding="utf-8")
    return await get_avatar(_avatar_id_safe(avatar_id))


@app.post("/api/v1/avatars/{avatar_id}/image-upload", response_model=AvatarDetail, dependencies=[Depends(require_mc_operator)])
async def upload_avatar_image(avatar_id: str, image: UploadFile = File(...)) -> AvatarDetail:
    d = _avatar_dir_or_404(avatar_id)
    ctype = (image.content_type or "").lower()
    if not ctype.startswith("image/"):
        raise HTTPException(status_code=400, detail="Only image uploads are allowed")
    data = await image.read()
    if not data or len(data) < 100:
        raise HTTPException(status_code=400, detail="Image file is empty or invalid")
    if len(data) > 12 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Image too large (max 12MB)")
    portrait = d / "portrait.png"
    face = d / "face.png"
    portrait.write_bytes(data)
    # Keep face fallback aligned for current pipeline expectations.
    if not face.exists():
        face.write_bytes(data)
    _sync_avatar_assets_to_r2(avatar_id, d)
    return await get_avatar(_avatar_id_safe(avatar_id))


@app.post("/api/v1/avatars/{avatar_id}/image-generate", response_model=AvatarDetail, dependencies=[Depends(require_mc_operator)])
async def generate_avatar_image_from_prompt(avatar_id: str, payload: Dict[str, Any] = Body(...)) -> AvatarDetail:
    d = _avatar_dir_or_404(avatar_id)
    prompt = str(payload.get("prompt") or "").strip()
    provider = str(payload.get("provider") or "wavespeed").strip().lower()
    if not prompt:
        raise HTTPException(status_code=400, detail="prompt is required")
    if provider != "wavespeed":
        raise HTTPException(status_code=400, detail="Only provider='wavespeed' is currently supported")
    ws_key = (os.getenv("WAVESPEED_API_KEY") or "").strip()
    if not ws_key:
        raise HTTPException(status_code=400, detail="WAVESPEED_API_KEY is not configured")
    out_path = str((d / "portrait.png").resolve())
    try:
        flux_dev_download_to_path(prompt, out_path, api_key=ws_key)
    except WaveSpeedT2IError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    face = d / "face.png"
    if not face.exists():
        try:
            face.write_bytes((d / "portrait.png").read_bytes())
        except Exception:
            pass
    # Save last prompt for reproducibility.
    persona_path = d / "persona.json"
    persona = _load_json(persona_path)
    persona["avatar_image_prompt"] = prompt
    persona["avatar_image_provider"] = "wavespeed"
    persona_path.write_text(json.dumps(persona, indent=2), encoding="utf-8")
    _sync_avatar_assets_to_r2(avatar_id, d)
    return await get_avatar(_avatar_id_safe(avatar_id))


@app.get("/api/v1/avatar-studio/personas", dependencies=[Depends(require_mc_operator)])
async def avatar_studio_list_personas() -> Dict[str, Any]:
    """List saved personas (Redis ``avatar:persona:index``)."""
    rows = await asyncio.to_thread(_list_avatar_studio_personas)
    return {"personas": rows}


@app.post(
    "/api/v1/avatar-studio/generate",
    response_model=AvatarStudioGenerateResponse,
    dependencies=[Depends(require_mc_operator)],
)
async def avatar_studio_generate(body: AvatarStudioGenerateRequest) -> AvatarStudioGenerateResponse:
    """
    Create a new AI avatar persona: composite prompt → WaveSpeed portrait → R2 → Redis ``avatar:persona:{id}``.
    Requires Redis and R2 to be configured.
    """
    try:
        out = await asyncio.to_thread(_avatar_studio_generate_sync, body)
        return AvatarStudioGenerateResponse(**out)
    except WaveSpeedT2IError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e


@app.post(
    "/api/v1/avatar-studio/test-lipsync",
    response_model=AvatarStudioTestLipsyncResponse,
    dependencies=[Depends(require_mc_operator)],
)
async def avatar_studio_test_lipsync(body: AvatarStudioTestLipsyncRequest) -> AvatarStudioTestLipsyncResponse:
    """Short WaveSpeed lip-sync clip from a persona or a direct image URL (blocking worker thread)."""
    try:
        out = await asyncio.to_thread(_avatar_studio_test_lipsync_sync, body)
        return AvatarStudioTestLipsyncResponse(**out)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e


class AvatarStudioSavePersonaRequest(BaseModel):
    persona_id: str = Field(..., min_length=1, max_length=200)
    name: Optional[str] = Field(None, max_length=200)


@app.post("/api/v1/avatar-studio/save-persona", dependencies=[Depends(require_mc_operator)])
async def avatar_studio_save_persona(body: AvatarStudioSavePersonaRequest) -> Dict[str, Any]:
    """Confirm-save (or rename) an existing persona generated in this session."""
    pid = body.persona_id.strip()
    doc = _load_avatar_persona_redis(pid)
    if not doc:
        raise HTTPException(status_code=404, detail="Persona not found in Redis — generate first.")
    if body.name and body.name.strip():
        doc["name"] = body.name.strip()
        doc["display_name"] = body.name.strip()
    doc["saved"] = True
    try:
        _save_avatar_persona_redis(pid, doc)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    return {"ok": True, "persona_id": pid, "name": doc.get("name", "")}


@app.delete("/api/v1/avatars/{avatar_id}", dependencies=[Depends(require_mc_operator)])
async def delete_avatar(avatar_id: str) -> Dict[str, Any]:
    aid = _avatar_id_safe(avatar_id)
    if not aid:
        raise HTTPException(status_code=400, detail="Invalid avatar id")
    # Safety guard: don't delete if active jobs still target this avatar.
    active = [
        j.id for j in state.jobs.values()
        if str(j.payload.get("avatar_id", "")).strip().lower() == aid and j.status in (JobStatus.PENDING, JobStatus.RUNNING)
    ]
    if active:
        raise HTTPException(status_code=409, detail=f"Avatar has active jobs: {len(active)}")
    d = _avatar_root() / aid
    if not d.is_dir():
        raise HTTPException(status_code=404, detail="Avatar not found")
    try:
        for p in sorted(d.rglob("*"), reverse=True):
            if p.is_file():
                p.unlink(missing_ok=True)
            elif p.is_dir():
                p.rmdir()
        d.rmdir()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete avatar: {e}") from e
    return {"ok": True, "deleted": aid}


# ============================================================================
# Decisions API (Track B)
# ============================================================================


@app.get("/api/v1/decisions/config/global", response_model=DecisionConfigModel, dependencies=[Depends(require_mc_operator)])
async def get_decision_global_config() -> DecisionConfigModel:
    return state.decision_global_config


@app.put("/api/v1/decisions/config/global", response_model=DecisionConfigModel, dependencies=[Depends(require_mc_operator)])
async def update_decision_global_config(body: DecisionConfigModel) -> DecisionConfigModel:
    if body.manual_review_threshold > body.auto_approve_threshold:
        raise HTTPException(status_code=400, detail="manual_review_threshold must be <= auto_approve_threshold")
    state.decision_global_config = body
    return state.decision_global_config


@app.get("/api/v1/decisions/config/avatar/{avatar_id}", response_model=DecisionConfigModel, dependencies=[Depends(require_mc_operator)])
async def get_decision_avatar_config(avatar_id: str) -> DecisionConfigModel:
    return _decision_config_for_avatar(avatar_id)


@app.put("/api/v1/decisions/config/avatar/{avatar_id}", response_model=DecisionConfigModel, dependencies=[Depends(require_mc_operator)])
async def update_decision_avatar_config(avatar_id: str, body: DecisionConfigModel) -> DecisionConfigModel:
    if body.manual_review_threshold > body.auto_approve_threshold:
        raise HTTPException(status_code=400, detail="manual_review_threshold must be <= auto_approve_threshold")
    aid = _avatar_id_safe(avatar_id)
    if not aid:
        raise HTTPException(status_code=400, detail="Invalid avatar id")
    state.decision_avatar_config[aid] = body
    return state.decision_avatar_config[aid]


@app.post("/api/v1/decisions/evaluate", response_model=DecisionRecord, dependencies=[Depends(require_mc_operator)])
async def evaluate_decision(body: DecisionEvaluateRequest) -> DecisionRecord:
    cfg = _decision_config_for_avatar(body.avatar_id)
    engine_cfg = DecisionConfig(
        auto_approve_threshold=cfg.auto_approve_threshold,
        manual_review_threshold=cfg.manual_review_threshold,
        pregen_hitl_required=cfg.pregen_hitl_required,
    )
    candidate = Candidate(
        avatar_id=body.avatar_id.strip(),
        category=(body.category or "").strip(),
        affiliate_tracking_id=(body.affiliate_tracking_id or "").strip(),
        has_crosspost_risk=body.has_crosspost_risk,
        estimated_cost=body.estimated_cost,
        remaining_budget=cfg.remaining_budget,
        trend_score=body.trend_score,
        commission_rate=body.commission_rate,
    )
    evaluated = evaluate_candidate(candidate=candidate, config=engine_cfg, compliance_policy=_compliance_policy)
    route = DecisionRoute(str(evaluated.get("route")))

    status = DecisionStatus.PENDING
    if route == DecisionRoute.AUTO_APPROVE and not cfg.pregen_hitl_required:
        status = DecisionStatus.AUTO_APPROVED
    elif route == DecisionRoute.AUTO_REJECT:
        status = DecisionStatus.AUTO_REJECTED

    rec = DecisionRecord(
        id=str(uuid.uuid4()),
        status=status,
        route=route,
        avatar_id=body.avatar_id.strip(),
        product_url=body.product_url.strip(),
        title=(body.title or "").strip(),
        category=(body.category or "").strip(),
        affiliate_tracking_id=(body.affiliate_tracking_id or "").strip(),
        final_score=float(evaluated.get("final_score", 0.0)),
        component_scores={k: float(v) for k, v in (evaluated.get("component_scores") or {}).items()},
        hard_gates=evaluated.get("hard_gates") or {},
        explainability=evaluated.get("explainability") or {},
        config_snapshot=cfg.model_dump(),
        metadata=body.metadata or {},
    )
    state.decisions[rec.id] = rec
    return rec


@app.get("/api/v1/decisions/queue", response_model=List[DecisionRecord], dependencies=[Depends(require_mc_operator)])
async def list_decision_queue(
    status: Optional[DecisionStatus] = None,
    limit: int = Query(100, ge=1, le=500),
) -> List[DecisionRecord]:
    rows = list(state.decisions.values())
    if status:
        rows = [d for d in rows if d.status == status]
    else:
        rows = [d for d in rows if d.status == DecisionStatus.PENDING]
    rows.sort(key=lambda x: x.created_at, reverse=True)
    return rows[:limit]


@app.post("/api/v1/decisions/{decision_id}/approve", response_model=DecisionRecord, dependencies=[Depends(require_mc_operator)])
async def approve_decision(decision_id: str, body: DecisionActionRequest = Body(default=DecisionActionRequest())) -> DecisionRecord:
    rec = state.decisions.get(decision_id)
    if not rec:
        raise HTTPException(status_code=404, detail="Decision not found")
    if rec.status != DecisionStatus.PENDING:
        raise HTTPException(status_code=400, detail=f"Decision is already {rec.status.value}")
    rec.status = DecisionStatus.APPROVED
    rec.updated_at = datetime.utcnow()
    if (body.note or "").strip():
        rec.explainability["operator_note"] = body.note.strip()

    pu = (rec.product_url or "").strip()
    aid = _avatar_id_safe(rec.avatar_id) or (rec.avatar_id or "").strip().lower()
    if pu and aid:
        q_job_id = str(uuid.uuid4())
        worker_payload: Dict[str, Any] = {
            "job_id": q_job_id,
            "product_url": pu,
            "avatar_id": aid,
            "retry_count": 0,
        }
        if (rec.title or "").strip():
            worker_payload["product_title"] = (rec.title or "").strip()
        if (rec.category or "").strip():
            worker_payload["category"] = (rec.category or "").strip()
        meta = rec.metadata if isinstance(rec.metadata, dict) else {}
        for key in (
            "product_page_url",
            "template_id",
            "template",
            "cta_line",
            "product_image_urls",
            "product_bullets",
            "script_system_override",
            "elevenlabs_voice_id",
        ):
            if meta.get(key) is not None:
                worker_payload[key] = meta[key]
        try:
            r = await get_redis()
            await r.lpush(QUEUE_JOBS_PENDING, json.dumps(worker_payload))
            q_job = Job(
                id=q_job_id,
                module=ModuleName.AVATAR,
                job_type="decision_approved",
                payload=worker_payload,
                priority=0,
                status=JobStatus.PENDING,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
            state.jobs[q_job_id] = q_job
            await _persist_job_model(q_job)
            await manager.broadcast({"type": "job.created", "data": q_job.model_dump()})
        except Exception as e:
            logger.warning("decision_approve_enqueue_failed: %s", e)
    return rec


@app.post("/api/v1/decisions/{decision_id}/reject", response_model=DecisionRecord, dependencies=[Depends(require_mc_operator)])
async def reject_decision(decision_id: str, body: DecisionActionRequest = Body(default=DecisionActionRequest())) -> DecisionRecord:
    rec = state.decisions.get(decision_id)
    if not rec:
        raise HTTPException(status_code=404, detail="Decision not found")
    if rec.status != DecisionStatus.PENDING:
        raise HTTPException(status_code=400, detail=f"Decision is already {rec.status.value}")
    rec.status = DecisionStatus.REJECTED
    rec.updated_at = datetime.utcnow()
    if (body.note or "").strip():
        rec.explainability["operator_note"] = body.note.strip()
    return rec


# ============================================================================
# Credits / provider usage (Track C)
# ============================================================================


@app.get(
    "/api/v1/credits/providers/summary",
    dependencies=[Depends(require_mc_operator)],
)
async def credits_providers_summary(
    since_hours: float = Query(168, ge=1, le=8760),
) -> Dict[str, Any]:
    """Aggregate spend by provider over a lookback window (local JSONL, no billing APIs)."""
    try:
        return provider_summary(since_hours=float(since_hours))
    except Exception as e:
        logger.warning("credits_providers_summary failed: %s", e)
        return {"since_hours": float(since_hours), "total_usd": 0.0, "providers": {}, "error": str(e)}


@app.get(
    "/api/v1/credits/by-avatar",
    dependencies=[Depends(require_mc_operator)],
)
async def credits_by_avatar(
    since_hours: float = Query(168, ge=1, le=8760),
) -> Dict[str, Any]:
    try:
        return per_avatar_spend(since_hours=float(since_hours))
    except Exception as e:
        logger.warning("credits_by_avatar failed: %s", e)
        return {"since_hours": float(since_hours), "avatars": {}, "error": str(e)}


@app.get(
    "/api/v1/credits/jobs/{job_id}/trace",
    dependencies=[Depends(require_mc_operator)],
)
async def credits_job_trace(job_id: str) -> Dict[str, Any]:
    try:
        jid = (job_id or "").strip()
        if not jid:
            raise HTTPException(status_code=400, detail="job_id required")
        return job_cost_trace(jid)
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("credits_job_trace failed: %s", e)
        return {"job_id": job_id, "events": [], "total_usd": 0.0, "error": str(e)}


@app.get(
    "/api/v1/credits/cap-status",
    dependencies=[Depends(require_mc_operator)],
)
async def credits_cap_status() -> Dict[str, Any]:
    try:
        return cap_status()
    except Exception as e:
        logger.warning("credits_cap_status failed: %s", e)
        return {
            "last_24h_spend_usd": 0.0,
            "last_30d_spend_usd": 0.0,
            "burn_rate_usd_per_hour": 0.0,
            "daily_cap_usd": None,
            "monthly_cap_usd": None,
            "daily_remaining_usd": None,
            "daily_cap_breach": False,
            "monthly_cap_breach": False,
            "wavespeed_max_i2v_per_hour": None,
            "notes": [str(e)],
        }

# ============================================================================
# Jobs API
# ============================================================================

@app.get("/api/v1/jobs", response_model=List[Job], dependencies=[Depends(require_mc_operator)])
async def list_jobs(
    limit: int = Query(100, ge=1, le=500),
    status: Optional[JobStatus] = None,
    module: Optional[ModuleName] = None
):
    """List all jobs."""
    jobs = list(state.jobs.values())
    
    # Filter
    if status:
        jobs = [j for j in jobs if j.status == status]
    if module:
        jobs = [j for j in jobs if j.module == module]
    
    # Sort by created_at desc
    jobs.sort(key=lambda x: x.created_at, reverse=True)
    
    return jobs[:limit]

@app.post("/api/v1/jobs", response_model=Job, dependencies=[Depends(require_mc_operator)])
async def create_job(job: JobCreate):
    """Create a new job."""
    # Check kill switch
    if state.kill_switches.get("global") or state.kill_switches.get(job.module.value):
        raise HTTPException(
            status_code=503,
            detail=f"Kill switch is active for {job.module.value}"
        )
    
    job_id = str(uuid.uuid4())
    worker_payload: Dict[str, Any] = dict(job.payload or {})
    worker_payload.setdefault("retry_count", 0)
    if job.module == ModuleName.AVATAR:
        wid = str(worker_payload.get("job_id") or job_id).strip() or job_id
        worker_payload["job_id"] = wid
        pu = str(worker_payload.get("product_url") or "").strip()
        if not pu:
            raise HTTPException(status_code=400, detail="payload.product_url required for klip-avatar jobs")
        worker_payload["product_url"] = pu
        worker_payload.setdefault("avatar_id", "theanikaglow")
    elif job.module == ModuleName.TRADER:
        raise HTTPException(
            status_code=400,
            detail="klip-trader is a separate repository; deploy and run it outside this monorepo queue.",
        )
    elif job.module == ModuleName.SELECTOR:
        raise HTTPException(
            status_code=400,
            detail="Use POST /api/v1/actions/selector-run to enqueue scored products from CSV (selector does not consume generic pending jobs).",
        )
    elif job.module in (ModuleName.FUNNEL, ModuleName.AVENTURE):
        raise HTTPException(
            status_code=501,
            detail=f"Module {job.module.value} is not yet wired to the shared jobs_pending queue in this release.",
        )
    else:
        worker_payload.setdefault("job_id", job_id)

    redis_enqueued = False
    job_warning: Optional[str] = None

    new_job = Job(
        id=job_id,
        module=job.module,
        job_type=job.job_type,
        payload=worker_payload,
        priority=job.priority,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
        redis_enqueued=None,
        warning=None,
    )

    state.jobs[job_id] = new_job

    # Push to Redis list (workers use LPOP/BLPOP on ``klipaura:jobs:pending``)
    job_data = json.dumps(worker_payload)
    last_exc: Optional[BaseException] = None
    for attempt in range(2):
        try:
            r = await get_redis()
            await r.lpush(QUEUE_JOBS_PENDING, job_data)
            redis_enqueued = True
            break
        except Exception as e:
            last_exc = e
            logger.warning(
                "Redis LPUSH failed for job %s (attempt %s): %s",
                job_id,
                attempt + 1,
                e,
            )
            if attempt == 0:
                await reset_redis_connection()
                continue

    if redis_enqueued:
        try:
            await manager.broadcast({
                "type": "job.created",
                "data": new_job.model_dump(),
            })
        except Exception as e:
            logger.warning("WebSocket broadcast after job %s failed (job is queued): %s", job_id, e)

    if not redis_enqueued and last_exc is not None:
        job_warning = (
            "Job saved in Mission Control but was not pushed to the worker queue — Redis unavailable. "
            f"{_redis_error_hint(last_exc)} "
            "Start Redis (e.g. `docker compose up -d redis` from the repo root) and set REDIS_URL "
            "to match where this API runs (on the host use redis://127.0.0.1:6379/0, not the Docker service name). "
            "The klip-avatar worker will not see this job until the queue is reachable."
        )

    updated = new_job.model_copy(update={"redis_enqueued": redis_enqueued, "warning": job_warning})
    state.jobs[job_id] = updated

    await _persist_job_model(updated)
    return updated


@app.post("/api/v1/actions/scanner-run", dependencies=[Depends(require_mc_operator)])
async def action_scanner_run(body: ScannerRunBody) -> Dict[str, Any]:
    """Execute one scanner pass (CSV/feeds) and enqueue top opportunities for the avatar worker."""
    loop = asyncio.get_running_loop()
    try:
        result = await loop.run_in_executor(_PIPELINE_EXECUTOR, lambda: _sync_scanner_run(body))
        if not isinstance(result, dict):
            return {"ok": False, "detail": "unexpected scanner result"}
        return result
    except ImportError as e:
        logger.warning("scanner-run unavailable (import): %s", e)
        raise HTTPException(
            status_code=503,
            detail="klip-scanner package not available in this image (rebuild Mission Control Dockerfile with scanner context).",
        ) from e
    except Exception as e:
        logger.exception("scanner-run failed")
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/api/v1/actions/selector-run", dependencies=[Depends(require_mc_operator)])
async def action_selector_run(body: SelectorRunBody) -> Dict[str, Any]:
    """Run one selector cycle: load products CSV → score → push avatar-ready jobs to pending."""
    loop = asyncio.get_running_loop()
    try:
        code = await loop.run_in_executor(_PIPELINE_EXECUTOR, lambda: _sync_selector_run(body))
        return {"ok": code == 0, "exit_code": code}
    except ImportError as e:
        logger.warning("selector-run unavailable (import): %s", e)
        raise HTTPException(
            status_code=503,
            detail="klip-selector package not available in this image (rebuild Mission Control Dockerfile with selector context).",
        ) from e
    except Exception as e:
        logger.exception("selector-run failed")
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/api/v1/jobs/{job_id}", response_model=Job, dependencies=[Depends(require_mc_operator)])
async def get_job(job_id: str):
    """Get job details."""
    if job_id not in state.jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    return state.jobs[job_id]

@app.put("/api/v1/jobs/{job_id}", response_model=Job, dependencies=[Depends(require_mc_operator)])
async def update_job(job_id: str, update: JobUpdate):
    """Update job status/progress."""
    if job_id not in state.jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    
    job = state.jobs[job_id]
    
    if update.status:
        job.status = update.status
        if update.status == JobStatus.AWAITING_HITL:
            job.hitl_requested = True
    if update.progress is not None:
        job.progress = update.progress
    if update.result is not None:
        job.result = update.result
    if update.error is not None:
        job.error = update.error
    elif update.status in (JobStatus.AWAITING_HITL, JobStatus.COMPLETED, JobStatus.RUNNING):
        job.error = None

    job.updated_at = datetime.utcnow()
    
    # Broadcast update
    await manager.broadcast({
        "type": "job.updated",
        "data": job.model_dump()
    })
    
    await _persist_job_model(job)
    return job

@app.post("/api/v1/jobs/{job_id}/approve", response_model=Job, dependencies=[Depends(require_mc_operator)])
async def approve_job(job_id: str):
    """Approve HITL job."""
    if job_id not in state.jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    
    job = state.jobs[job_id]
    if job.status != JobStatus.AWAITING_HITL:
        raise HTTPException(status_code=400, detail="Job not awaiting approval")
    
    job.hitl_approved = True
    job.status = JobStatus.RUNNING
    job.updated_at = datetime.utcnow()
    
    try:
        r = await get_redis()
        items = await r.lrange(QUEUE_JOBS_HITL, 0, -1)
        for item in items or []:
            try:
                data = json.loads(item)
                if str(data.get("job_id")) == str(job_id):
                    await r.lrem(QUEUE_JOBS_HITL, 1, item)
                    break
            except Exception:
                continue
        await r.lpush(QUEUE_JOBS_PENDING, json.dumps(job.payload))
    except Exception:
        pass
    
    await manager.broadcast({
        "type": "job.approved",
        "data": job.model_dump()
    })
    
    await _persist_job_model(job)
    return job

@app.post("/api/v1/jobs/{job_id}/reject", response_model=Job, dependencies=[Depends(require_mc_operator)])
async def reject_job(job_id: str, reason: Optional[str] = Query(default=None)):
    """Reject HITL job."""
    if job_id not in state.jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    
    job = state.jobs[job_id]
    if job.status != JobStatus.AWAITING_HITL:
        raise HTTPException(status_code=400, detail="Job not awaiting approval")
    
    job.hitl_approved = False
    job.status = JobStatus.CANCELLED
    job.error = reason or "Rejected by operator"
    job.updated_at = datetime.utcnow()
    
    try:
        r = await get_redis()
        items = await r.lrange(QUEUE_JOBS_HITL, 0, -1)
        for item in items or []:
            try:
                data = json.loads(item)
                if str(data.get("job_id")) == str(job_id):
                    await r.lrem(QUEUE_JOBS_HITL, 1, item)
                    break
            except Exception:
                continue
    except Exception:
        pass
    
    await manager.broadcast({
        "type": "job.rejected",
        "data": job.model_dump()
    })
    
    await _persist_job_model(job)
    return job

# ============================================================================
# Modules API
# ============================================================================

@app.get("/api/v1/modules", response_model=List[ModuleStatus], dependencies=[Depends(require_mc_operator)])
async def list_modules():
    """List all module statuses."""
    return list(state.modules.values())

@app.get("/api/v1/modules/{name}", response_model=ModuleStatus, dependencies=[Depends(require_mc_operator)])
async def get_module(name: ModuleName):
    """Get module details."""
    if name.value not in state.modules:
        # Return default status
        return ModuleStatus(name=name)
    return state.modules[name.value]

@app.put("/api/v1/modules/{name}/toggle", response_model=ModuleStatus, dependencies=[Depends(require_mc_operator)])
async def toggle_module(name: ModuleName, enabled: bool):
    """Enable or disable a module."""
    if name.value not in state.modules:
        state.modules[name.value] = ModuleStatus(name=name)
    
    module = state.modules[name.value]
    module.enabled = enabled
    
    # Update kill switch accordingly
    if not enabled:
        state.kill_switches[name.value] = KillSwitch(
            scope=KillSwitchScope(name.value),
            active=True,
            triggered_by="operator",
            reason="Manual disable",
            triggered_at=datetime.utcnow()
        )
    else:
        state.kill_switches.pop(name.value, None)
    
    await manager.broadcast({
        "type": "module.toggled",
        "data": module.model_dump()
    })
    
    return module

# ============================================================================
# Kill Switch API
# ============================================================================

@app.get("/api/v1/kill-switch", response_model=List[KillSwitch], dependencies=[Depends(require_mc_operator)])
async def get_kill_switches():
    """Get all active kill switches."""
    return [ks for ks in state.kill_switches.values() if ks.active]

@app.post("/api/v1/kill-switch", response_model=KillSwitch, dependencies=[Depends(require_mc_operator)])
async def trigger_kill_switch(kill: KillSwitchCreate, triggered_by: str = "operator"):
    """Trigger a kill switch."""
    scope = kill.scope
    active_key = scope.value
    
    kill_switch = KillSwitch(
        scope=scope,
        active=True,
        triggered_by=triggered_by,
        reason=kill.reason,
        triggered_at=datetime.utcnow()
    )
    
    state.kill_switches[active_key] = kill_switch
    
    # Set in Redis for workers to check
    try:
        r = await get_redis()
        await r.set(KEY_KILL_GLOBAL if scope == KillSwitchScope.GLOBAL else f"klipaura:kill:{scope.value}", "1", ex=86400)
    except Exception:
        pass
    
    await manager.broadcast({
        "type": "kill_switch.triggered",
        "data": kill_switch.model_dump()
    })
    
    return kill_switch

@app.get("/api/v1/queues/overview", dependencies=[Depends(require_mc_operator)])
async def queues_overview() -> Dict[str, Any]:
    """Depth of main Redis lists + global pause flag (workers read ``klipaura:queue:paused``)."""
    try:
        r = await get_redis()
        pending = int(await r.llen(QUEUE_JOBS_PENDING) or 0)
        hitl = int(await r.llen(QUEUE_JOBS_HITL) or 0)
        dlq = int(await r.llen(QUEUE_JOBS_DLQ) or 0)
        paused_raw = await r.get(GLOBAL_QUEUE_PAUSED_KEY)
        paused = bool(str(paused_raw or "").strip())
        return {
            "jobs_pending": pending,
            "hitl_pending": hitl,
            "dlq": dlq,
            "global_paused": paused,
        }
    except Exception as e:
        logger.warning("queues_overview: Redis unavailable: %s", e)
        return {
            "jobs_pending": 0,
            "hitl_pending": 0,
            "dlq": 0,
            "global_paused": False,
            "degraded": True,
            "note": str(e),
        }


@app.get("/api/v1/workers/avatar", response_model=WorkerHeartbeatStatus, dependencies=[Depends(require_mc_operator)])
async def avatar_worker_status() -> WorkerHeartbeatStatus:
    """Avatar worker heartbeat + queue context for pipeline readiness diagnostics."""
    try:
        r = await get_redis()
        hb_raw = await r.get(QUEUE_NAMES.worker_avatar)
        q_depth = int(await r.llen(QUEUE_JOBS_PENDING) or 0)
    except Exception as e:
        return WorkerHeartbeatStatus(
            worker="klip-avatar",
            online=False,
            queue_depth=0,
            note=f"Redis unavailable: {e}",
        )

    if not hb_raw:
        note = "No heartbeat key found. Worker may be down or not deployed."
        if q_depth > 0:
            note = (
                "No heartbeat key found while queue has pending jobs. "
                "Deploy/start klip-avatar worker and verify REDIS_URL + MC_SERVICE_TOKEN."
            )
        return WorkerHeartbeatStatus(
            worker="klip-avatar",
            online=False,
            queue_depth=q_depth,
            note=note,
        )
    try:
        if isinstance(hb_raw, bytes):
            hb_raw = hb_raw.decode("utf-8", errors="replace")
        hb = json.loads(hb_raw)
        ts_raw = str(hb.get("ts") or "").strip()
        seen_at: Optional[datetime] = None
        if ts_raw:
            seen_at = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
            if seen_at.tzinfo:
                seen_at = seen_at.replace(tzinfo=None)
        age = int((datetime.utcnow() - seen_at).total_seconds()) if seen_at else None
        online = age is not None and age <= 180
        note = None
        if age is None:
            note = "Heartbeat timestamp missing."
        elif age > 180:
            note = "Heartbeat stale (>180s). Worker likely not running."
        elif q_depth > 0 and str(hb.get("state") or "").upper() == "IDLE":
            note = "Worker is online but idle while queue has pending jobs."
        return WorkerHeartbeatStatus(
            worker="klip-avatar",
            online=online,
            state=str(hb.get("state") or "") or None,
            job_id=str(hb.get("job_id") or "") or None,
            last_seen=(seen_at.isoformat() + "Z") if seen_at else None,
            seconds_since_last_seen=age,
            queue_depth=q_depth,
            note=note,
        )
    except Exception as e:
        return WorkerHeartbeatStatus(
            worker="klip-avatar",
            online=False,
            queue_depth=q_depth,
            note=f"Invalid heartbeat payload: {e}",
        )


@app.get("/api/v1/health/pipeline", dependencies=[Depends(require_mc_operator)])
async def pipeline_health_diagnostics() -> Dict[str, Any]:
    """Env + Redis + worker + avatar portrait readiness for Avatar Studio diagnostics card."""
    env_flags = {
        "wavespeed_api_key": bool((os.getenv("WAVESPEED_API_KEY") or "").strip()),
        "elevenlabs_api_key": bool((os.getenv("ELEVENLABS_API_KEY") or os.getenv("XI_API_KEY") or "").strip()),
        "groq_api_key": bool((os.getenv("GROQ_API_KEY") or "").strip()),
        "elevenlabs_voice_id": bool(
            (os.getenv("ELEVENLABS_VOICE_ID") or os.getenv("ELEVENLABS_VOICE_ID_ANIKA") or "").strip()
        ),
    }
    redis_ok = False
    try:
        r = await get_redis()
        await asyncio.wait_for(r.ping(), timeout=3.0)
        redis_ok = True
    except Exception:
        redis_ok = False

    avatars_with_portrait = 0
    avatar_total = 0
    try:
        root = _avatar_root()
        for p in root.iterdir():
            if not p.is_dir():
                continue
            avatar_total += 1
            if _avatar_summary_from_dir(p).has_portrait:
                avatars_with_portrait += 1
    except Exception:
        pass

    worker = await avatar_worker_status()

    return {
        "redis_connected": redis_ok,
        "redis_url_redacted": _redact_redis_url(_mc_redis_url_resolved()),
        "env": env_flags,
        "avatars_total": avatar_total,
        "avatars_with_portrait": avatars_with_portrait,
        "worker": worker.model_dump(),
    }


@app.post("/api/v1/queue/pause", dependencies=[Depends(require_mc_operator)])
async def queue_pause_global() -> Dict[str, Any]:
    try:
        r = await get_redis()
        await r.set(GLOBAL_QUEUE_PAUSED_KEY, "1")
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Redis unavailable: {e}") from e
    return {"ok": True, "paused": True}


@app.delete("/api/v1/queue/pause", dependencies=[Depends(require_mc_operator)])
async def queue_resume_global() -> Dict[str, Any]:
    try:
        r = await get_redis()
        await r.delete(GLOBAL_QUEUE_PAUSED_KEY)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Redis unavailable: {e}") from e
    return {"ok": True, "paused": False}


@app.delete("/api/v1/kill-switch", dependencies=[Depends(require_mc_operator)])
async def clear_kill_switch(scope: KillSwitchScope = KillSwitchScope.GLOBAL):
    """Clear a kill switch."""
    key = scope.value
    
    if key in state.kill_switches:
        state.kill_switches[key].active = False
    
    try:
        r = await get_redis()
        await r.delete(KEY_KILL_GLOBAL if scope == KillSwitchScope.GLOBAL else f"klipaura:kill:{scope.value}")
    except Exception:
        pass
    
    await manager.broadcast({
        "type": "kill_switch.cleared",
        "data": {"scope": scope.value}
    })
    
    return {"message": f"Kill switch cleared for {scope.value}"}

# ============================================================================
# Metrics API
# ============================================================================

@app.get("/api/v1/metrics", response_model=SystemMetrics, dependencies=[Depends(require_mc_operator)])
async def get_metrics():
    """Get system metrics."""
    jobs = list(state.jobs.values())
    
    # Count by status
    jobs_by_status = {}
    for job in jobs:
        status = job.status.value
        jobs_by_status[status] = jobs_by_status.get(status, 0) + 1
    
    # Count by module
    jobs_by_module = {}
    for job in jobs:
        module = job.module.value
        jobs_by_module[module] = jobs_by_module.get(module, 0) + 1
    
    # Events in last hour (ring log)
    events_count = 0
    try:
        r = await get_redis()
        hour_ago = datetime.utcnow() - timedelta(hours=1)
        raw_items = await r.lrange(KEY_EVENTS_LOG, 0, 199)
        for raw in raw_items or []:
            try:
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8", errors="replace")
                ev = json.loads(raw)
                if not isinstance(ev, dict):
                    continue
                ts_raw = ev.get("timestamp")
                if not ts_raw:
                    continue
                if isinstance(ts_raw, (int, float)):
                    ts_dt = datetime.utcfromtimestamp(float(ts_raw))
                else:
                    ts_dt = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
                    if ts_dt.tzinfo:
                        ts_dt = ts_dt.replace(tzinfo=None)
                if ts_dt >= hour_ago:
                    events_count += 1
            except Exception:
                continue
    except Exception:
        pass
    
    # Active kill switches
    active_kills = [ks.scope.value for ks in state.kill_switches.values() if ks.active]
    
    # Redis connected
    redis_connected = False
    try:
        r = await get_redis()
        await r.ping()
        redis_connected = True
    except Exception:
        pass
    
    return SystemMetrics(
        total_jobs=len(jobs),
        jobs_by_status=jobs_by_status,
        jobs_by_module=jobs_by_module,
        events_last_hour=events_count,
        active_kill_switches=active_kills,
        redis_connected=redis_connected,
        uptime_seconds=int((datetime.utcnow() - state.started_at).total_seconds())
    )

# ============================================================================
# WebSocket Endpoint (DEPRECATED — no frontend client uses this; SSE is the live channel)
# Kept for potential external tooling. Safe to remove if confirmed unused.
# ============================================================================

@app.websocket("/ws/events")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket for real-time updates (deprecated — frontend uses SSE)."""
    await manager.connect(websocket)
    try:
        while True:
            # Receive and echo back (keeps connection alive)
            data = await websocket.receive_text()
            await websocket.send_json({"type": "pong", "data": data})
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception:
        manager.disconnect(websocket)

# ============================================================================
# Startup / Shutdown
# ============================================================================

@app.on_event("startup")
async def startup():
    """Initialize on startup."""
    # Initialize modules with default status
    for module in ModuleName:
        if module.value not in state.modules:
            state.modules[module.value] = ModuleStatus(name=module)

    # Connect to Redis (never block lifespan indefinitely — Railway healthcheck needs 8000 up)
    try:
        client = await get_redis()
        await asyncio.wait_for(
            client.ping(),
            timeout=float(os.getenv("REDIS_STARTUP_PING_TIMEOUT", "5")),
        )
        print("Connected to Redis")
    except Exception as e:
        print(f"Warning: Could not connect to Redis: {e}")
        try:
            if state.redis is not None:
                await state.redis.aclose()
        except Exception:
            pass
        state.redis = None

    try:
        async def _load_jobs() -> None:
            if await init_job_store():
                rows = await load_all_job_rows()
                for row in rows:
                    try:
                        state.jobs[row.id] = _job_row_to_job(row)
                    except Exception:
                        pass
                print(f"Loaded {len(rows)} job(s) from database")

        await asyncio.wait_for(
            _load_jobs(),
            timeout=float(os.getenv("DATABASE_STARTUP_TIMEOUT", "25")),
        )
    except asyncio.TimeoutError:
        print("Warning: job store init timed out — continuing without DB")
    except Exception as e:
        print(f"Warning: job store unavailable: {e}")

    asyncio.create_task(start_scheduler())

@app.on_event("shutdown")
async def shutdown():
    """Cleanup on shutdown."""
    if state.redis:
        await state.redis.close()
    try:
        await close_job_store()
    except Exception:
        pass

# ============================================================================
# HITL fallback routes — served by main.py when klip-dispatch is not running.
# next.config.js rewrites /api/dashboard/*, /api/jobs, /api/avatars to the HITL
# service (default :8080).  These handlers mirror the subset the MC dashboard
# actually uses so local dev works without docker compose.
# ============================================================================

def _hitl_list_recent_jobs(limit: int) -> list[dict[str, Any]]:
    """Read job manifests from JOBS_DIR (same format as klip-dispatch)."""
    jobs_dir = Path(os.getenv("JOBS_DIR", str(Path(__file__).resolve().parents[1] / "jobs")))
    if not jobs_dir.is_dir():
        return []
    rows: list[tuple[float, dict[str, Any]]] = []
    for d in jobs_dir.iterdir():
        if not d.is_dir():
            continue
        mf = d / "manifest.json"
        if not mf.is_file():
            continue
        try:
            mtime = mf.stat().st_mtime
            data = json.loads(mf.read_text(encoding="utf-8"))
            rows.append((mtime, {
                "job_id": data.get("job_id") or d.name,
                "status": data.get("status"),
                "avatar_id": (data.get("payload") or {}).get("avatar_id"),
                "product_url": (data.get("payload") or {}).get("product_url"),
                "video_url": data.get("r2_url"),
                "funnel_url": None,
                "updated_at": data.get("updated_at"),
                "error": (data.get("error") or "")[:200] or None,
            }))
        except Exception:
            continue
    rows.sort(key=lambda x: x[0], reverse=True)
    return [r[1] for r in rows[:max(1, limit)]]


@app.get("/api/dashboard/recent-jobs")
async def hitl_fallback_recent_jobs(limit: int = Query(30, ge=1, le=200)) -> Dict[str, Any]:
    jobs = await asyncio.to_thread(_hitl_list_recent_jobs, limit)
    return {"jobs": jobs}


@app.get("/api/jobs")
async def hitl_fallback_list_jobs(limit: int = Query(100, ge=1, le=500)) -> Dict[str, Any]:
    jobs = await asyncio.to_thread(_hitl_list_recent_jobs, limit)
    return {"jobs": jobs}


@app.post("/api/jobs")
async def hitl_fallback_create_job(body: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """Forward to the v1 job creation logic."""
    product_url = str(body.get("product_url") or "").strip()
    avatar_id = str(body.get("avatar_id") or "theanikaglow").strip()
    if not product_url:
        raise HTTPException(status_code=400, detail="product_url is required")
    job = JobCreate(
        module=ModuleName.AVATAR,
        payload={
            "product_url": product_url,
            "avatar_id": avatar_id,
            **{k: v for k, v in body.items() if k not in ("product_url", "avatar_id", "module")},
        },
    )
    return await create_job(job)


@app.get("/api/avatars")
async def hitl_fallback_list_avatars() -> Dict[str, Any]:
    rows = await list_avatars()
    return {"avatars": rows}


# ============================================================================
# Entry Point
# ============================================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
