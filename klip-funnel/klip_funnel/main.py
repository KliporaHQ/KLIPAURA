"""klip-funnel — landing builder API + health."""
from __future__ import annotations

import json
import uuid
from typing import Any, Optional

from fastapi import FastAPI
from pydantic import BaseModel, Field

from klip_core.redis.client import get_redis_client_optional
from klip_core.redis.queues import QUEUE_NAMES

from klip_funnel.builder import build_landing_page
from klip_funnel.deploy import deploy_html
from klip_funnel.tracking import append_utm, conversion_pixel_snippet

app = FastAPI(title="klip-funnel", version="0.2.0")


class FunnelBuildRequest(BaseModel):
    title: str = Field(..., min_length=2)
    product_url: str
    category: str = ""
    description: str = ""
    cta_label: str = "Shop now"
    deploy: bool = False
    tracking_pixel_url: Optional[str] = None


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "module": "klip-funnel"}


@app.post("/api/v1/funnel/build")
def api_build(body: FunnelBuildRequest) -> dict[str, Any]:
    product: dict[str, Any] = {
        "title": body.title,
        "product_title": body.title,
        "product_url": body.product_url,
        "affiliate_url": append_utm(body.product_url),
        "category": body.category,
        "description": body.description,
        "cta_label": body.cta_label,
    }
    built = build_landing_page(product, niche=body.category or "base")
    html = built["html"]
    if body.tracking_pixel_url:
        html = html.replace("</body>", conversion_pixel_snippet(body.tracking_pixel_url) + "</body>")
    out: dict[str, Any] = {"html": html, "slug": built["slug"], "niche": built["niche"]}
    if body.deploy:
        job_id = str(uuid.uuid4())[:8]
        key = f"funnels/{job_id}/{built['slug']}.html"
        dep = deploy_html(html, key=key)
        out["deploy"] = dep
    return out


@app.post("/api/v1/funnel/enqueue-sample")
def enqueue_sample() -> dict[str, Any]:
    """LPUSH a sample funnel job for worker testing (optional)."""
    r = get_redis_client_optional()
    if r is None:
        return {"ok": False, "error": "Redis unavailable"}
    try:
        payload = {"job_id": str(uuid.uuid4())[:12], "title": "Sample", "product_url": "https://example.com"}
        r.lpush(QUEUE_NAMES.funnel_projects, json.dumps(payload))
        return {"ok": True, "queue": QUEUE_NAMES.funnel_projects}
    except Exception as e:
        return {"ok": False, "error": str(e)}
