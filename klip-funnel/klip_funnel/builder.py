"""LLM-assisted landing HTML from product payload (Groq optional)."""

from __future__ import annotations

import html
import os
import re
from pathlib import Path
from typing import Any

_TEMPLATES = Path(__file__).resolve().parent / "templates"

_DEFAULT_AFFILIATE_DISCLOSURE = (
    "Disclosure: This page may contain affiliate links. "
    "If you make a purchase through our link, we may earn a commission at no extra cost to you."
)


def _safe_video_url(raw: str | None) -> str | None:
    u = (raw or "").strip()
    if not u.startswith(("https://", "http://")):
        return None
    return u


def _video_section_html(video_url: str | None) -> str:
    u = _safe_video_url(video_url)
    if not u:
        return ""
    esc = html.escape(u, quote=True)
    return (
        f'<section class="video-wrap" aria-label="Product video">'
        f'<video controls playsinline preload="metadata" '
        f'style="width:100%;border-radius:12px;background:#000;max-height:min(70vh,640px)">'
        f'<source src="{esc}" type="video/mp4" />'
        f"Your browser does not support embedded video."
        f"</video></section>"
    )


def _persona_brand_html(job_payload: dict[str, Any]) -> str:
    name = str(
        job_payload.get("persona_display_name")
        or job_payload.get("avatar_display_name")
        or job_payload.get("avatar_id")
        or ""
    ).strip()[:120]
    img = str(job_payload.get("persona_image_url") or job_payload.get("persona_image") or "").strip()
    if not name and not img:
        return ""
    parts: list[str] = ['<div class="persona-brand">']
    if img and img.startswith(("https://", "http://")):
        ie = html.escape(img, quote=True)
        parts.append(f'<img src="{ie}" alt="" class="persona-avatar" loading="lazy" decoding="async" />')
    if name:
        ne = html.escape(name, quote=True)
        parts.append(f'<span class="persona-name">{ne}</span>')
    parts.append("</div>")
    return "".join(parts)


def _resolve_affiliate_disclosure(job_payload: dict[str, Any]) -> str:
    custom = str(job_payload.get("affiliate_disclosure") or "").strip()
    if custom:
        return custom[:800]
    env = (os.getenv("AFFILIATE_DISCLOSURE_DEFAULT") or "").strip()
    return env[:800] if env else _DEFAULT_AFFILIATE_DISCLOSURE


def _slug(s: str) -> str:
    x = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return x[:48] or "page"


def _template_html(niche: str) -> str:
    niche = (niche or "base").lower()
    for name in (f"{niche}.html", "base.html"):
        p = _TEMPLATES / name
        if p.is_file():
            return p.read_text(encoding="utf-8")
    return (_TEMPLATES / "base.html").read_text(encoding="utf-8")


def _groq_copy(product: dict[str, Any]) -> tuple[str, str]:
    key = (os.getenv("GROQ_API_KEY") or "").strip()
    title = str(product.get("title") or product.get("product_title") or "Offer")
    if not key:
        body = str(product.get("description") or product.get("bullets") or "")[:800]
        if not body:
            body = f"Limited-time deal on {title}. Tap below to shop."
        return f"Why {title}?", body
    try:
        import json
        import urllib.request

        prompt = (
            f"Write a short landing: headline (max 12 words) and body (2 sentences) for: {title}. "
            "JSON only: {{\"headline\":\"...\",\"body\":\"...\"}}"
        )
        req = urllib.request.Request(
            "https://api.groq.com/openai/v1/chat/completions",
            data=json.dumps(
                {
                    "model": os.getenv("GROQ_FUNNEL_MODEL", "llama-3.1-8b-instant"),
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.5,
                }
            ).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {key}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode())
        txt = data["choices"][0]["message"]["content"]
        m = re.search(r"\{[\s\S]*\}", txt)
        if not m:
            raise ValueError("no json")
        obj = json.loads(m.group())
        return str(obj.get("headline") or title), str(obj.get("body") or "")
    except Exception:
        return f"Shop {title}", f"See why creators love {title} — link below."


