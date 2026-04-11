"""
Product URL ingestion: fetch HTML and extract title, images, bullets.

Used by the URL → UGC video pipeline (outside core_v1).
"""

from __future__ import annotations

import os
import re
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

REJECTED_PATTERNS = [
    r"\d+\.\d+(mm|cm|inch|in)",
    r"dimension|specification|size chart",
    # Temu product alts often include discount/marketing copy; do not blanket-reject.
    r"(easy|capturing|fun)(?=\s|$|[,.\]])",
    r"^\d+\s*x\s*\d+",
]

# Temu / marketplace CDNs — allow real product files unless clearly spec-chart content.
_KWCDN_MARK = "kwcdn.com"


def _first_img_url(img, base: str) -> str:
    """Temu and similar pages often lazy-load via data-* instead of src."""
    for key in (
        "src",
        "data-src",
        "data-lazy",
        "data-lazy-src",
        "data-original",
        "data-zoom",
    ):
        raw = (img.get(key) or "").strip()
        if raw and raw not in ("undefined", "null", "about:blank"):
            return raw
    srcset = (img.get("srcset") or "").strip()
    if srcset:
        # "url 1x, url2 2x" → take first URL
        part = srcset.split(",")[0].strip().split()[0]
        if part.startswith("http") or part.startswith("/"):
            return part
    return ""


def _normalize_img_url(src: str, base: str) -> str | None:
    src = (src or "").strip()
    if not src:
        return None
    if src.startswith("//"):
        src = "https:" + src
    elif src.startswith("/"):
        src = urljoin(base, src)
    elif not src.startswith("http"):
        src = urljoin(base, src)
    if "http" not in src:
        return None
    return src


def _kwcdn_urls_from_html(html: str) -> list[str]:
    """Fallback when <img src> is empty but gallery URLs exist in HTML/JSON."""
    out: list[str] = []
    seen: set[str] = set()
    for m in re.finditer(
        r"https://img\.kwcdn\.com/[a-zA-Z0-9_\-./%]+\.(?:jpg|jpeg|png|webp)(?:\?[^\s\"'<>]*)?",
        html,
        re.I,
    ):
        u = m.group(0)
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def extract_product_data(url: str, timeout: float = 20.0) -> dict[str, Any]:
    """
    Fetch product page and parse images. Temu often returns a bot-challenge shell to
    plain HTTP clients — no <img> in HTML. In that case set UGC_PRODUCT_IMAGE_URLS
    in env to comma-separated https image URLs copied from an open browser tab.
    """
    override = (os.environ.get("UGC_PRODUCT_IMAGE_URLS") or "").strip()
    if override:
        urls = [u.strip() for u in override.split(",") if u.strip()]
        if not urls:
            raise RuntimeError("INVALID_PRODUCT_IMAGES")
        title = ((os.environ.get("UGC_PRODUCT_TITLE") or "").strip() or "Product")[:500]
        raw_bullets = (os.environ.get("UGC_PRODUCT_BULLETS") or "").strip()
        bullets: list[str] = []
        if raw_bullets:
            if "\n" in raw_bullets:
                bullets = [b.strip() for b in raw_bullets.splitlines() if b.strip()]
            else:
                bullets = [b.strip() for b in raw_bullets.split(",") if b.strip()]
        out = {
            "title": title,
            "images": [{"url": u, "alt": ""} for u in urls],
            "bullets": bullets[:10],
            "url": url,
        }
        if (os.environ.get("UGC_EXTRACT_DEBUG") or "").strip().lower() in ("1", "true", "yes"):
            print(f"DEBUG extract: UGC_PRODUCT_IMAGE_URLS override count={len(urls)}", flush=True)
        return out

    res = requests.get(
        url,
        timeout=timeout,
        headers={**_DEFAULT_HEADERS, "Referer": f"https://{urlparse(url).netloc}/"},
    )
    res.raise_for_status()
    html = res.text
    soup = BeautifulSoup(html, "html.parser")

    title = ""
    if soup.title:
        title = soup.title.get_text(strip=True) or ""

    images: list[dict[str, str]] = []
    seen: set[str] = set()
    base = url
    for img in soup.find_all("img"):
        raw = _first_img_url(img, base)
        src = _normalize_img_url(raw, base)
        if not src:
            continue
        if src in seen:
            continue
        seen.add(src)
        images.append({"url": src, "alt": (img.get("alt") or "")})

    # Temu often embeds gallery URLs in JSON/HTML while <img> uses placeholders — merge CDN hits.
    for u in _kwcdn_urls_from_html(html):
        if u in seen:
            continue
        seen.add(u)
        images.append({"url": u, "alt": ""})

    if len(html) < 5000 and not images:
        print(
            "WARN: Product HTML is tiny or has no parseable images. "
            "Temu often serves a bot challenge to scripts. "
            "Set UGC_PRODUCT_IMAGE_URLS (comma-separated img.kwcdn.com URLs from your browser).",
            flush=True,
        )

    bullets: list[str] = []
    for li in soup.find_all("li"):
        text = li.get_text(strip=True)
        if len(text) > 20:
            bullets.append(text)

    out = {
        "title": title,
        "images": images,
        "bullets": bullets[:10],
        "url": url,
    }
    if (os.environ.get("UGC_EXTRACT_DEBUG") or "").strip().lower() in ("1", "true", "yes"):
        print(f"DEBUG extract: raw image count={len(images)}", flush=True)
        for i, im in enumerate(images[:8]):
            print(f"  [{i}] {im.get('url', '')[:120]!s}", flush=True)
    return out


def is_valid_product_image(image: dict[str, Any]) -> bool:
    url = (image.get("url") or "").lower()
    alt = (image.get("alt") or "").lower()
    text = alt + " " + url
    # Primary Temu product CDN: trust image assets unless clearly spec-chart.
    if _KWCDN_MARK in url and any(ext in url for ext in (".jpg", ".jpeg", ".png", ".webp")):
        if re.search(r"size chart|specification sheet|dimension diagram", text, re.I):
            return False
        return True
    for pattern in REJECTED_PATTERNS:
        if re.search(pattern, text, re.I | re.M):
            return False
    return True


def filter_images(images: list[dict[str, Any]]) -> list[dict[str, Any]]:
    valid = [img for img in images if is_valid_product_image(img)]
    if (os.environ.get("UGC_EXTRACT_DEBUG") or "").strip().lower() in ("1", "true", "yes"):
        print(f"DEBUG filter: in={len(images)} out={len(valid)}", flush=True)
    if not valid:
        raise RuntimeError("INVALID_PRODUCT_IMAGES")
    return valid


def validate_http_product_url(url: str) -> None:
    p = urlparse((url or "").strip())
    if p.scheme not in ("http", "https") or not p.netloc:
        raise ValueError("invalid product URL")
