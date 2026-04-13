"""TemuAdapter — reads Temu feed CSV or falls back to manual products.csv rows tagged temu."""

from __future__ import annotations

import os
from pathlib import Path
from typing import List

from .base import AffiliateAdapter, AdapterProduct

_TEMU_AFFILIATE_CODE = os.getenv("TEMU_AFFILIATE_CODE", "")
_TEMU_AFFILIATE_LINK = os.getenv("TEMU_AFFILIATE_LINK", "")


def _build_temu_url(product_id: str) -> str:
    """Build affiliate URL for a Temu product ID."""
    base = f"https://temu.com/product/{product_id}.html"
    if _TEMU_AFFILIATE_CODE:
        return f"{base}?rreferrer={_TEMU_AFFILIATE_CODE}"
    return base


class TemuAdapter(AffiliateAdapter):
    """Temu affiliate adapter.

    Currently reads rows from PRODUCTS_CSV where network/source column is 'temu'.
    Extend _fetch_live() when Temu provides a product feed API.
    """

    network = "temu"

    def __init__(self, csv_path: str | None = None) -> None:
        self._csv_path = csv_path or os.getenv("PRODUCTS_CSV") or ""

    def fetch(self, limit: int = 20) -> List[AdapterProduct]:
        try:
            return self._fetch_from_csv(limit)
        except Exception as exc:
            print(f"[temu] Fetch error: {exc}", flush=True)
            return []

    def _fetch_from_csv(self, limit: int) -> List[AdapterProduct]:
        import csv

        path = Path(self._csv_path) if self._csv_path else Path("products.csv")
        if not path.is_file():
            return []

        out: List[AdapterProduct] = []
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if len(out) >= limit:
                    break
                source = (row.get("network") or row.get("source") or "").strip().lower()
                url = (row.get("url") or "").strip()
                if source != "temu" and "temu" not in url.lower():
                    continue
                try:
                    cr = float(row.get("commission_rate") or 5.0)
                except (TypeError, ValueError):
                    cr = 5.0
                try:
                    trend = float(row.get("trend_score") or 0.5)
                except (TypeError, ValueError):
                    trend = 0.5

                # Apply affiliate link if code is set and URL doesn't already have it
                aff_url = url
                if _TEMU_AFFILIATE_CODE and "rreferrer=" not in url:
                    aff_url = f"{url}&rreferrer={_TEMU_AFFILIATE_CODE}" if "?" in url else f"{url}?rreferrer={_TEMU_AFFILIATE_CODE}"

                out.append(
                    AdapterProduct(
                        network=self.network,
                        title=(row.get("title") or "").strip(),
                        url=aff_url,
                        source_url=url,
                        price="",
                        images=[],
                        category=(row.get("category") or "").strip(),
                        description="",
                        commission_rate=cr,
                        trend_score=min(1.0, max(0.0, trend)),
                    )
                )
        return out
