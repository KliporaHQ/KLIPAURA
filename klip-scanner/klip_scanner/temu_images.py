from __future__ import annotations

import re
import urllib.request

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"


def follow_redirects(url: str, timeout: int = 25) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.geturl()


def _goods_detail_html(final_url: str, timeout: int = 50) -> str:
    req = urllib.request.Request(final_url, headers={"User-Agent": UA, "Accept": "text/html,*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        html = r.read().decode("utf-8", "replace")

    if len(html) >= 80000 and len(re.findall(r"https://img\\.kwcdn\\.com", html)) >= 3:
        return html

    m = re.search(r"goods_id=(\\d+)", final_url)
    if not m:
        m2 = re.search(r"goods_id%3D(\\d+)", final_url)
        m = m2
    if not m:
        return html

    gid = m.group(1)
    full = (
        f"https://www.temu.com/kuiper/uk1.html?subj=goods-detail&goods_id={gid}&_p_rfs=1"
        "&g_lg=en&g_region=209"
    )
    req2 = urllib.request.Request(full, headers={"User-Agent": UA, "Accept": "text/html,*/*"})
    with urllib.request.urlopen(req2, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def extract_title_and_images(url: str, *, limit: int = 8) -> tuple[str, list[str]]:
    final_url = follow_redirects(url)
    html = _goods_detail_html(final_url)

    title = ""
    title_m = re.search(r'<meta\\s+property="og:title"\\s+content="([^"]+)"', html)
    if title_m:
        title = (title_m.group(1) or "").replace("&amp;", "&").strip()

    raw = re.findall(r"https://img\\.kwcdn\\.com[^\"'\\\\\s>]+", html)
    cleaned: list[str] = []
    for u in raw:
        u = u.split("&#x27;")[0].rstrip("\\").strip()
        if not u or u in cleaned:
            continue
        cleaned.append(u)
        if len(cleaned) >= max(1, int(limit)):
            break

    return title, cleaned
