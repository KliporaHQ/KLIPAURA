"""Optional Telegram alerts (spawn milestone, ops). Set TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID."""

from __future__ import annotations

import os
import urllib.error
import urllib.parse
import urllib.request


def send_telegram(text: str) -> bool:
    token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    chat = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()
    if not token or not chat:
        return False
    url = f"https://api.telegram.org/bot{urllib.parse.quote(token, safe='')}/sendMessage"
    body = urllib.parse.urlencode(
        {"chat_id": chat, "text": text[:4000], "disable_web_page_preview": "true"}
    ).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST", headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return 200 <= resp.status < 300
    except (OSError, urllib.error.HTTPError, urllib.error.URLError):
        return False
