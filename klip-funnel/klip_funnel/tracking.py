"""UTM + simple conversion pixel helpers for funnel pages."""

from __future__ import annotations

from urllib.parse import urlencode, urlparse, urlunparse


def append_utm(url: str, *, source: str = "klipaura", medium: str = "funnel", campaign: str = "default") -> str:
    p = urlparse(url)
    q = dict(x.split("=", 1) for x in p.query.split("&") if "=" in x) if p.query else {}
    q.setdefault("utm_source", source)
    q.setdefault("utm_medium", medium)
    q.setdefault("utm_campaign", campaign)
    new_q = urlencode(q)
    return urlunparse((p.scheme, p.netloc, p.path, p.params, new_q, p.fragment))


def conversion_pixel_snippet(pixel_url: str | None) -> str:
    """1×1 img tag for server-side log hosts (optional)."""
    if not pixel_url:
        return ""
    safe = pixel_url.replace('"', "&quot;")
    return f'<img src="{safe}" alt="" width="1" height="1" style="display:none" />'
