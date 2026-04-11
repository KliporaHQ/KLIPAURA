"""Upload static HTML to R2 / MinIO (S3 API)."""

from __future__ import annotations

from io import BytesIO
from typing import Any

from klip_core.storage.r2 import create_r2_store, r2_configured


def deploy_html(html: str, *, key: str, content_type: str = "text/html; charset=utf-8") -> dict[str, Any]:
    """
    Upload ``html`` to object key ``key`` (e.g. ``funnels/{job_id}/index.html``).
    Returns ``{ "ok": bool, "url": str | None, "error": str | None }``.
    """
    if not r2_configured():
        return {"ok": False, "url": None, "error": "R2 / MinIO not configured (set R2_* env)"}
    try:
        store = create_r2_store()
        buf = BytesIO(html.encode("utf-8"))
        url = store.upload_fileobj(buf, key, content_type=content_type)
        return {"ok": True, "url": url or None, "error": None}
    except Exception as e:
        return {"ok": False, "url": None, "error": str(e)}
