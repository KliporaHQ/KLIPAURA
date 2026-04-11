from __future__ import annotations

import os
from typing import Any

from infrastructure.db import db_configured
from klip_selector.manual_feeder import load_products

from klip_scanner.opportunity_store import insert_opportunities
from klip_scanner.csv_scanner import ingest_products_csv
from klip_scanner.google_images import search_images
from klip_scanner.sources import clickbank_rows, opportunities_from_rows, temu_rows
from klip_scanner.temu_images import extract_title_and_images
from klip_scanner.uae_compliance import check_compliance_violations

def run_scanner(*, include_amazon: bool = True, include_clickbank: bool = True, include_temu: bool = True, geo_target: str = "AE") -> dict[str, Any]:
    if not db_configured():
        raise RuntimeError("DATABASE_URL not set")

    out: dict[str, Any] = {"ok": True, "sources": {}}

    if include_amazon:
        amazon_source = (os.getenv("AMAZON_SOURCE") or "amazon_associates").strip() or "amazon_associates"
        rows = load_products()
        values = opportunities_from_rows(amazon_source, rows)
        compliant_values: list[dict[str, Any]] = []
        blocked_values: list[dict[str, Any]] = []
        for v in values:
            title = (v.get("title") or "").strip()
            description = (v.get("description") or "").strip()
            raw = v.get("raw") if isinstance(v.get("raw"), dict) else {}
            category = (v.get("category") or raw.get("category") or raw.get("niche") or "").strip()
            compliance = check_compliance_violations(title, description, category, geo_target)
            v["geo_target"] = geo_target
            v["compliance_score"] = int(compliance.get("compliance_score") or 0)
            v["compliance_data"] = compliance
            if compliance["auto_block"]:
                v["state"] = "blocked_compliance"
                blocked_values.append(v)
            else:
                compliant_values.append(v)
        inserted_ok = insert_opportunities(compliant_values)
        inserted_blocked = insert_opportunities(blocked_values)
        out["sources"][amazon_source] = {
            "scanned": len(values),
            "blocked": len(blocked_values),
            "inserted": inserted_ok,
            "inserted_blocked": inserted_blocked,
        }

    if include_clickbank:
        rows = clickbank_rows()
        values = opportunities_from_rows("clickbank", rows)
        # UAE compliance check for ClickBank
        compliant_values: list[dict[str, Any]] = []
        blocked_values: list[dict[str, Any]] = []
        for v in values:
            title = (v.get("title") or "").strip()
            description = (v.get("description") or "").strip()
            raw = v.get("raw") if isinstance(v.get("raw"), dict) else {}
            category = (v.get("category") or raw.get("category") or raw.get("niche") or "").strip()
            compliance = check_compliance_violations(title, description, category, geo_target)
            v["geo_target"] = geo_target
            v["compliance_score"] = int(compliance.get("compliance_score") or 0)
            v["compliance_data"] = compliance
            if compliance["auto_block"]:
                v["state"] = "blocked_compliance"
                blocked_values.append(v)
            else:
                compliant_values.append(v)
        inserted_ok = insert_opportunities(compliant_values)
        inserted_blocked = insert_opportunities(blocked_values)
        out["sources"]["clickbank"] = {
            "scanned": len(values),
            "blocked": len(blocked_values),
            "inserted": inserted_ok,
            "inserted_blocked": inserted_blocked,
        }

    if include_temu:
        rows = temu_rows()
        values = opportunities_from_rows("temu", rows)
        # UAE compliance check for Temu
        compliant_values: list[dict[str, Any]] = []
        blocked_values: list[dict[str, Any]] = []
        for v in values:
            url = (v.get("url") or "").strip()
            if not url:
                continue
            title = (v.get("title") or "").strip()
            description = (v.get("description") or "").strip()
            raw = v.get("raw") if isinstance(v.get("raw"), dict) else {}
            category = (v.get("category") or raw.get("category") or raw.get("niche") or "").strip()
            
            # UAE compliance check before processing
            compliance = check_compliance_violations(title, description, category, geo_target)
            v["geo_target"] = geo_target
            v["compliance_score"] = int(compliance.get("compliance_score") or 0)
            v["compliance_data"] = compliance
            if compliance["auto_block"]:
                v["state"] = "blocked_compliance"
                blocked_values.append(v)
                continue
                
            img_urls: list[str] = []
            try:
                t2, imgs = extract_title_and_images(url, limit=8)
                if t2 and not title:
                    v["title"] = t2[:512]
                img_urls = imgs
            except Exception:
                img_urls = []
            if not img_urls and title:
                img_urls = search_images(f"{title} white background", limit=6)
            raw = v.get("raw") if isinstance(v.get("raw"), dict) else {}
            v["raw"] = {**raw, "image_urls": img_urls}
            compliant_values.append(v)
        inserted_ok = insert_opportunities(compliant_values)
        inserted_blocked = insert_opportunities(blocked_values)
        out["sources"]["temu"] = {
            "scanned": len(values),
            "blocked": len(blocked_values),
            "inserted": inserted_ok,
            "inserted_blocked": inserted_blocked,
        }

    return out
