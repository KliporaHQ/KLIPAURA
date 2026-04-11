from __future__ import annotations

import csv
import hashlib
import os
from pathlib import Path
from typing import Any


def _url_hash(url: str) -> str:
    return hashlib.sha256(url.strip().encode("utf-8")).hexdigest()


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


def _clamp_int(v: float, lo: int = 0, hi: int = 100) -> int:
    if v != v:
        return lo
    if v < lo:
        return lo
    if v > hi:
        return hi
    return int(round(v))


def _load_csv_rows(path: str) -> list[dict[str, Any]]:
    p = Path(path).resolve()
    if not p.is_file():
        return []
    with open(p, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return [dict(r) for r in reader]


def clickbank_rows() -> list[dict[str, Any]]:
    path = (os.getenv("CLICKBANK_CSV") or "").strip()
    if not path:
        return []
    return _load_csv_rows(path)


def temu_rows() -> list[dict[str, Any]]:
    path = (os.getenv("TEMU_CSV") or "").strip()
    if not path:
        return []
    return _load_csv_rows(path)


def opportunities_from_rows(source: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for r in rows:
        url = (r.get("url") or r.get("link") or "").strip()
        if not url:
            continue

        title = (r.get("title") or r.get("name") or "").strip() or url[:512]

        trend_raw = _parse_float(r.get("trend_score") or r.get("trend") or r.get("gravity"), default=0.0)
        if 0.0 <= trend_raw <= 1.0:
            trend_score = _clamp_int(trend_raw * 100.0)
        else:
            trend_score = _clamp_int(trend_raw)

        commission = _parse_float(r.get("commission_rate") or r.get("commission") or r.get("payout"), default=0.0)
        affiliate_score = _clamp_int(commission * 10.0)

        out.append(
            {
                "source": source,
                "title": title[:512],
                "description": (r.get("description") or "").strip() or None,
                "url": url,
                "trend_score": trend_score,
                "affiliate_score": affiliate_score,
                "content_angle": (r.get("content_angle") or "").strip() or None,
                "raw": dict(r),
                "dedupe_hash": _url_hash(url),
            }
        )
    return out