def build_landing_page(product: dict[str, Any], *, niche: str = "base") -> dict[str, Any]:
    """
    Returns ``{ "html": str, "slug": str, "niche": str }``.
    ``product`` should include title, affiliate_url (or product_url), optional category for template pick.
    """
    cat = str(product.get("category") or product.get("layout_hint") or "").lower()
    if any(x in cat for x in ("beauty", "skin", "glow")):
        niche = "beauty"
    elif any(x in cat for x in ("tech", "gadget", "phone")):
        niche = "tech"
    headline, body = _groq_copy(product)
    title = str(product.get("title") or product.get("product_title") or "Offer")
    cta_url = str(product.get("affiliate_url") or product.get("product_url") or "#")
    cta_label = str(product.get("cta_label") or "Get it here")
    tracking = str(product.get("tracking_id") or "utm_source=klipaura")
    tpl = _template_html(niche)
    html = (
        tpl.replace("__TITLE__", title)
        .replace("__HEADLINE__", headline)
        .replace("__BODY__", body)
        .replace("__CTA_URL__", cta_url)
        .replace("__CTA_LABEL__", cta_label)
        .replace("__TRACKING__", tracking)
    )
    slug = _slug(title)
    return {"html": html, "slug": slug, "niche": niche, "title": title}


def _benefits_list_html(product: dict[str, Any]) -> str:
    raw = product.get("bullets") or product.get("product_bullets")
    items: list[str] = []
    if isinstance(raw, list):
        items = [str(x).strip() for x in raw[:8] if str(x).strip()]
    elif isinstance(raw, str):
        items = [b.strip() for b in raw.replace("\n", ",").split(",") if b.strip()][:8]
    if not items:
        items = ["Curated deal", "Mobile-friendly checkout", "Link verified for this offer"]
    return "".join(f"<li>{html.escape(t, quote=True)}</li>" for t in items)


def build_mobile_funnel_page(job_payload: dict[str, Any]) -> dict[str, Any]:
    """
    Mobile-first landing: hero image, Groq headline/body, bullets, CTA with tagged affiliate link.
    ``job_payload`` may include product_title, product_url, product_bullets, product_image_urls,
    affiliate_data.affiliate_link, avatar_id / avatar_display_name.
    """
    title = str(
        job_payload.get("product_title")
        or job_payload.get("title")
        or "Offer"
    )[:200]
    ad = job_payload.get("affiliate_data") if isinstance(job_payload.get("affiliate_data"), dict) else {}
    aff_link = str(ad.get("affiliate_link") or "").strip()
    product_url = str(job_payload.get("product_url") or "").strip()
    cta_url = aff_link or product_url or "#"
    imgs = job_payload.get("product_image_urls")
    hero = ""
    if isinstance(imgs, list) and imgs:
        hero = str(imgs[0]).strip()
    elif isinstance(imgs, str) and imgs.strip():
        hero = imgs.split(",")[0].strip()
    if not hero:
        hero = "https://placehold.co/800x500/1e293b/94a3b8?text=KLIPAURA"

    product: dict[str, Any] = {
        "title": title,
        "product_title": title,
        "product_url": product_url,
        "affiliate_url": cta_url,
        "description": job_payload.get("product_bullets") or "",
        "bullets": job_payload.get("product_bullets"),
        "category": str(job_payload.get("category") or ""),
    }
    headline, body = _groq_copy(product)
    cta_label = str(job_payload.get("cta_label") or "Get it now — best price")
    tracking = str(job_payload.get("tracking_id") or "utm_source=klipaura&utm_medium=funnel")
    avatar_name = str(
        job_payload.get("persona_display_name")
        or job_payload.get("avatar_display_name")
        or job_payload.get("avatar_id")
        or "Creator"
    )[:120]

    video_src = (
        job_payload.get("public_video_url")
        or job_payload.get("r2_url")
        or job_payload.get("video_url")
    )
    video_section = _video_section_html(str(video_src) if video_src else None)
    persona_brand = _persona_brand_html(job_payload)
    disclosure = html.escape(_resolve_affiliate_disclosure(job_payload), quote=True)

    tpl_path = _TEMPLATES / "funnel_mobile.html"
    if tpl_path.is_file():
        tpl = tpl_path.read_text(encoding="utf-8")
    else:
        tpl = _template_html("base")

    benefits = _benefits_list_html(product)
    html_out = (
        tpl.replace("__TITLE__", html.escape(title, quote=True))
        .replace("__HEADLINE__", html.escape(headline, quote=True))
        .replace("__BODY__", html.escape(body, quote=True))
        .replace("__CTA_URL__", html.escape(cta_url, quote=True))
        .replace("__CTA_LABEL__", html.escape(cta_label, quote=True))
        .replace("__TRACKING__", html.escape(tracking, quote=True))
        .replace("__HERO_IMAGE__", hero)
        .replace("__BENEFITS__", benefits)
        .replace("__AVATAR_NAME__", html.escape(avatar_name, quote=True))
        .replace("__VIDEO_SECTION__", video_section)
        .replace("__PERSONA_BRAND__", persona_brand)
        .replace("__DISCLOSURE__", disclosure)
    )
    slug = _slug(title)
    return {"html": html_out, "slug": slug, "niche": "mobile", "title": title}
