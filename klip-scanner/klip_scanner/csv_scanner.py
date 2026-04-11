from __future__ import annotations

import hashlib
import os
from typing import Any

from sqlalchemy.dialects.postgresql import insert

from infrastructure.db import db_configured
from infrastructure.db_models import Opportunity
from infrastructure.db_session import get_session
from klip_selector.manual_feeder import load_products


def _url_hash(url: str) -> str:
    return hashlib.sha256(url.strip().encode("utf-8")).hexdigest()


def _clamp_int(v: float, lo: int = 0, hi: int = 100) -> int:
    if v != v:
        return lo
    if v < lo:
        return lo
    if v > hi:
        return hi
    return int(round(v))


def _parse_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return float(default)
        s = str(v).strip()
        if not s:
            return float(default)
        return float(s)
    except Exception:
        return float(default)


def ingest_products_csv(csv_path: str | None = None) -> dict[str, Any]:
    if not db_configured():
        raise RuntimeError("DATABASE_URL not set")

    if csv_path:
        rows = load_products(csv_path=csv_path)
    else:
        rows = load_products(csv_path=None)

    source = (os.getenv("SCANNER_SOURCE") or "products_csv").strip() or "products_csv"

    values: list[dict[str, Any]] = []
    for r in rows:
        url = (r.get("url") or "").strip()
        if not url:
            continue

        trend_raw = _parse_float(r.get("trend_score"), default=0.5)
        if 0.0 <= trend_raw <= 1.0:
            trend_score = _clamp_int(trend_raw * 100.0)
        else:
            trend_score = _clamp_int(trend_raw)

        commission = _parse_float(r.get("commission_rate"), default=0.0)
        affiliate_score = _clamp_int(commission * 10.0)

        values.append(
            {
                "source": source,
                "title": (r.get("title") or "").strip() or url[:512],
                "description": None,
                "url": url,
                "trend_score": trend_score,
                "affiliate_score": affiliate_score,
                "content_angle": None,
                "raw": dict(r),
                "dedupe_hash": _url_hash(url),
            }
        )

    inserted = 0
    scanned = len(values)

    if not values:
        return {"ok": True, "scanned": 0, "inserted": 0}

    with get_session() as session:
        stmt = insert(Opportunity).values(values)
        stmt = stmt.on_conflict_do_nothing(index_elements=["dedupe_hash"])
        res = session.execute(stmt)
        session.commit()
        try:
            inserted = int(res.rowcount or 0)
        except Exception:
            inserted = 0

    return {"ok": True, "scanned": scanned, "inserted": inserted}
