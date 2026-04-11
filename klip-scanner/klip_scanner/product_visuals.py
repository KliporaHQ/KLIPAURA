from __future__ import annotations

from typing import Any

from klip_scanner.google_images import search_images
from klip_scanner.temu_images import extract_title_and_images


def resolve_product_images(url: str, title: str | None = None, *, limit: int = 8) -> tuple[str, list[str], dict[str, Any]]:
    u = (url or "").strip()
    t = (title or "").strip()
    meta: dict[str, Any] = {"source": None}
    if not u:
        return t, [], meta

    low = u.lower()
    is_temu = ("temu.com" in low) or ("temu.to" in low)
    if not is_temu:
        return t, [], meta

    img_urls: list[str] = []

    try:
        t2, imgs = extract_title_and_images(u, limit=limit)
        if t2 and not t:
            t = t2
        if imgs:
            img_urls = imgs
            meta["source"] = "temu_scrape"
    except Exception:
        img_urls = []

    if not img_urls and t:
        imgs = search_images(f"{t} white background", limit=min(6, max(1, int(limit))))
        if imgs:
            img_urls = imgs
            meta["source"] = "google_images"

    return t, img_urls, meta
