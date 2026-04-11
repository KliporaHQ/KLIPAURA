from __future__ import annotations

import os
import time
from typing import Any

import requests

from infrastructure.db import get_session
from infrastructure.db_models import OpportunitySource


def discover_and_track_sources() -> dict[str, Any]:
    """
    Minimal discovery agent: checks known affiliate network health and persists status.
    Returns summary with active/inactive networks and any newly discovered sources.
    """
    networks = [
        {"name": "ClickBank", "url": "https://www.clickbank.com", "priority": 1},
        {"name": "Temu", "url": "https://www.temu.com", "priority": 1},
        {"name": "Amazon Associates", "url": "https://affiliate-program.amazon.com", "priority": 1},
        {"name": "WarriorPlus", "url": "https://www.warriorplus.com", "priority": 2},
        {"name": "JVZoo", "url": "https://www.jvzoo.com", "priority": 2},
        {"name": "Digistore24", "url": "https://www.digistore24.com", "priority": 2},
        {"name": "ShareASale", "url": "https://www.shareasale.com", "priority": 2},
        {"name": "CJ Affiliate", "url": "https://www.cj.com", "priority": 2},
        {"name": "Hotmart", "url": "https://www.hotmart.com", "priority": 2},
        {"name": "AliExpress", "url": "https://portals.aliexpress.com", "priority": 2},
        {"name": "Impact.com", "url": "https://www.impact.com", "priority": 3},
        {"name": "ElevenLabs", "url": "https://elevenlabs.io", "priority": 3},
        {"name": "WaveSpeed", "url": "https://wavespeed.ai", "priority": 3},
    ]

    out: dict[str, Any] = {"checked": len(networks), "active": 0, "inactive": 0, "new": 0, "details": []}
    with get_session() as sess:
        for net in networks:
            name = net["name"]
            url = net["url"]
            priority = net["priority"]
            try:
                start = time.time()
                r = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
                elapsed = time.time() - start
                status = "active" if r.status_code == 200 else "inactive"
                if status == "active":
                    out["active"] += 1
                else:
                    out["inactive"] += 1
                detail = {
                    "name": name,
                    "url": url,
                    "priority": priority,
                    "status": status,
                    "http_status": r.status_code,
                    "response_time_sec": round(elapsed, 2),
                }
            except Exception as e:
                out["inactive"] += 1
                detail = {
                    "name": name,
                    "url": url,
                    "priority": priority,
                    "status": "inactive",
                    "error": str(e)[:100],
                }

            out["details"].append(detail)

            # Upsert to Postgres
            existing = sess.query(OpportunitySource).filter_by(source_name=name).one_or_none()
            if existing:
                existing.status = detail["status"]
                existing.last_checked_at = time.time()
                if "http_status" in detail:
                    existing.metadata = {**existing.metadata, "http_status": detail["http_status"], "response_time_sec": detail["response_time_sec"]}
                else:
                    existing.metadata = {**existing.metadata, "error": detail.get("error")}
            else:
                rec = OpportunitySource(
                    source_name=name,
                    source_url=url,
                    priority=priority,
                    status=detail["status"],
                    last_checked_at=time.time(),
                    metadata={"http_status": detail.get("http_status"), "response_time_sec": detail.get("response_time_sec"), "error": detail.get("error")},
                )
                sess.add(rec)
                out["new"] += 1
        sess.commit()
    return out


def run_discovery_cycle() -> dict[str, Any]:
    """
    Entry point for scheduled discovery (e.g., cron or Railway scheduler).
    Returns summary and persists to Postgres.
    """
    return discover_and_track_sources()
