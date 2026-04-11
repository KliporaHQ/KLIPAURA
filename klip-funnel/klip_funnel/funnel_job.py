"""Build + publish funnel HTML for a video job (R2 or local ``jobs/<id>/``)."""

from __future__ import annotations

import os
from io import BytesIO
from pathlib import Path
from typing import Any

from klip_funnel.builder import build_mobile_funnel_page


def _enrich_job_for_funnel(repo_root: Path, job: dict[str, Any]) -> dict[str, Any]:
    out = dict(job)
    aid = str(out.get("avatar_id") or "").strip()
    if aid:
        try:
            from infrastructure.avatar_registry import display_name_for_avatar

            out["avatar_display_name"] = display_name_for_avatar(aid)
        except Exception:
            out["avatar_display_name"] = aid
    return out


def publish_funnel_html(repo_root: Path, html: str, job_id: str, slug: str) -> dict[str, Any]:
    """Upload to R2 when configured; else write under ``jobs/<job_id>/funnel_<slug>.html``."""
    key = f"funnels/{job_id}/{slug}.html"
    try:
        from infrastructure.storage import create_r2_store, r2_configured

        if r2_configured():
            store = create_r2_store(prefix="assets")
            store.put(BytesIO(html.encode("utf-8")), "text/html; charset=utf-8", key=key)
            url = store.get_url(key)
            return {"ok": True, "url": url, "key": key, "local_path": None}
    except Exception:
        pass

    jdir = Path(os.getenv("JOBS_DIR", str(repo_root / "jobs"))).resolve() / job_id
    jdir.mkdir(parents=True, exist_ok=True)
    local = jdir / f"funnel_{slug}.html"
    local.write_text(html, encoding="utf-8")
    base = (os.getenv("FUNNEL_PUBLIC_BASE_URL") or "").strip().rstrip("/")
    if base:
        public = f"{base}/jobs/{job_id}/{local.name}"
    else:
        public = f"local://{local.as_posix()}"
    return {"ok": True, "url": public, "key": None, "local_path": str(local)}


def build_and_attach_funnel(repo_root: Path, job_id: str, job: dict[str, Any]) -> tuple[str | None, str | None]:
    """
    Returns ``(funnel_url, error)``. On success ``funnel_url`` is public or ``local://`` path.
    """
    try:
        enriched = _enrich_job_for_funnel(repo_root, job)
        built = build_mobile_funnel_page(enriched)
        html = built["html"]
        slug = built["slug"]
        pub = publish_funnel_html(repo_root, html, job_id, slug)
        if pub.get("ok") and pub.get("url"):
            return str(pub["url"]), None
        return None, str(pub.get("error") or "publish failed")
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"[:400]
