from __future__ import annotations

import os
from typing import Any

import requests


def search_images(query: str, *, limit: int = 6) -> list[str]:
    q = (query or "").strip()
    if not q:
        return []

    serper_key = (os.getenv("SERPER_API_KEY") or "").strip()
    if serper_key:
        try:
            r = requests.post(
                "https://google.serper.dev/images",
                headers={"X-API-KEY": serper_key, "Content-Type": "application/json"},
                json={"q": q, "num": max(1, min(20, int(limit)))},
                timeout=25,
            )
            if r.status_code != 200:
                return []
            data = r.json() if r.content else {}
            out: list[str] = []
            for it in (data.get("images") or []):
                if not isinstance(it, dict):
                    continue
                u = (it.get("imageUrl") or it.get("thumbnailUrl") or it.get("link") or "").strip()
                if u and u.startswith("http") and u not in out:
                    out.append(u)
                if len(out) >= limit:
                    break
            return out
        except Exception:
            return []

    serpapi_key = (os.getenv("SERPAPI_API_KEY") or "").strip()
    if serpapi_key:
        try:
            params: dict[str, Any] = {
                "engine": "google_images",
                "q": q,
                "api_key": serpapi_key,
                "ijn": "0",
                "num": str(max(1, min(100, int(limit)))),
            }
            r = requests.get("https://serpapi.com/search", params=params, timeout=25)
            if r.status_code != 200:
                return []
            data = r.json() if r.content else {}
            out: list[str] = []
            for it in (data.get("images_results") or []):
                if not isinstance(it, dict):
                    continue
                u = (it.get("original") or it.get("thumbnail") or "").strip()
                if u and u.startswith("http") and u not in out:
                    out.append(u)
                if len(out) >= limit:
                    break
            return out
        except Exception:
            return []

    return []
