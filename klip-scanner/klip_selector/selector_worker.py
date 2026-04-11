#!/usr/bin/env python3
"""
Load products.csv → UAE filter → score → Redis blacklist check → RPUSH top N jobs.

Run from repo root:
  python -m klip_selector.selector_worker

Env: PRODUCTS_CSV, SELECTOR_LIMIT (default 5), SELECTOR_AVATAR_ID (default theanikaglow), Redis same as worker.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import uuid
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

try:
    from dotenv import load_dotenv

    load_dotenv(_REPO / ".env", override=False)
except ImportError:
    pass

from infrastructure.job_state import create_manifest
from infrastructure.queue_names import BLACKLIST_PREFIX, JOBS_PENDING
from infrastructure.redis_client import RedisConfigError, get_redis_client

from klip_selector.layout_router import layout_for_category
from klip_selector.manual_feeder import load_products
from klip_selector.scorer import rank_products
from klip_selector.uae_filter import passes as uae_passes
from klip_scanner.product_visuals import resolve_product_images


def _url_hash(url: str) -> str:
    return hashlib.sha256(url.strip().encode("utf-8")).hexdigest()


def is_blacklisted(r, url: str) -> bool:
    key = BLACKLIST_PREFIX + _url_hash(url)
    try:
        return bool(r.exists(key))
    except Exception:
        return False


def run_cycle(limit: int | None = None, avatar_id: str | None = None) -> int:
    lim = limit if limit is not None else int(os.getenv("SELECTOR_LIMIT", "5"))
    av = (avatar_id or os.getenv("SELECTOR_AVATAR_ID") or "theanikaglow").strip()

    try:
        redis = get_redis_client()
    except RedisConfigError as e:
        print("[selector] Redis unavailable:", e, flush=True)
        return 2

    rows = load_products()
    filtered = [r for r in rows if uae_passes(r) and not is_blacklisted(redis, r["url"])]
    ranked = rank_products(filtered)
    top = ranked[:lim]

    pushed = 0
    for row in top:
        job_id = str(uuid.uuid4())
        layout = layout_for_category(row.get("category") or "")
        title = (row.get("title") or "").strip()
        url = row["url"]
        title2, imgs, _meta = resolve_product_images(url, title)
        if ("temu.com" in url.lower() or "temu.to" in url.lower()) and not imgs:
            print(f"[selector] skip {url[:80]}... reason=no_product_images", flush=True)
            continue
        payload = {
            "job_id": job_id,
            "product_url": url,
            "avatar_id": av,
            "retry_count": 0,
            "layout_hint": layout,
            "asin": row.get("asin") or "",
            "title": title2 or title or "",
            "product_title": title2 or title or "",
            "product_image_urls": imgs,
        }
        create_manifest(job_id, payload)
        redis.rpush(JOBS_PENDING, json.dumps({**payload, "_selector_score": row.get("_score")}))
        pushed += 1
        print(f"[selector] queued {job_id} score={row.get('_score'):.4f} url={row['url'][:60]}...", flush=True)

    print(f"[selector] done pushed={pushed}", flush=True)
    return 0


def main() -> None:
    raise SystemExit(run_cycle())


if __name__ == "__main__":
    main()
