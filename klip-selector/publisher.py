"""publisher.py — validate ProductPassport and push pointer to klipaura:jobs:pending.

Flow:
    1. Resolve affiliate_url if empty via infrastructure.affiliate_programs
    2. Validate passport.is_valid()
    3. Check klipaura:blacklist:{url_hash} — skip if blacklisted
    4. passport.save(redis_client) — stores at product:passport:{id}
    5. redis_client.rpush(JOBS_PENDING, json.dumps({passport_id, job_id, submitted_at}))
    6. create_manifest(job_id, {...}) for tracking
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

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

from infrastructure.product_passport import ProductPassport
from infrastructure.queue_names import BLACKLIST_PREFIX, JOBS_PENDING
from infrastructure.job_state import create_manifest


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _url_hash(url: str) -> str:
    return hashlib.sha256(url.strip().encode("utf-8")).hexdigest()


def _is_blacklisted(r: Any, url: str) -> bool:
    """Check if affiliate_url is in the Redis blacklist."""
    if not url:
        return False
    # BLACKLIST_PREFIX = "klipaura:blacklist:" — this is a full key passed to redis which adds namespace
    # Strip the klipaura: prefix since redis_client adds it
    bare_key = BLACKLIST_PREFIX.removeprefix("klipaura:") + _url_hash(url)
    try:
        return bool(r.exists(bare_key))
    except Exception:
        return False


def _resolve_affiliate_url(passport: ProductPassport) -> ProductPassport:
    """If affiliate_url is empty, try to resolve it via affiliate_programs config."""
    if passport.affiliate_url.strip():
        return passport
    try:
        from infrastructure.affiliate_programs import resolve_affiliate_data_for_job
        data = resolve_affiliate_data_for_job(
            passport.network,
            product_url=passport.source_url or passport.affiliate_url,
        )
        link = str(data.get("affiliate_link") or "").strip()
        if link:
            passport.affiliate_url = link
    except Exception:
        pass
    return passport


def push_passport(
    passport: ProductPassport,
    redis_client: Any,
    *,
    skip_score_check: bool = False,
    skip_blacklist_check: bool = False,
) -> tuple[bool, str]:
    """Validate, store, and queue a ProductPassport.

    Returns ``(True, job_id)`` on success or ``(False, reason)`` on failure.
    """
    # Step 1: resolve affiliate URL
    passport = _resolve_affiliate_url(passport)

    # Step 2: validate
    valid, reason = passport.is_valid()
    if not valid:
        print(f"[publisher] Invalid passport {passport.passport_id}: {reason}", flush=True)
        return False, f"INVALID:{reason}"

    # Step 3: blacklist check
    if not skip_blacklist_check and _is_blacklisted(redis_client, passport.affiliate_url):
        print(f"[publisher] Blacklisted URL: {passport.affiliate_url[:80]}", flush=True)
        return False, "BLACKLISTED"

    # Step 4: persist passport to Redis (bare key — client adds klipaura: prefix)
    try:
        passport.save(redis_client)
    except Exception as exc:
        print(f"[publisher] Redis save failed: {exc}", flush=True)
        return False, f"REDIS_SAVE_ERROR:{exc}"

    # Step 5: push pointer to pending queue
    job_id = f"job-{uuid.uuid4()}"
    submitted_at = _now_iso()
    pointer = json.dumps({
        "passport_id": passport.passport_id,
        "job_id": job_id,
        "submitted_at": submitted_at,
        "avatar_id": passport.avatar_id,
        "video_format": passport.video_format,
        "network": passport.network,
    })
    try:
        redis_client.rpush(JOBS_PENDING, pointer)
    except Exception as exc:
        print(f"[publisher] Queue push failed: {exc}", flush=True)
        return False, f"QUEUE_PUSH_ERROR:{exc}"

    # Step 6: create manifest for tracking
    try:
        create_manifest(job_id, {
            "passport_id": passport.passport_id,
            "avatar_id": passport.avatar_id,
            "video_format": passport.video_format,
            "network": passport.network,
            "affiliate_url": passport.affiliate_url,
            "title": passport.title,
            "score": passport.score,
            "submitted_at": submitted_at,
        })
    except Exception as exc:
        # Non-fatal — job is already queued
        print(f"[publisher] Manifest create warning: {exc}", flush=True)

    print(
        f"[publisher] Queued passport={passport.passport_id} job={job_id} "
        f"score={passport.score:.1f} format={passport.video_format} avatar={passport.avatar_id}",
        flush=True,
    )
    return True, job_id


def push_adapter_product(
    product: Any,
    redis_client: Any,
    *,
    avatar_id: str = "",
    skip_score_check: bool = False,
) -> tuple[bool, str]:
    """Create a ProductPassport from an AdapterProduct and queue it.

    Scoring and format routing must be done BEFORE calling this.
    ``product`` must have a ``._score`` attribute set by the opportunity engine.
    """
    from routing.format_router import format_for_product

    score = float(getattr(product, "_score", 0.0))
    video_format = format_for_product(product.category, product.network)

    passport = ProductPassport.new(
        network=product.network,
        title=product.title,
        images=product.images,
        price=product.price,
        description=product.description,
        affiliate_url=product.url,
        source_url=product.source_url,
        commission_rate=product.commission_rate,
        score=score,
        avatar_id=avatar_id,
        video_format=video_format,
        category=product.category,
        status="queued",
        meta=product.meta,
    )
    return push_passport(passport, redis_client, skip_score_check=skip_score_check)
