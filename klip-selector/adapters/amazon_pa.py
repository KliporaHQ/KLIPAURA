"""AmazonPAAdapter — uses paapi5-python-sdk to discover products."""

from __future__ import annotations

import os
from typing import List

from .base import AffiliateAdapter, AdapterProduct

_MARKETPLACE = os.getenv("AMAZON_PA_MARKETPLACE", "www.amazon.ae")
_PARTNER_TAG = os.getenv("AMAZON_PA_PARTNER_TAG", "")
_ACCESS_KEY = os.getenv("AMAZON_PA_API_ACCESS_KEY", "")
_SECRET_KEY = os.getenv("AMAZON_PA_API_SECRET_KEY", "")


class AmazonPAAdapter(AffiliateAdapter):
    """Amazon Product Advertising API v5 adapter.

    Credentials are read from environment:
        AMAZON_PA_API_ACCESS_KEY, AMAZON_PA_API_SECRET_KEY, AMAZON_PA_PARTNER_TAG,
        AMAZON_PA_MARKETPLACE (default: www.amazon.ae)

    Returns empty list when credentials are missing (non-fatal).
    """

    network = "amazon"

    def __init__(self, keywords: List[str] | None = None) -> None:
        self._keywords = keywords or ["beauty", "skincare", "home appliance", "kitchen gadget"]

    def _is_configured(self) -> bool:
        return bool(_ACCESS_KEY and _SECRET_KEY and _PARTNER_TAG)

    def fetch(self, limit: int = 20) -> List[AdapterProduct]:
        if not self._is_configured():
            print("[amazon_pa] Credentials not configured — skipping PA API fetch.", flush=True)
            return []
        try:
            return self._fetch_live(limit)
        except Exception as exc:
            print(f"[amazon_pa] Fetch error: {exc}", flush=True)
            return []

    def _fetch_live(self, limit: int) -> List[AdapterProduct]:
        """Call paapi5-python-sdk.  Returns AdapterProduct list."""
        from paapi5_python_sdk.api.default_api import DefaultApi  # type: ignore
        from paapi5_python_sdk.models.search_items_request import SearchItemsRequest  # type: ignore
        from paapi5_python_sdk.models.partner_type import PartnerType  # type: ignore
        from paapi5_python_sdk.rest import ApiException  # type: ignore
        import paapi5_python_sdk  # type: ignore

        config = paapi5_python_sdk.Configuration()
        config.access_key = _ACCESS_KEY
        config.secret_key = _SECRET_KEY
        config.host = f"webservices.{_MARKETPLACE}"
        config.region = "us-east-1"  # PA API uses us-east-1 regardless of marketplace

        client = paapi5_python_sdk.ApiClient(configuration=config)
        api = DefaultApi(api_client=client)

        out: List[AdapterProduct] = []
        per_kw = max(1, limit // len(self._keywords))

        for kw in self._keywords:
            if len(out) >= limit:
                break
            try:
                req = SearchItemsRequest(
                    partner_tag=_PARTNER_TAG,
                    partner_type=PartnerType.ASSOCIATES,
                    keywords=kw,
                    search_index="All",
                    item_count=min(10, per_kw),
                    resources=[
                        "Images.Primary.Large",
                        "ItemInfo.Title",
                        "Offers.Listings.Price",
                        "ItemInfo.ByLineInfo",
                    ],
                )
                resp = api.search_items(req)
                if not resp or not resp.search_result:
                    continue
                for item in (resp.search_result.items or []):
                    title = ""
                    try:
                        title = item.item_info.title.display_value or ""
                    except Exception:
                        pass
                    price = ""
                    try:
                        price = item.offers.listings[0].price.display_amount or ""
                    except Exception:
                        pass
                    image = ""
                    try:
                        image = item.images.primary.large.url or ""
                    except Exception:
                        pass
                    affiliate_url = item.detail_page_url or ""
                    out.append(
                        AdapterProduct(
                            network=self.network,
                            title=title,
                            url=affiliate_url,
                            source_url=affiliate_url,
                            price=price,
                            images=[image] if image else [],
                            category=kw,
                            description="",
                            commission_rate=4.0,  # Amazon AE standard ~4%
                            trend_score=0.6,
                            asin=item.asin or "",
                        )
                    )
            except ApiException as exc:
                print(f"[amazon_pa] API error for kw={kw!r}: {exc}", flush=True)
                continue

        return out[:limit]
