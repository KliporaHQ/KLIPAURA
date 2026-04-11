"""Lightweight product page fetch for affiliate script context (Core V1)."""

from __future__ import annotations

import re
from html import unescape
from typing import Any
from urllib.parse import urlparse

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None  # type: ignore


def _meta_content(html: str, prop_or_name: str) -> str | None:
    # property="og:title" or name="twitter:title"
    pat = re.compile(
        rf'<meta[^>]+(?:property|name)\s*=\s*["\']{re.escape(prop_or_name)}["\'][^>]+content\s*=\s*["\']([^"\']+)["\']',
        re.I,
    )
    m = pat.search(html)
    if m:
        return unescape(m.group(1).strip())
    pat2 = re.compile(
        rf'<meta[^>]+content\s*=\s*["\']([^"\']+)["\'][^>]+(?:property|name)\s*=\s*["\']{re.escape(prop_or_name)}["\']',
        re.I,
    )
    m2 = pat2.search(html)
    if m2:
        return unescape(m2.group(1).strip())
    return None


def _title_tag(html: str) -> str | None:
    m = re.search(r"<title[^>]*>([^<]+)</title>", html, re.I)
    if m:
        return unescape(re.sub(r"\s+", " ", m.group(1)).strip())
    return None


def _price_guess(html: str) -> str | None:
    m = re.search(
        r"[\$€£]\s?\d+(?:[.,]\d{2})?|\d+(?:[.,]\d{2})?\s?(?:USD|EUR|GBP|AED)",
        html,
    )
    if m:
        return m.group(0).strip()
    return None


def extract_product_data(url: str, timeout: float = 15.0) -> dict[str, Any]:
    """
    Fetch HTML and extract title, best-effort price, and image URLs (og:image + img src).
    Returns a dict safe to log (no secrets).
    """
    raw = (url or "").strip()
    out: dict[str, Any] = {
        "url": raw,
        "title": "",
        "price": "",
        "images": [],
        "ok": False,
        "error": "",
    }
    if not raw:
        out["error"] = "empty_url"
        return out
    p = urlparse(raw)
    if p.scheme not in ("http", "https") or not p.netloc:
        out["error"] = "invalid_url"
        return out
    if requests is None:
        out["error"] = "requests_not_installed"
        return out
    try:
        r = requests.get(
            raw,
            timeout=timeout,
            headers={
                "User-Agent": "KLIP-AVATAR-ProductBot/1.0 (+https://klipaura.com)",
                "Accept": "text/html,application/xhtml+xml",
            },
        )
        r.raise_for_status()
        html = r.text or ""
    except Exception as e:
        out["error"] = str(e)[:300]
        return out

    title = _meta_content(html, "og:title") or _meta_content(html, "twitter:title") or _title_tag(html) or ""
    og_img = _meta_content(html, "og:image") or _meta_content(html, "twitter:image")
    images: list[str] = []
    if og_img:
        images.append(og_img)
    for m in re.finditer(r'<img[^>]+src=["\']([^"\']+)["\']', html, re.I):
        u = m.group(1).strip()
        if u.startswith("//"):
            u = p.scheme + ":" + u
        if u.startswith("http") and u not in images:
            images.append(u)
        if len(images) >= 8:
            break

    price = _price_guess(html) or ""

    out["title"] = title[:500]
    out["price"] = price[:80]
    out["images"] = images[:8]
    out["ok"] = bool(title or images)
    return out
