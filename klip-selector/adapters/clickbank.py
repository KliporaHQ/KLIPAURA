"""ClickBankAdapter — ClickBank marketplace product discovery."""

from __future__ import annotations

import os
from typing import List

from .base import AffiliateAdapter, AdapterProduct

_CLICKBANK_API_KEY = os.getenv("CLICKBANK_API_KEY", "")
_CLICKBANK_AFFILIATE_ID = os.getenv("CLICKBANK_AFFILIATE_ID", "")


class ClickBankAdapter(AffiliateAdapter):
    """ClickBank Marketplace API adapter.

    Credentials from env: CLICKBANK_API_KEY, CLICKBANK_AFFILIATE_ID
    Returns empty list when credentials are missing (non-fatal).
    """

    network = "clickbank"

    def __init__(self, categories: List[str] | None = None) -> None:
        self._categories = categories or ["health", "beauty", "self-help"]

    def _is_configured(self) -> bool:
        return bool(_CLICKBANK_API_KEY and _CLICKBANK_AFFILIATE_ID)

    def fetch(self, limit: int = 20) -> List[AdapterProduct]:
        if not self._is_configured():
            print("[clickbank] Credentials not configured — skipping ClickBank fetch.", flush=True)
            return []
        try:
            return self._fetch_live(limit)
        except Exception as exc:
            print(f"[clickbank] Fetch error: {exc}", flush=True)
            return []

    def _fetch_live(self, limit: int) -> List[AdapterProduct]:
        """Query ClickBank Marketplace API."""
        import urllib.request
        import urllib.parse
        import json

        out: List[AdapterProduct] = []
        per_cat = max(1, limit // len(self._categories))

        for cat in self._categories:
            if len(out) >= limit:
                break
            try:
                params = urllib.parse.urlencode({
                    "site": cat,
                    "affiliateId": _CLICKBANK_AFFILIATE_ID,
                    "rows": str(per_cat),
                    "orderBy": "RANK",
                })
                url = f"https://api.clickbank.com/rest/1.3/marketplace/products?{params}"
                req = urllib.request.Request(
                    url,
                    headers={"Authorization": _CLICKBANK_API_KEY},
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    data = json.loads(resp.read())
                products = data.get("products") or []
                for p in products:
                    vendor = p.get("site") or p.get("vendor", "")
                    title = p.get("title") or p.get("siteName", vendor)
                    commission = float(p.get("commissionRate") or 50.0)
                    hoplink = f"https://{_CLICKBANK_AFFILIATE_ID}.{vendor}.hop.clickbank.net"
                    out.append(
                        AdapterProduct(
                            network=self.network,
                            title=str(title),
                            url=hoplink,
                            source_url=f"https://{vendor}.com",
                            price="",
                            images=[],
                            category=cat,
                            description=str(p.get("description") or ""),
                            commission_rate=commission,
                            trend_score=0.5,
                        )
                    )
            except Exception as exc:
                print(f"[clickbank] Error for cat={cat!r}: {exc}", flush=True)
                continue

        return out[:limit]
