"""Load products from CSV (url, category, commission_rate, asin, title, trend_score)."""

from __future__ import annotations

import csv
import os
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_CSV = Path(os.getenv("PRODUCTS_CSV", str(_REPO_ROOT / "products.csv")))


def load_products(csv_path: Path | None = None) -> list[dict[str, Any]]:
    p = Path(csv_path or _DEFAULT_CSV).resolve()
    if not p.is_file():
        return []
    out: list[dict[str, Any]] = []
    with open(p, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            url = (row.get("url") or "").strip()
            if not url:
                continue
            out.append(
                {
                    "url": url,
                    "category": (row.get("category") or "").strip(),
                    "commission_rate": row.get("commission_rate") or "0",
                    "asin": (row.get("asin") or "").strip(),
                    "title": (row.get("title") or "").strip(),
                    "trend_score": row.get("trend_score") or "0.5",
                }
            )
    return out
