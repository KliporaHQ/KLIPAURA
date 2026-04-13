"""ManualAdapter — loads products from PRODUCTS_CSV (via klip-scanner manual_feeder)."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import List

_REPO = Path(__file__).resolve().parents[2]
_SCANNER = _REPO / "klip-scanner"
for _p in [str(_REPO), str(_SCANNER)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from .base import AffiliateAdapter, AdapterProduct


class ManualAdapter(AffiliateAdapter):
    """Reads products from a local CSV file (PRODUCTS_CSV env var or products.csv)."""

    network = "manual"

    def __init__(self, csv_path: str | None = None) -> None:
        self._csv_path = csv_path or os.getenv("PRODUCTS_CSV") or str(_REPO / "products.csv")

    def fetch(self, limit: int = 20) -> List[AdapterProduct]:
        from klip_selector.manual_feeder import load_products

        rows = load_products(Path(self._csv_path))
        out: List[AdapterProduct] = []
        for row in rows[:limit]:
            url = row.get("url") or ""
            try:
                cr = float(row.get("commission_rate") or 0.0)
            except (TypeError, ValueError):
                cr = 0.0
            try:
                trend = float(row.get("trend_score") or 0.5)
            except (TypeError, ValueError):
                trend = 0.5
            out.append(
                AdapterProduct(
                    network=self.network,
                    title=(row.get("title") or "").strip(),
                    url=url,
                    source_url=url,
                    price="",
                    images=[],
                    category=(row.get("category") or "").strip(),
                    description="",
                    commission_rate=cr,
                    trend_score=min(1.0, max(0.0, trend)),
                    asin=(row.get("asin") or "").strip(),
                )
            )
        return out
