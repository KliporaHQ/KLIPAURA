#!/usr/bin/env python3
"""klip-selector scheduler — APScheduler + FastAPI.

Runs as a single Railway service:
    python klip-selector/scheduler.py

Environment:
    SELECTOR_INTERVAL_MINUTES  — how often to run a discovery cycle (default 60)
    SELECTOR_LIMIT             — max products per cycle (default 5)
    SELECTOR_API_KEY           — X-API-Key for the REST API
    PORT                       — HTTP port (default 8001)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
_SCANNER = _REPO / "klip-scanner"

# Ensure imports work from any working directory
for _p in [str(_REPO), str(_SCANNER), str(_HERE)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    from dotenv import load_dotenv, find_dotenv
    load_dotenv(find_dotenv(usecwd=False), override=False)
except ImportError:
    pass

import logging

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("klip-selector")

import uvicorn
from apscheduler.schedulers.background import BackgroundScheduler

from api import app

_INTERVAL_MINUTES = int(os.getenv("SELECTOR_INTERVAL_MINUTES", "60"))
_SELECTOR_LIMIT = int(os.getenv("SELECTOR_LIMIT", "5"))
_PORT = int(os.getenv("PORT", "8001"))


def run_cycle() -> None:
    """One full selector cycle: fetch → filter → score → queue."""
    logger.info("[scheduler] Starting discovery cycle (limit=%d)", _SELECTOR_LIMIT)
    try:
        from infrastructure.redis_client import get_redis_client_optional, RedisConfigError
        r = get_redis_client_optional()
        if r is None:
            logger.error("[scheduler] Redis unavailable — skipping cycle")
            return

        from infrastructure.avatar_loader import AvatarLoader
        loader = AvatarLoader()
        active = loader.list_active()
        if not active:
            logger.warning("[scheduler] NO_ACTIVE_AVATARS — skipping cycle")
            return
        avatar_id = active[0]["avatar_id"]
        logger.info("[scheduler] Using avatar: %s", avatar_id)

        # Run adapters
        from adapters.manual import ManualAdapter
        from adapters.temu import TemuAdapter
        from adapters.amazon_pa import AmazonPAAdapter
        from adapters.clickbank import ClickBankAdapter

        all_products = []
        for Adapter in [ManualAdapter, TemuAdapter, AmazonPAAdapter, ClickBankAdapter]:
            try:
                adapter = Adapter()
                products = adapter.fetch(limit=_SELECTOR_LIMIT * 2)
                all_products.extend(products)
            except Exception as exc:
                logger.warning("[scheduler] Adapter %s error: %s", Adapter.__name__, exc)

        # Filter
        from klip_selector.uae_filter import passes as uae_passes
        import hashlib, json
        from infrastructure.queue_names import BLACKLIST_PREFIX

        def _is_blacklisted(url: str) -> bool:
            if not url:
                return False
            bare_prefix = BLACKLIST_PREFIX.removeprefix("klipaura:")
            bl_key = bare_prefix + hashlib.sha256(url.encode()).hexdigest()
            try:
                return bool(r.exists(bl_key))
            except Exception:
                return False

        # Reuse UAE filter with duck-typing (convert AdapterProduct to dict-like)
        filtered = []
        for p in all_products:
            row = {"title": p.title, "url": p.url, "category": p.category}
            if uae_passes(row) and not _is_blacklisted(p.url):
                filtered.append(p)

        # Score
        from scoring.opportunity_engine import score_product, is_above_threshold
        scored = []
        for p in filtered:
            s = score_product(
                commission_rate=p.commission_rate,
                trend_score=p.trend_score,
                category=p.category,
                price=p.price,
            )
            p._score = s  # type: ignore[attr-defined]
            if is_above_threshold(s):
                scored.append(p)

        # Sort and take top N
        scored.sort(key=lambda x: x._score, reverse=True)  # type: ignore[attr-defined]
        top = scored[:_SELECTOR_LIMIT]

        if not top:
            logger.info("[scheduler] No products above threshold this cycle")
            return

        # Queue
        from publisher import push_adapter_product
        queued = 0
        for product in top:
            ok, result = push_adapter_product(product, r, avatar_id=avatar_id)
            if ok:
                queued += 1

        logger.info("[scheduler] Cycle complete — queued=%d/%d", queued, len(top))

    except Exception as exc:
        logger.exception("[scheduler] Cycle error: %s", exc)


def main() -> None:
    scheduler = BackgroundScheduler()
    scheduler.add_job(
        run_cycle,
        "interval",
        minutes=_INTERVAL_MINUTES,
        id="selector_cycle",
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    logger.info(
        "[scheduler] Started — interval=%dm limit=%d port=%d",
        _INTERVAL_MINUTES, _SELECTOR_LIMIT, _PORT,
    )

    # Run one cycle immediately on startup
    try:
        run_cycle()
    except Exception as exc:
        logger.warning("[scheduler] Startup cycle error: %s", exc)

    try:
        uvicorn.run(app, host="0.0.0.0", port=_PORT, log_level="warning")
    finally:
        scheduler.shutdown(wait=False)


if __name__ == "__main__":
    main()
