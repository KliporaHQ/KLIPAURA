"""Optional Telegram alerts (spawn milestone, ops). Set TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID.

All notify_* helpers wrap send_telegram() and return None.
Telegram failure never affects pipeline — all exceptions are silently swallowed.
"""

from __future__ import annotations

import json
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


# ── Worker lifecycle ──────────────────────────────────────────────────────────

def notify_worker_online(worker_id: str) -> None:
    send_telegram(f"✅ KLIPAURA Worker online\nID: {worker_id}")


def notify_worker_offline(worker_id: str) -> None:
    send_telegram(f"🔴 KLIPAURA Worker offline\nID: {worker_id}")


# ── Job lifecycle ─────────────────────────────────────────────────────────────

def notify_job_complete(job_id: str, r2_url: str) -> None:
    msg = f"🎬 Job complete\nJob: {job_id}"
    if r2_url:
        msg += f"\nVideo: {r2_url}"
    send_telegram(msg)


def notify_job_failed(job_id: str, reason: str) -> None:
    send_telegram(f"❌ Job failed\nJob: {job_id}\nReason: {reason[:200]}")


def notify_stale_job(job_id: str, worker_id: str, minutes: int) -> None:
    send_telegram(f"⚠️ Stale job detected\nJob: {job_id}\nWorker: {worker_id}\nElapsed: {minutes}m")


# ── Queue / system alerts ─────────────────────────────────────────────────────

def notify_dlq_alert(count: int, sample_job_id: str) -> None:
    send_telegram(f"🪦 DLQ alert\n{count} dead-letter jobs\nSample: {sample_job_id}")


def notify_no_active_avatars() -> None:
    send_telegram("⚠️ KLIPAURA: NO_ACTIVE_AVATARS\nNo active avatars — worker is idle.\nActivate an avatar via /api/v1/avatars/{id}/resume")


def notify_redis_down(worker_id: str) -> None:
    send_telegram(f"🔴 Redis unreachable\nWorker: {worker_id}\nCheck REDIS_URL / UPSTASH credentials")


# ── Telegram bot command polling ──────────────────────────────────────────────

def get_updates(offset: int = 0, timeout: int = 0) -> list[dict]:
    """Long-poll Telegram getUpdates. Returns list of update objects."""
    token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    if not token:
        return []
    url = (
        f"https://api.telegram.org/bot{urllib.parse.quote(token, safe='')}/getUpdates"
        f"?offset={offset}&timeout={timeout}&allowed_updates=message"
    )
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=max(timeout + 5, 10)) as resp:
            data = json.loads(resp.read())
            return data.get("result") or []
    except Exception:
        return []


def poll_telegram_commands(redis_client, interval_sec: int = 5) -> None:
    """Background thread: poll Telegram for operator commands and dispatch.

    Recognised commands:
        /status   — worker status
        /queue    — pending queue depth
        /pause    — global pause
        /resume   — global resume
        /approve {job_id}
        /reject {job_id}
        /avatars  — list active avatars
        /flush    — NOT implemented (require explicit confirmation)
    """
    import time
    import threading

    from infrastructure.queue_names import (
        JOBS_PENDING, HITL_PENDING, DLQ,
        QUEUE_GLOBAL_PAUSED_KEY,
    )

    offset = 0
    chat_id = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()

    def _reply(text: str) -> None:
        send_telegram(text)

    def _loop() -> None:
        nonlocal offset
        while True:
            updates = get_updates(offset=offset)
            for update in updates:
                offset = update["update_id"] + 1
                msg = update.get("message") or {}
                text = (msg.get("text") or "").strip()
                if not text.startswith("/"):
                    continue
                parts = text.split()
                cmd = parts[0].lower().split("@")[0]
                try:
                    _dispatch(cmd, parts[1:])
                except Exception as exc:
                    _reply(f"Error handling {cmd}: {exc}")
            time.sleep(interval_sec)

    def _dispatch(cmd: str, args: list[str]) -> None:
        if cmd == "/status":
            try:
                depth = redis_client.llen(JOBS_PENDING)
                hitl = redis_client.llen(HITL_PENDING)
                dlq = redis_client.llen(DLQ)
                paused = bool((redis_client.get(QUEUE_GLOBAL_PAUSED_KEY) or "").strip())
                _reply(
                    f"📊 KLIPAURA Status\n"
                    f"Queue: {depth} pending\n"
                    f"HITL: {hitl} awaiting approval\n"
                    f"DLQ: {dlq} dead-letter\n"
                    f"Paused: {'YES' if paused else 'no'}"
                )
            except Exception as exc:
                _reply(f"Status error: {exc}")

        elif cmd == "/queue":
            try:
                depth = redis_client.llen(JOBS_PENDING)
                _reply(f"📋 Queue depth: {depth}")
            except Exception as exc:
                _reply(f"Queue error: {exc}")

        elif cmd == "/pause":
            try:
                redis_client.set(QUEUE_GLOBAL_PAUSED_KEY, "1")
                _reply("⏸ Queue paused globally")
            except Exception as exc:
                _reply(f"Pause error: {exc}")

        elif cmd == "/resume":
            try:
                redis_client.delete(QUEUE_GLOBAL_PAUSED_KEY)
                _reply("▶️ Queue resumed")
            except Exception as exc:
                _reply(f"Resume error: {exc}")

        elif cmd == "/avatars":
            try:
                from infrastructure.avatar_loader import AvatarLoader
                active = AvatarLoader().list_active()
                names = ", ".join(a["avatar_id"] for a in active) or "none"
                _reply(f"🎭 Active avatars: {names}")
            except Exception as exc:
                _reply(f"Avatars error: {exc}")

        elif cmd == "/approve" and args:
            job_id = args[0].strip()
            try:
                from infrastructure.job_state import update_manifest, read_manifest
                m = read_manifest(job_id)
                if not m:
                    _reply(f"Job {job_id} not found")
                    return
                update_manifest(job_id, status="APPROVED")
                _reply(f"✅ Approved: {job_id}")
            except Exception as exc:
                _reply(f"Approve error: {exc}")

        elif cmd == "/reject" and args:
            job_id = args[0].strip()
            try:
                from infrastructure.job_state import update_manifest, read_manifest
                m = read_manifest(job_id)
                if not m:
                    _reply(f"Job {job_id} not found")
                    return
                update_manifest(job_id, status="REJECTED")
                _reply(f"🚫 Rejected: {job_id}")
            except Exception as exc:
                _reply(f"Reject error: {exc}")

        else:
            _reply(
                "📖 Commands:\n"
                "/status — system status\n"
                "/queue — queue depth\n"
                "/pause — global pause\n"
                "/resume — resume\n"
                "/approve {job_id}\n"
                "/reject {job_id}\n"
                "/avatars — list active"
            )

    t = threading.Thread(target=_loop, name="telegram-cmd-poll", daemon=True)
    t.start()
    return t
