"""
WaveSpeed talking-head lip-sync (minimal port of klip-avatar ``wavespeed_video.generate_lipsync_video_to_path``).

Uses urllib + multipart upload — no dependency on klip-avatar path bootstrap.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Optional, Tuple


BASE = "https://api.wavespeed.ai/api/v3"


def _wavespeed_api_ok(code: object) -> bool:
    if code is None:
        return False
    if code == 200:
        return True
    if isinstance(code, str) and code.strip() == "200":
        return True
    try:
        return int(code) == 200
    except (TypeError, ValueError):
        return False


def _http_timeout_sec() -> int:
    try:
        return max(15, min(600, int(os.environ.get("WAVESPEED_HTTP_TIMEOUT_SEC", "120"))))
    except ValueError:
        return 120


def upload_file_detailed(file_path: str, api_key: str) -> Tuple[Optional[str], Optional[str]]:
    """POST ``/media/upload/binary``. Returns (url, error)."""
    with open(file_path, "rb") as f:
        data = f.read()
    boundary = "----FormBoundary" + str(int(time.time()))
    raw_body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{os.path.basename(file_path)}"\r\n'
        "Content-Type: application/octet-stream\r\n\r\n"
    ).encode() + data + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        f"{BASE}/media/upload/binary",
        data=raw_body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        try:
            err_body = e.read().decode("utf-8", errors="replace")[:500]
        except Exception:
            err_body = ""
        return None, f"HTTP {e.code} {e.reason or ''}: {err_body or str(e)}".strip()
    except Exception as e:
        return None, f"{type(e).__name__}: {str(e)[:400]}"
    try:
        out = json.loads(raw)
    except ValueError:
        return None, f"Non-JSON upload response: {raw[:300]}"
    data_o = out.get("data") or {}
    hosted = data_o.get("url") or data_o.get("download_url")
    if _wavespeed_api_ok(out.get("code")) and hosted:
        return str(hosted).strip(), None
    msg = out.get("message") or out.get("msg") or raw[:400]
    return None, f"code={out.get('code')}: {msg}"


def generate_lipsync_video_to_path(
    image_url: str,
    audio_path: str,
    api_key: str,
    output_path: str,
    *,
    lipsync_model: Optional[str] = None,
    max_wait: int = 720,
) -> Tuple[Optional[str], Optional[str]]:
    """
    Portrait image URL + local audio file → downloaded MP4 path.
    Returns (output_path, None) on success, else (None, error_hint).
    """
    if not (image_url or "").strip():
        return None, "missing image_url"
    if not audio_path or not os.path.isfile(audio_path):
        return None, "missing audio file"

    ws_key = (api_key or "").strip()
    if not ws_key:
        return None, "WaveSpeed API key not configured"

    audio_url, up_err = upload_file_detailed(audio_path, ws_key)
    if not audio_url:
        return None, up_err or "audio upload failed"

    raw_model = (
        lipsync_model or os.environ.get("WAVESPEED_LIPSYNC_MODEL") or "wavespeed-ai/ltx-2.3/lipsync"
    ).strip().lstrip("/")
    model_path = raw_model if raw_model.startswith("wavespeed-ai/") else f"wavespeed-ai/{raw_model}"
    url = f"{BASE}/{model_path}"
    bodies: list[dict] = [
        {"image": image_url, "audio": audio_url},
        {"image_url": image_url, "audio_url": audio_url},
    ]
    last_submit_err = ""
    _lip_to = _http_timeout_sec()
    for body in bodies:
        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(body).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {ws_key}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=_lip_to) as resp:
                raw_submit = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            try:
                err_body = e.read().decode("utf-8", errors="replace")[:500]
            except Exception:
                err_body = ""
            last_submit_err = f"HTTP {e.code}: {err_body or e.reason}"
            continue
        except Exception as e:
            last_submit_err = f"{type(e).__name__}: {str(e)[:400]}"
            continue
        try:
            out = json.loads(raw_submit)
        except ValueError:
            last_submit_err = f"non-JSON: {raw_submit[:300]}"
            continue
        if not _wavespeed_api_ok(out.get("code")):
            last_submit_err = f"code={out.get('code')}: {out.get('message') or raw_submit[:400]}"
            continue
        data = out.get("data") or {}
        task_id = data.get("id")
        get_url = (data.get("urls") or {}).get("get") or f"{BASE}/predictions/{task_id}"
        if not task_id:
            last_submit_err = "missing task id"
            continue
        last_status = ""
        for _ in range(max(1, max_wait // 3)):
            time.sleep(3)
            try:
                req_g = urllib.request.Request(
                    get_url, headers={"Authorization": f"Bearer {ws_key}"}, method="GET"
                )
                with urllib.request.urlopen(req_g, timeout=_lip_to) as resp:
                    result = json.loads(resp.read().decode("utf-8", errors="replace"))
            except Exception:
                continue
            if not _wavespeed_api_ok(result.get("code")):
                continue
            rdata = result.get("data") or {}
            status = (str(rdata.get("status") or "")).lower()
            last_status = status
            if status in ("completed", "succeeded", "complete", "success"):
                outputs = rdata.get("outputs") or []
                if not outputs:
                    return None, "lip-sync finished but no video output was returned"
                out0 = outputs[0]
                clip_url = (
                    out0 if isinstance(out0, str) else (out0.get("url") if isinstance(out0, dict) else str(out0))
                )
                cu = str(clip_url).strip() or None
                if not cu:
                    return None, "empty output url"
                try:
                    with urllib.request.urlopen(
                        urllib.request.Request(cu, method="GET"),
                        timeout=_http_timeout_sec(),
                    ) as resp:
                        clip_data = resp.read()
                    if clip_data and len(clip_data) > 1000:
                        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
                        with open(output_path, "wb") as f:
                            f.write(clip_data)
                        return output_path, None
                except Exception as e:
                    return None, f"download failed: {e}"
                return None, "download empty"
            if status == "failed":
                err = rdata.get("error") or rdata.get("message") or str(rdata)[:400]
                return None, f"lip-sync task failed: {err}"
        return None, f"lip-sync timed out (last status: {last_status})"
    return None, last_submit_err or "lip-sync request failed"
