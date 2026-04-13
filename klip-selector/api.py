"""klip-selector FastAPI application.

Endpoints:
    POST /api/v1/products/submit     → ManualAdapter → score → queue
    GET  /api/v1/products/queue      → last 50 passport summaries
    POST /api/v1/products/{id}/approve → force-push regardless of score
    POST /api/v1/products/{id}/reject  → blacklist affiliate_url, 30-day TTL
    GET  /health                     → {ok, redis, queue_depth}

Auth: X-API-Key header matching SELECTOR_API_KEY env var.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Optional

_REPO = Path(__file__).resolve().parents[1]
_SCANNER = _REPO / "klip-scanner"
for _p in [str(_REPO), str(_SCANNER)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    from dotenv import load_dotenv, find_dotenv
    load_dotenv(find_dotenv(usecwd=False), override=False)
except ImportError:
    pass

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from infrastructure.product_passport import ProductPassport
from infrastructure.queue_names import BLACKLIST_PREFIX, JOBS_PENDING
from infrastructure.redis_client import get_redis_client_optional

from scoring.opportunity_engine import score_product, is_above_threshold
from routing.format_router import format_for_product
from publisher import push_passport

app = FastAPI(title="klip-selector", version="1.0.0")


def _api_key() -> str:
    return (os.getenv("SELECTOR_API_KEY") or "").strip()


def _require_auth(request: Request) -> None:
    key = _api_key()
    if not key:
        return  # No key configured → open access (local dev)
    got = (request.headers.get("X-API-Key") or request.query_params.get("api_key") or "").strip()
    if got != key:
        raise HTTPException(401, "Invalid or missing X-API-Key")


def _redis() -> Any:
    r = get_redis_client_optional()
    if r is None:
        raise HTTPException(503, "Redis not configured")
    return r


def _avatar_id_for_request() -> str:
    """Return default avatar from registry (empty string = let worker pick)."""
    try:
        from infrastructure.avatar_loader import AvatarLoader
        return AvatarLoader().pick_default() or ""
    except Exception:
        return ""


# ── Request models ──────────────────────────────────────────────────────────

class ProductSubmitBody(BaseModel):
    network: str = Field(..., pattern="^(amazon|temu|clickbank|manual)$")
    title: str = Field(..., min_length=3)
    images: List[str] = Field(default_factory=list)
    price: str = Field(default="")
    description: str = Field(default="")
    affiliate_url: str = Field(default="")
    source_url: str = Field(default="")
    commission_rate: float = Field(default=0.0, ge=0.0, le=100.0)
    category: str = Field(default="")
    video_format: Optional[str] = None
    avatar_id: Optional[str] = None
    skip_score_check: bool = False


class RejectBody(BaseModel):
    reason: str = Field(default="operator_reject")
    ttl_days: int = Field(default=30, ge=1, le=365)


# ── Endpoints ────────────────────────────────────────────────────────────────

@app.get("/health")
def health() -> JSONResponse:
    r = get_redis_client_optional()
    redis_ok = False
    queue_depth = 0
    if r is not None:
        try:
            redis_ok = r.ping()
            queue_depth = r.llen(JOBS_PENDING)
        except Exception:
            pass
    return JSONResponse({"ok": True, "redis": redis_ok, "queue_depth": queue_depth})


@app.post("/api/v1/products/submit")
def submit_product(body: ProductSubmitBody, request: Request) -> JSONResponse:
    """Submit a product for scoring and queuing (operator endpoint)."""
    _require_auth(request)
    r = _redis()

    # Determine avatar
    avatar = (body.avatar_id or "").strip() or _avatar_id_for_request()

    # Score
    score = score_product(
        commission_rate=body.commission_rate,
        trend_score=0.6,  # manual submissions get neutral trend
        category=body.category,
        price=body.price,
    )

    # Format routing
    video_format = body.video_format or format_for_product(body.category, body.network)

    if not body.skip_score_check and not is_above_threshold(score):
        return JSONResponse(
            status_code=422,
            content={
                "ok": False,
                "reason": "SCORE_BELOW_THRESHOLD",
                "score": round(score, 2),
                "threshold": int(os.getenv("SELECTOR_MIN_SCORE", "65")),
                "hint": "Pass skip_score_check=true to force-queue.",
            },
        )

    passport = ProductPassport.new(
        network=body.network,
        title=body.title,
        images=body.images,
        price=body.price,
        description=body.description,
        affiliate_url=body.affiliate_url,
        source_url=body.source_url or body.affiliate_url,
        commission_rate=body.commission_rate,
        score=score,
        avatar_id=avatar,
        video_format=video_format,
        category=body.category,
        status="queued",
    )

    ok, result = push_passport(passport, r, skip_score_check=True)
    if not ok:
        raise HTTPException(500, f"Push failed: {result}")

    return JSONResponse({
        "ok": True,
        "passport_id": passport.passport_id,
        "job_id": result,
        "score": round(score, 2),
        "video_format": video_format,
        "avatar_assigned": avatar,
        "status": "queued",
    })


@app.get("/api/v1/products/queue")
def list_queue(request: Request, limit: int = 50) -> JSONResponse:
    """Return last N passport summaries from the pending queue."""
    _require_auth(request)
    r = _redis()
    limit = max(1, min(200, limit))
    raw_items = r.lrange(JOBS_PENDING, -limit, -1)
    out = []
    for raw in raw_items:
        try:
            pointer = json.loads(raw)
        except Exception:
            continue
        passport_id = pointer.get("passport_id")
        if passport_id:
            pp = ProductPassport.load(r, passport_id)
            if pp:
                out.append({
                    "passport_id": pp.passport_id,
                    "job_id": pointer.get("job_id"),
                    "title": pp.title,
                    "network": pp.network,
                    "score": pp.score,
                    "video_format": pp.video_format,
                    "avatar_id": pp.avatar_id,
                    "status": pp.status,
                    "submitted_at": pointer.get("submitted_at"),
                })
            else:
                out.append({"passport_id": passport_id, "status": "passport_expired"})
        else:
            # Legacy inline job
            out.append({"legacy_job": pointer.get("job_id"), "status": "legacy"})
    return JSONResponse({"queue": out, "total": len(out)})


@app.post("/api/v1/products/{passport_id}/approve")
def approve_product(passport_id: str, request: Request) -> JSONResponse:
    """Force re-queue an existing passport regardless of score."""
    _require_auth(request)
    r = _redis()
    pp = ProductPassport.load(r, passport_id)
    if pp is None:
        raise HTTPException(404, f"Passport '{passport_id}' not found (may have expired)")
    ok, result = push_passport(pp, r, skip_score_check=True, skip_blacklist_check=True)
    if not ok:
        raise HTTPException(500, f"Push failed: {result}")
    return JSONResponse({"ok": True, "passport_id": passport_id, "job_id": result})


@app.post("/api/v1/products/{passport_id}/reject")
def reject_product(passport_id: str, body: RejectBody, request: Request) -> JSONResponse:
    """Blacklist a passport's affiliate_url for N days."""
    _require_auth(request)
    r = _redis()
    pp = ProductPassport.load(r, passport_id)
    if pp is None:
        raise HTTPException(404, f"Passport '{passport_id}' not found")

    url = pp.affiliate_url.strip() or pp.source_url.strip()
    if url:
        url_hash = hashlib.sha256(url.encode("utf-8")).hexdigest()
        # Bare key (client adds klipaura: prefix)
        bare_prefix = BLACKLIST_PREFIX.removeprefix("klipaura:")
        bl_key = bare_prefix + url_hash
        ttl = body.ttl_days * 86400
        r.setex(bl_key, json.dumps({"reason": body.reason, "url": url}), ttl)

    pp.update_status(r, "rejected")
    return JSONResponse({"ok": True, "passport_id": passport_id, "blacklisted_url": url, "ttl_days": body.ttl_days})
