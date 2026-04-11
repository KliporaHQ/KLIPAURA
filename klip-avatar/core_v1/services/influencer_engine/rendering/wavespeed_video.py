"""
Wavespeed image-to-video (I2V) for B-roll pipeline.

Upload avatar image, submit I2V tasks (default 5s @ 480p ultra-fast), poll until done, download clips.
Used by VideoRenderer to build continuous B-roll with consistent avatar.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from core.services.wavespeed_key import normalize_wavespeed_api_key_secret

BASE = "https://api.wavespeed.ai/api/v3"
# WAN 2.2 I2V: isolation + production use v2 (v3 submit can differ). Override with WAVESPEED_I2V_API_BASE.
I2V_API_BASE_DEFAULT = "https://api.wavespeed.ai/api/v2"
WAVESPEED_RETRY_QUEUE_KEY = "wavespeed:retry_queue"
WAVESPEED_CIRCUIT_KEY = "wavespeed:circuit:open"


def _wavespeed_http_timeout_sec() -> int:
    """urllib timeout for WaveSpeed submit, poll, and clip download (default 120s)."""
    try:
        return max(15, min(600, int(os.environ.get("WAVESPEED_HTTP_TIMEOUT_SEC", "120"))))
    except ValueError:
        return 120


def _ws_resilient_enabled() -> bool:
    """When true, 429 / caps trip a circuit breaker and park payloads to Redis ``wavespeed:retry_queue``."""
    return os.environ.get("WAVESPEED_RESILIENT_MODE", "").strip().lower() in ("1", "true", "yes", "on")


def wavespeed_circuit_is_open() -> bool:
    try:
        from klipaura_core.infrastructure.redis_client import get_redis_client

        r = get_redis_client()
        raw = r.get(WAVESPEED_CIRCUIT_KEY)
        if raw is None:
            return False
        s = raw if isinstance(raw, str) else (raw.decode("utf-8", errors="ignore") if hasattr(raw, "decode") else str(raw))
        return (s or "").strip().lower() in ("1", "true", "open", "yes")
    except Exception:
        return False


def wavespeed_trip_circuit(reason: str, ttl_sec: int = 300) -> None:
    if not _ws_resilient_enabled():
        return
    try:
        from klipaura_core.infrastructure.redis_client import get_redis_client

        r = get_redis_client()
        r.setex(WAVESPEED_CIRCUIT_KEY, "1", max(30, int(ttl_sec)))
    except Exception:
        pass


def park_wavespeed_retry_job(payload: Dict[str, Any], reason: str) -> None:
    """LPUSH JSON job for later reprocessing (manual or worker drain)."""
    if not _ws_resilient_enabled():
        return
    try:
        from klipaura_core.infrastructure.redis_client import get_redis_client

        r = get_redis_client()
        entry = {
            **payload,
            "reason": (reason or "")[:500],
            "queued_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        r.lpush(WAVESPEED_RETRY_QUEUE_KEY, json.dumps(entry))
        r.ltrim(WAVESPEED_RETRY_QUEUE_KEY, 0, 4999)
    except Exception:
        pass


def _wavespeed_api_ok(code: Any) -> bool:
    """WaveSpeed JSON envelopes use code 200; some clients stringify it as '200'."""
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


def _http_image_url(u: str) -> bool:
    s = (u or "").strip()
    return s.startswith("https://") or s.startswith("http://")
# Locked policy: Wan 2.2 I2V 480p Ultra Fast. API allows duration in {5, 8} seconds only.
I2V_MODEL = "wavespeed-ai/wan-2.2/i2v-480p-ultra-fast"
I2V_RESOLUTION = "480p"
WAVESPEED_WAN_I2V_ALLOWED_DURATIONS = (5, 8)
CLIP_DURATION_SEC = 5  # economical default; must be in WAVESPEED_WAN_I2V_ALLOWED_DURATIONS
NUM_CLIPS = 6  # total duration ≈ NUM_CLIPS * CLIP_DURATION_SEC (subject to model caps)


def clamp_wan_i2v_duration_sec(sec: int) -> int:
    """WAN 2.2 I2V rejects any duration not in {5, 8}."""
    try:
        s = int(sec)
    except (TypeError, ValueError):
        s = CLIP_DURATION_SEC
    if s in WAVESPEED_WAN_I2V_ALLOWED_DURATIONS:
        return s
    if s < 5:
        return 5
    if s > 8:
        return 8
    return 5 if s <= 6 else 8


def _wavespeed_shared_rate_limit_mode() -> bool:
    """
    Legacy: only WAVESPEED_MAX_CALLS_PER_HOUR is set (no per-kind split).
    One Redis counter for T2I + I2V + lipsync — easy to hit during mixed use.
    """
    legacy = os.environ.get("WAVESPEED_MAX_CALLS_PER_HOUR")
    if legacy is None or str(legacy).strip() == "":
        return False
    if os.environ.get("WAVESPEED_MAX_T2I_PER_HOUR") is not None:
        return False
    if os.environ.get("WAVESPEED_MAX_I2V_PER_HOUR") is not None:
        return False
    return True


def _wavespeed_shared_cap() -> int:
    try:
        return max(0, int(os.environ.get("WAVESPEED_MAX_CALLS_PER_HOUR", "5")))
    except ValueError:
        return 5


def _wavespeed_split_cap(kind: str) -> int:
    """0 = unlimited. kind is t2i | i2v | lipsync."""
    if kind == "t2i":
        try:
            v = os.environ.get("WAVESPEED_MAX_T2I_PER_HOUR")
            if v is None or str(v).strip() == "":
                return 50
            return max(0, int(v))
        except ValueError:
            return 50
    if kind == "lipsync":
        raw = os.environ.get("WAVESPEED_MAX_LIPSYNC_PER_HOUR")
        if raw is not None and str(raw).strip() != "":
            try:
                return max(0, int(raw))
            except ValueError:
                pass
        kind = "i2v"
    if kind == "i2v":
        try:
            v = os.environ.get("WAVESPEED_MAX_I2V_PER_HOUR")
            if v is None or str(v).strip() == "":
                return 5
            return max(0, int(v))
        except ValueError:
            return 5
    return 5


def _wavespeed_effective_cap(kind: str) -> int:
    if _wavespeed_shared_rate_limit_mode():
        return _wavespeed_shared_cap()
    return _wavespeed_split_cap(kind)


def _wavespeed_rate_limit_key(kind: str) -> str:
    bucket = time.strftime("%Y%m%d%H", time.gmtime())
    if _wavespeed_shared_rate_limit_mode():
        return f"wavespeed:api_calls:{bucket}"
    return f"wavespeed:api_calls:{kind}:{bucket}"


def _wavespeed_rate_limit_allows(kind: str) -> bool:
    cap = _wavespeed_effective_cap(kind)
    if cap <= 0:
        return True
    try:
        from klipaura_core.infrastructure.redis_client import get_redis_client

        r = get_redis_client()
        key = _wavespeed_rate_limit_key(kind)
        raw = r.get(key)
        cur = int(raw) if raw is not None and str(raw).isdigit() else 0
        if cur >= cap:
            return False
        n = r.incr(key)
        if n == 1:
            r.expire(key, 7200)
        return True
    except Exception:
        return True


def wavespeed_rate_limit_status() -> Dict[str, Any]:
    """Counts for Mission Control GET /wavespeed-key-status (no secrets)."""
    bucket = time.strftime("%Y%m%d%H", time.gmtime())
    shared = _wavespeed_shared_rate_limit_mode()
    out: Dict[str, Any] = {
        "utc_hour_bucket": bucket,
        "shared_legacy_mode": shared,
        "note": (
            "Default split mode: T2I 50/h, I2V 5/h, lipsync follows I2V unless WAVESPEED_MAX_LIPSYNC_PER_HOUR is set. "
            "If only WAVESPEED_MAX_CALLS_PER_HOUR is set (no per-kind vars), one shared cap applies to all."
        ),
    }
    try:
        from klipaura_core.infrastructure.redis_client import get_redis_client

        r = get_redis_client()
        if shared:
            key = f"wavespeed:api_calls:{bucket}"
            raw = r.get(key)
            used = int(raw) if raw is not None and str(raw).isdigit() else 0
            cap = _wavespeed_shared_cap()
            out["shared"] = {"used": used, "cap": cap, "redis_key": key}
        else:
            for k in ("t2i", "i2v", "lipsync"):
                key = f"wavespeed:api_calls:{k}:{bucket}"
                raw = r.get(key)
                used = int(raw) if raw is not None and str(raw).isdigit() else 0
                cap = _wavespeed_split_cap(k)
                out[k] = {"used": used, "cap": cap, "redis_key": key}
    except Exception as e:
        out["redis_error"] = f"{type(e).__name__}: {str(e)[:200]}"
    return out


def _upload_file_once_detailed(file_path: str, api_key: str) -> Tuple[Optional[str], Optional[str]]:
    """Single POST /upload. Returns (url, None) or (None, error_hint for logs/UI)."""
    with open(file_path, "rb") as f:
        data = f.read()
    boundary = "----FormBoundary" + str(int(time.time()))
    raw_body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{os.path.basename(file_path)}"\r\n'
        "Content-Type: application/octet-stream\r\n\r\n"
    ).encode() + data + f"\r\n--{boundary}--\r\n".encode()
    # Docs: POST .../api/v3/media/upload/binary — legacy /upload returns 404
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
    data = out.get("data") or {}
    hosted = data.get("url") or data.get("download_url")
    if _wavespeed_api_ok(out.get("code")) and hosted:
        return str(hosted).strip(), None
    msg = out.get("message") or out.get("msg") or raw[:400]
    return None, f"code={out.get('code')}: {msg}"


def upload_file_detailed(file_path: str, api_key: str) -> Tuple[Optional[str], Optional[str]]:
    """Upload to WaveSpeed; return (url, last_error). Retries up to 3 times."""
    if not file_path or not os.path.isfile(file_path):
        return None, "not a valid file path"
    last_err: Optional[str] = None
    for attempt in range(3):
        url, err = _upload_file_once_detailed(file_path, api_key)
        if url:
            return url, None
        last_err = err
        if attempt < 2:
            time.sleep(2)
    return None, last_err or "upload failed after retries"


def upload_file(file_path: str, api_key: str) -> Optional[str]:
    """Upload file to Wavespeed; return URL on success. Retries up to 3 times on failure."""
    url, _ = upload_file_detailed(file_path, api_key)
    return url


def _get_ffmpeg_exe() -> str:
    try:
        from .ffmpeg_path import get_ffmpeg_exe

        return get_ffmpeg_exe()
    except Exception:
        return (os.environ.get("FFMPEG_PATH") or "").strip() or "ffmpeg"


def _ffprobe_duration_sec(path: str, ffprobe_exe: str) -> float:
    if not path or not os.path.isfile(path):
        return 0.0
    exe = ffprobe_exe
    if not os.path.isfile(exe) and exe in ("ffprobe", "ffprobe.exe"):
        w = shutil.which("ffprobe")
        if w:
            exe = w
    try:
        r = subprocess.run(
            [exe, "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True,
            text=True,
            timeout=20,
        )
        if r.returncode == 0 and (r.stdout or "").strip():
            return float((r.stdout or "").strip())
    except Exception:
        pass
    return 0.0


def _ffprobe_path_for(ffmpeg_exe: str) -> str:
    base = os.path.dirname(ffmpeg_exe) if ffmpeg_exe and os.path.isfile(ffmpeg_exe) else ""
    if base:
        cand = os.path.join(base, "ffprobe.exe" if os.name == "nt" else "ffprobe")
        if os.path.isfile(cand):
            return cand
    w = shutil.which("ffprobe")
    return w or "ffprobe"


def extract_last_frame_png(
    video_path: str,
    out_png: str,
    *,
    ffmpeg_exe: Optional[str] = None,
) -> bool:
    """
    Lightweight FFmpeg extract: final frame of MP4 → PNG for I2V continuity.
    """
    exe = ffmpeg_exe or _get_ffmpeg_exe()
    ffprobe = _ffprobe_path_for(exe)
    dur = _ffprobe_duration_sec(video_path, ffprobe)
    if dur <= 0.05:
        return False
    t = max(0.0, dur - 0.05)
    try:
        os.makedirs(os.path.dirname(out_png) or ".", exist_ok=True)
        subprocess.run(
            [exe, "-y", "-ss", f"{t:.3f}", "-i", video_path, "-vframes", "1", "-q:v", "2", out_png],
            capture_output=True,
            timeout=90,
            check=True,
        )
        return os.path.isfile(out_png) and os.path.getsize(out_png) > 80
    except Exception:
        return False


def _i2v_frame_continuity_enabled() -> bool:
    v = (os.environ.get("KLIP_I2V_FRAME_CONTINUITY") or "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def _poll_prediction(get_url: str, api_key: str, max_wait: int = 180) -> Optional[Dict[str, Any]]:
    """Poll GET predictions/{id} until completed. Returns data dict or None."""
    for _ in range(max_wait // 3):
        time.sleep(3)
        try:
            req = urllib.request.Request(get_url, headers={"Authorization": f"Bearer {api_key}"}, method="GET")
            with urllib.request.urlopen(req, timeout=_wavespeed_http_timeout_sec()) as resp:
                result = json.loads(resp.read().decode())
        except Exception:
            continue
        if not _wavespeed_api_ok(result.get("code")):
            continue
        rdata = result.get("data") or {}
        status = (rdata.get("status") or "").lower()
        if status == "completed":
            return rdata
        if status == "failed":
            return None
    return None


def generate_i2v_clip(
    image_url: str,
    motion_prompt: str,
    api_key: str,
    output_path: str,
    duration_sec: int = CLIP_DURATION_SEC,
    *,
    i2v_model: Optional[str] = None,
    resolution: Optional[str] = None,
    job_id: Optional[str] = None,
) -> Optional[str]:
    """
    Submit one I2V task, poll until done, download to output_path.
    Retries submit+poll up to ``WAVESPEED_I2V_MAX_RETRIES`` (default 2 → 3 attempts).
    Returns output_path if successful else None.
    """
    duration_sec = clamp_wan_i2v_duration_sec(duration_sec)
    try:
        max_retries = max(0, min(5, int(os.environ.get("WAVESPEED_I2V_MAX_RETRIES", "2") or "2")))
    except ValueError:
        max_retries = 2
    last_err: Optional[str] = None
    for attempt in range(max_retries + 1):
        if attempt:
            delay = min(8.0, float(2 ** (attempt - 1)))
            print(f"I2V retry {attempt}/{max_retries} after: {last_err}; sleeping {delay:.1f}s", flush=True)
            time.sleep(delay)
        clip_url, err_d = generate_i2v_clip_url_detailed(
            image_url,
            motion_prompt,
            api_key,
            duration_sec=duration_sec,
            i2v_model=i2v_model,
            resolution=resolution,
            job_id=job_id,
        )
        last_err = err_d
        if not clip_url:
            continue
        try:
            to = _wavespeed_http_timeout_sec()
            with urllib.request.urlopen(urllib.request.Request(clip_url, method="GET"), timeout=to) as resp:
                clip_data = resp.read()
            if clip_data and len(clip_data) > 1000:
                os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
                with open(output_path, "wb") as f:
                    f.write(clip_data)
                return output_path
        except Exception as ex:
            last_err = f"download: {type(ex).__name__}: {ex}"
            print(f"I2V clip download failed: {last_err}", flush=True)
            continue
    if last_err:
        print(f"I2V clip failed after {max_retries + 1} attempt(s): {last_err}", flush=True)
    return None


def generate_i2v_clip_url_detailed(
    image_url: str,
    motion_prompt: str,
    api_key: str,
    duration_sec: int = CLIP_DURATION_SEC,
    *,
    i2v_model: Optional[str] = None,
    resolution: Optional[str] = None,
    max_wait: int = 240,
    job_id: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str]]:
    """
    Submit one I2V task, poll until completed. Returns (video_url_or_none, error_hint_or_none).
    """
    if wavespeed_circuit_is_open():
        park_wavespeed_retry_job({"kind": "i2v", "job_id": job_id, "image_url": (image_url or "")[:200]}, "circuit_open")
        return None, "WaveSpeed circuit breaker open; job parked in wavespeed:retry_queue"
    if not _wavespeed_rate_limit_allows("i2v"):
        park_wavespeed_retry_job({"kind": "i2v", "job_id": job_id, "image_url": (image_url or "")[:200]}, "hourly_cap")
        wavespeed_trip_circuit("i2v_hourly_cap", ttl_sec=180)
        return None, (
            "WaveSpeed I2V hourly cap reached (WAVESPEED_MAX_I2V_PER_HOUR, or legacy shared WAVESPEED_MAX_CALLS_PER_HOUR). "
            "Set WAVESPEED_MAX_I2V_PER_HOUR=0 to disable I2V cap, or wait until next UTC hour."
        )
    model_path = I2V_MODEL
    res = I2V_RESOLUTION
    duration_sec = clamp_wan_i2v_duration_sec(duration_sec)
    i2v_base = (os.environ.get("WAVESPEED_I2V_API_BASE") or I2V_API_BASE_DEFAULT).rstrip("/")
    _to = _wavespeed_http_timeout_sec()
    try:
        url = f"{i2v_base}/{model_path}"
        body: Dict[str, Any] = {
            "prompt": (motion_prompt or "speaking to camera, subtle head movement")[:500],
            "image": image_url,
            "image_url": image_url,
            "duration": duration_sec,
            "resolution": res,
            "seed": -1,
        }
        print(
            f"WAVESPEED_I2V_SUBMIT url={url} model_path={model_path} duration={duration_sec} "
            f"resolution={res} prompt_len={len((motion_prompt or '')[:500])} "
            f"image_ref={str(image_url)[:160]}",
            flush=True,
        )
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=_to) as resp:
                http_status = int(resp.getcode() or 0)
                raw_submit = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            try:
                err_body = e.read().decode("utf-8", errors="replace")
            except Exception:
                err_body = ""
            print(f"I2V status: {e.code}", flush=True)
            print(f"I2V response: {err_body}", flush=True)
            if e.code == 429 and _ws_resilient_enabled():
                wavespeed_trip_circuit("i2v_429", ttl_sec=300)
                park_wavespeed_retry_job(
                    {"kind": "i2v", "job_id": job_id, "image_url": (image_url or "")[:200], "motion": (motion_prompt or "")[:120]},
                    "http_429",
                )
            return None, f"I2V submit HTTP {e.code}: {err_body or e.reason or str(e)}"
        print(f"I2V status: {http_status}", flush=True)
        print(f"I2V response: {raw_submit}", flush=True)
        if http_status != 200:
            return None, f"I2V submit HTTP {http_status} (expected 200); body logged above"
        try:
            out = json.loads(raw_submit)
        except ValueError:
            return None, f"I2V submit non-JSON: {raw_submit[:300]}"
        if not _wavespeed_api_ok(out.get("code")):
            print(f"I2V envelope rejected: code={out.get('code')} message={out.get('message')}", flush=True)
            return None, f"I2V submit code={out.get('code')}: {out.get('message') or raw_submit[:800]}"
        data = out.get("data") or {}
        task_id = data.get("id")
        if not task_id:
            return None, "I2V submit missing task id in response"
        get_url = (data.get("urls") or {}).get("get") or f"{i2v_base}/predictions/{task_id}/result"
        last_status = ""
        for _ in range(max(1, max_wait // 3)):
            time.sleep(3)
            try:
                req_g = urllib.request.Request(
                    get_url, headers={"Authorization": f"Bearer {api_key}"}, method="GET"
                )
                with urllib.request.urlopen(req_g, timeout=_to) as resp:
                    result = json.loads(resp.read().decode("utf-8", errors="replace"))
            except urllib.error.HTTPError as poll_http:
                try:
                    pb = poll_http.read().decode("utf-8", errors="replace")
                except Exception:
                    pb = ""
                print(f"I2V poll HTTP {poll_http.code}: {pb[:2000]}", flush=True)
                continue
            except (urllib.error.URLError, TimeoutError, OSError) as loop_e:
                print(f"I2V poll network error (retry): {loop_e}", flush=True)
                continue
            except Exception as loop_e:
                print(f"I2V poll error (retry): {loop_e}", flush=True)
                continue
            if not _wavespeed_api_ok(result.get("code")):
                print(f"I2V poll bad envelope: {json.dumps(result, ensure_ascii=False)[:1500]}", flush=True)
                continue
            rdata = result.get("data") or {}
            status = (str(rdata.get("status") or "")).lower()
            last_status = status
            if status in ("completed", "succeeded", "complete", "success"):
                outputs = rdata.get("outputs") or []
                if not outputs:
                    return None, "task completed but outputs[] empty (wrong model or image fetch failed?)"
                out0 = outputs[0]
                clip_url = (
                    out0 if isinstance(out0, str) else (out0.get("url") if isinstance(out0, dict) else str(out0))
                )
                cu = str(clip_url).strip() or None
                if not cu:
                    return None, "task completed but output URL empty"
                print(f"I2V completed; output[0] URL length={len(cu)}", flush=True)
                return cu, None
            if status == "failed":
                err = (
                    rdata.get("error")
                    or rdata.get("message")
                    or rdata.get("fail_msg")
                    or str(rdata)[:400]
                )
                print(f"I2V task failed: {err}", flush=True)
                return None, f"task failed: {err}"
        return None, f"poll timeout after ~{max_wait}s (last status: {last_status or 'unknown'})"
    except Exception as e:
        return None, f"{type(e).__name__}: {str(e)[:400]}"


def generate_i2v_clip_url(
    image_url: str,
    motion_prompt: str,
    api_key: str,
    duration_sec: int = CLIP_DURATION_SEC,
    *,
    i2v_model: Optional[str] = None,
    resolution: Optional[str] = None,
    max_wait: int = 240,
) -> Optional[str]:
    """
    Submit one I2V task, poll until completed, return remote video URL (no local file).
    Used by Mission Control short preview; same API shape as generate_i2v_clip.
    """
    u, _ = generate_i2v_clip_url_detailed(
        image_url,
        motion_prompt,
        api_key,
        duration_sec=duration_sec,
        i2v_model=i2v_model,
        resolution=resolution,
        max_wait=max_wait,
    )
    return u


def split_narration_into_segments(narration: str, num_segments: int = NUM_CLIPS) -> List[str]:
    """Split script into roughly equal segments for B-roll. Prefer sentence boundaries."""
    text = (narration or "").strip()
    if not text:
        return ["speaking to camera"] * num_segments
    sentences = [s.strip() for s in text.replace("!", ".").replace("?", ".").split(".") if s.strip()]
    if not sentences:
        sentences = [text]
    if len(sentences) <= num_segments:
        # Pad or use as-is
        out = sentences + [""] * max(0, num_segments - len(sentences))
        return out[:num_segments]
    # Distribute sentences across num_segments
    per = len(sentences) // num_segments
    extra = len(sentences) % num_segments
    segments = []
    idx = 0
    for i in range(num_segments):
        n = per + (1 if i < extra else 0)
        chunk = " ".join(sentences[idx : idx + n])
        idx += n
        segments.append(chunk or " ")
    return segments


def motion_prompts_for_segments(segment_texts: List[str]) -> List[str]:
    """
    One UGC-style motion prompt per I2V segment (non-cinematic, social-native).
    Scene keys cycle hook → problem → demo → benefits → cta (demo always present).
    """
    try:
        from engine.ugc_visual_prompts import build_ugc_i2v_prompt, default_ugc_scene_types
    except Exception:
        build_ugc_i2v_prompt = None  # type: ignore[assignment]
        default_ugc_scene_types = None  # type: ignore[assignment]
    n = len(segment_texts)
    if not n:
        return []
    if build_ugc_i2v_prompt and default_ugc_scene_types:
        keys = default_ugc_scene_types(n)
        return [build_ugc_i2v_prompt(keys[i]) for i in range(n)]
    return ["authentic social media UGC, handheld, natural lighting"] * n


def generate_t2i_image_url(
    prompt: str,
    api_key: str,
    *,
    model: Optional[str] = None,
    size: Optional[str] = None,
    max_wait: int = 200,
    negative_prompt: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str]]:
    """
    Text-to-image via WaveSpeed (same task flow as influencer avatar_visual_generator).
    Returns (image_url, None) on success, or (None, error_hint) on failure.
    """
    api_key = normalize_wavespeed_api_key_secret(api_key)
    if not api_key:
        return None, "WaveSpeed API key is empty after normalization — set WAVESPEED_API_KEY (raw token, no Bearer prefix) in KLIP-AVATAR/.env."
    if not _wavespeed_rate_limit_allows("t2i"):
        return None, (
            "WaveSpeed T2I hourly cap reached (WAVESPEED_MAX_T2I_PER_HOUR when split; "
            "or legacy shared WAVESPEED_MAX_CALLS_PER_HOUR). "
            "Set WAVESPEED_MAX_T2I_PER_HOUR=0 to disable T2I cap only, or wait until next UTC hour."
        )
    p = (prompt or "").strip()
    if not p:
        return None, "empty prompt"
    model_path = (model or os.environ.get("WAVESPEED_IMAGE_MODEL") or "wavespeed-ai/flux-dev").strip()
    if model_path and not model_path.startswith("wavespeed-ai/"):
        model_path = f"wavespeed-ai/{model_path}" if "/" not in model_path else model_path
    size_s = (size or os.environ.get("WAVESPEED_T2I_SIZE") or "768*1344").strip()
    body: Dict[str, Any] = {
        "prompt": p[:2000],
        "size": size_s,
        "num_inference_steps": 28,
        "guidance_scale": 3.5,
        "num_images": 1,
        "seed": -1,
    }
    np = (negative_prompt or "").strip()
    if np:
        body["negative_prompt"] = np[:1000]
    submit_url = f"{BASE}/{model_path}"
    try:
        req = urllib.request.Request(
            submit_url,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                raw_submit = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            try:
                err_body = e.read().decode("utf-8", errors="replace")[:600]
            except Exception:
                err_body = ""
            if e.code == 401:
                return None, (
                    "T2I submit HTTP 401: Invalid or rejected WaveSpeed API token. "
                    "Set WAVESPEED_API_KEY in KLIP-AVATAR/.env to the raw API key only (no 'Bearer ' prefix or quotes). "
                    f"Provider: {err_body or e.reason or 'Unauthorized'}"
                )
            return None, f"T2I submit HTTP {e.code}: {err_body or e.reason or 'request failed'}"
        try:
            out = json.loads(raw_submit)
        except ValueError:
            return None, f"T2I submit non-JSON response: {raw_submit[:400]}"
        if not _wavespeed_api_ok(out.get("code")):
            msg = out.get("message") or out.get("msg") or raw_submit[:500]
            return None, f"T2I submit rejected: code={out.get('code')} — {msg}"
        data = out.get("data") or {}
        task_id = data.get("id") or data.get("task_id")
        get_url = (data.get("urls") or {}).get("get") or (
            f"{BASE}/predictions/{task_id}" if task_id else ""
        )
        if not task_id:
            st = (str(data.get("status") or "")).lower()
            if st in ("completed", "succeeded", "complete", "success"):
                outputs = data.get("outputs") or []
                if outputs:
                    out0 = outputs[0]
                    img_url = (
                        out0
                        if isinstance(out0, str)
                        else (out0.get("url") if isinstance(out0, dict) else str(out0))
                    )
                    u = str(img_url).strip() or None
                    if u and _http_image_url(u):
                        return u, None
                    if u:
                        return None, f"T2I submit returned non-HTTP output: {u[:120]}"
            return None, "T2I submit: missing task id in response"
        last_status = ""
        for _ in range(max(1, max_wait // 3)):
            time.sleep(3)
            try:
                req_g = urllib.request.Request(
                    get_url, headers={"Authorization": f"Bearer {api_key}"}, method="GET"
                )
                with urllib.request.urlopen(req_g, timeout=20) as resp:
                    result = json.loads(resp.read().decode("utf-8", errors="replace"))
            except urllib.error.HTTPError as poll_http:
                if poll_http.code == 401:
                    return None, (
                        "T2I poll HTTP 401: WaveSpeed rejected the API token. "
                        "Use the raw key in WAVESPEED_API_KEY (no Bearer prefix)."
                    )
                last_status = f"poll_http:{poll_http.code}"
                continue
            except Exception as poll_e:
                last_status = f"poll_err:{type(poll_e).__name__}"
                continue
            if not _wavespeed_api_ok(result.get("code")):
                last_status = f"code={result.get('code')}"
                continue
            rdata = result.get("data") or {}
            status = (str(rdata.get("status") or "")).lower()
            last_status = status or last_status
            if status in ("completed", "succeeded", "complete", "success"):
                outputs = rdata.get("outputs") or []
                if not outputs:
                    return None, "T2I completed but outputs[] empty — try another model or shorter prompt"
                out0 = outputs[0]
                img_url = out0 if isinstance(out0, str) else (out0.get("url") if isinstance(out0, dict) else str(out0))
                u = str(img_url).strip() or None
                if u and _http_image_url(u):
                    return u, None
                if u:
                    return None, "T2I completed but image URL was not http(s) (unexpected output shape)"
                return None, "T2I completed but image URL missing in outputs"
            if status == "failed":
                err = (
                    rdata.get("error")
                    or rdata.get("message")
                    or rdata.get("fail_msg")
                    or str(rdata)[:400]
                )
                return None, f"T2I task failed: {err}"
        return None, f"T2I poll timeout after ~{max_wait}s (last status: {last_status or 'unknown'})"
    except Exception as e:
        return None, f"{type(e).__name__}: {str(e)[:450]}"


def _t2i_err_is_rate_limited(err: Optional[str]) -> bool:
    if not err:
        return False
    e = err.lower()
    return "429" in err or "rate limit" in e or "too many requests" in e


def _normalize_t2i_model_path(model: Optional[str]) -> str:
    m = (model or "").strip()
    if not m:
        m = os.environ.get("WAVESPEED_IMAGE_MODEL") or "wavespeed-ai/flux-dev"
    if m.startswith("wavespeed-ai/"):
        return m
    if "/" in m:
        return m if m.startswith("wavespeed-ai/") else f"wavespeed-ai/{m}"
    return f"wavespeed-ai/{m}"


def wavespeed_processing_prediction_count(api_key: str) -> Tuple[int, Optional[str]]:
    """
    Best-effort: POST /api/v3/predictions with status=processing; returns count of items.
    Returns (-1, err_hint) if the API call fails (caller may ignore and use Redis-only guards).
    """
    try:
        body = json.dumps({"page": 1, "page_size": 50, "status": "processing"}).encode("utf-8")
        req = urllib.request.Request(
            f"{BASE}/predictions",
            data=body,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        out = json.loads(raw)
        if not _wavespeed_api_ok(out.get("code")):
            return -1, f"predictions list code={out.get('code')}"
        data = out.get("data") or {}
        items = data.get("items") or data.get("list") or []
        if isinstance(items, list):
            return len(items), None
        return -1, "unexpected predictions response shape"
    except urllib.error.HTTPError as e:
        try:
            b = e.read().decode("utf-8", errors="replace")[:400]
        except Exception:
            b = ""
        return -1, f"HTTP {e.code}: {b or e.reason}"
    except Exception as e:
        return -1, f"{type(e).__name__}: {str(e)[:200]}"


def is_high_resource_wavespeed_model(model_path: str) -> bool:
    """Flux T2I + WAN 2.2 I2V: stricter IPM spacing when resilient mode is on."""
    m = (model_path or "").lower()
    return "flux-dev" in m or "wan-2.2" in m


def generate_t2i_image_url_with_resilient_fallback(
    prompt: str,
    api_key: str,
    *,
    primary_model: Optional[str] = None,
    negative_prompt: Optional[str] = None,
    size: Optional[str] = None,
    max_wait: int = 220,
) -> Tuple[Optional[str], Optional[str], Dict[str, Any]]:
    """
    Try primary T2I model; on repeated rate-limit errors, optionally try fallback models (draft).
    Returns (url, err, meta) where meta includes model_used and high_speed_draft.
    """
    primary = _normalize_t2i_model_path(primary_model)
    meta: Dict[str, Any] = {
        "model_used": primary,
        "high_speed_draft": False,
        "fallback_attempts": [],
    }
    last_err: Optional[str] = None
    max_primary_on_429 = max(1, int(os.environ.get("WAVESPEED_T2I_PRIMARY_429_RETRIES", "2")))
    attempts = 0
    while attempts < max_primary_on_429:
        attempts += 1
        u, err = generate_t2i_image_url(
            prompt,
            api_key,
            model=primary,
            negative_prompt=negative_prompt,
            size=size,
            max_wait=max_wait,
        )
        if u:
            return u, None, meta
        last_err = err
        meta["fallback_attempts"].append({"model": primary, "error": (err or "")[:300]})
        if not _t2i_err_is_rate_limited(err):
            break
        if attempts < max_primary_on_429:
            try:
                time.sleep(min(12.0, float(2 ** (attempts - 1))))
            except Exception:
                time.sleep(2.0)

    raw_fb = os.environ.get(
        "WAVESPEED_T2I_FALLBACK_MODELS",
        "wavespeed-ai/gemini-3-flash-image,wavespeed-ai/sdxl",
    )
    for part in [x.strip() for x in raw_fb.split(",") if x.strip()]:
        fb = _normalize_t2i_model_path(part)
        if fb.rstrip("/") == primary.rstrip("/"):
            continue
        u, err = generate_t2i_image_url(
            prompt,
            api_key,
            model=fb,
            negative_prompt=negative_prompt,
            size=size,
            max_wait=max_wait,
        )
        meta["fallback_attempts"].append({"model": fb, "error": (err or "")[:300]})
        if u:
            meta["model_used"] = fb
            meta["high_speed_draft"] = True
            return u, None, meta
        last_err = err
    return None, last_err, meta


def generate_lipsync_video_to_path(
    image_url: str,
    audio_path: str,
    api_key: str,
    output_path: str,
    *,
    lipsync_model: Optional[str] = None,
    max_wait: int = 720,
    job_id: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str]]:
    """
    WaveSpeed talking-head lipsync: portrait image URL + local audio → downloaded MP4 path.
    Uses Redis fixed_male_avatar_url / fixed_female_avatar_url via caller-supplied image_url.
    Model default: WAVESPEED_LIPSYNC_MODEL or wavespeed-ai/ltx-2.3/lipsync.
    Returns (output_path, None) on success, else (None, error_hint).
    """
    if not image_url:
        return None, "missing image_url or audio file"
    try:
        audio_path = os.path.abspath(os.path.normpath(audio_path))
    except Exception:
        pass
    if not audio_path or not os.path.isfile(audio_path):
        return None, "missing image_url or audio file"
    if wavespeed_circuit_is_open():
        park_wavespeed_retry_job(
            {"kind": "lipsync", "job_id": job_id, "image_url": image_url[:200], "audio_path": audio_path},
            "circuit_open",
        )
        return None, "WaveSpeed circuit breaker open (recent limits); job parked in wavespeed:retry_queue"
    if not _wavespeed_rate_limit_allows("lipsync"):
        park_wavespeed_retry_job(
            {"kind": "lipsync", "job_id": job_id, "image_url": image_url[:200], "audio_path": audio_path},
            "hourly_cap",
        )
        wavespeed_trip_circuit("lipsync_hourly_cap", ttl_sec=180)
        return None, (
            "WaveSpeed lipsync hourly cap reached (WAVESPEED_MAX_LIPSYNC_PER_HOUR or WAVESPEED_MAX_I2V_PER_HOUR). "
            "Set WAVESPEED_MAX_LIPSYNC_PER_HOUR=0 to disable, or wait until next UTC hour."
        )
    audio_url, up_err = upload_file_detailed(audio_path, api_key)
    if not audio_url:
        return None, up_err or "audio upload failed"
    raw_model = (
        lipsync_model or os.environ.get("WAVESPEED_LIPSYNC_MODEL") or "wavespeed-ai/ltx-2.3/lipsync"
    ).strip().lstrip("/")
    model_path = raw_model if raw_model.startswith("wavespeed-ai/") else f"wavespeed-ai/{raw_model}"
    url = f"{BASE}/{model_path}"
    # Unified API: models vary; try image+audio then image_url+audio_url.
    bodies: List[Dict[str, Any]] = [
        {"image": image_url, "audio": audio_url},
        {"image_url": image_url, "audio_url": audio_url},
    ]
    last_submit_err = ""
    for body in bodies:
        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(body).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            _lip_to = _wavespeed_http_timeout_sec()
            with urllib.request.urlopen(req, timeout=_lip_to) as resp:
                raw_submit = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            try:
                err_body = e.read().decode("utf-8", errors="replace")[:500]
            except Exception:
                err_body = ""
            last_submit_err = f"HTTP {e.code}: {err_body or e.reason}"
            if e.code == 429 and _ws_resilient_enabled():
                wavespeed_trip_circuit("lipsync_429", ttl_sec=300)
                park_wavespeed_retry_job(
                    {"kind": "lipsync", "job_id": job_id, "image_url": image_url[:200], "audio_path": audio_path},
                    "http_429",
                )
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
                    get_url, headers={"Authorization": f"Bearer {api_key}"}, method="GET"
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
                    return None, "lipsync completed but outputs[] empty"
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
                        timeout=_wavespeed_http_timeout_sec(),
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
                return None, f"lipsync task failed: {err}"
        return None, f"lipsync poll timeout (last={last_status})"
    return None, last_submit_err or "lipsync submit failed"


def resolve_fixed_portrait_image_url(
    avatar_id: str,
    avatar_profile: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """
    Strict avatar consistency: optional Redis-hosted portrait URLs (HTTPS) so the same
    reference image is used for every I2V run (no drift from re-uploads).
    Keys: fixed_male_avatar_url, fixed_female_avatar_url.
    """
    try:
        from klipaura_core.infrastructure.redis_client import get_redis_client

        r = get_redis_client()
        ap = avatar_profile if isinstance(avatar_profile, dict) else {}
        gender = str(ap.get("gender") or "").strip().lower()
        aid = (avatar_id or "").lower()
        if gender in ("female", "f", "woman", "girl") or any(
            x in aid for x in ("female", "woman", "girl")
        ):
            raw = r.get("fixed_female_avatar_url")
        else:
            raw = r.get("fixed_male_avatar_url")
        if raw is None:
            return None
        s = raw if isinstance(raw, str) else (raw.decode("utf-8", errors="ignore") if hasattr(raw, "decode") else str(raw))
        s = (s or "").strip()
        if s.startswith("http://") or s.startswith("https://"):
            return s
    except Exception:
        pass
    return None


def generate_broll_clips(
    avatar_face_path: str,
    segment_texts: List[str],
    api_key: str,
    output_dir: str,
    *,
    motion_prompts: Optional[List[str]] = None,
    clip_duration_sec: Optional[int] = None,
    i2v_model: Optional[str] = None,
    i2v_resolution: Optional[str] = None,
    image_url_override: Optional[str] = None,
    job_id: Optional[str] = None,
) -> List[str]:
    """
    Upload avatar image, generate one I2V clip per segment, download to output_dir.
    Uses motion_prompts if provided (from video planning agent); else derives from segment_texts.
    clip_duration_sec: per-clip duration (from plan or env WAVESPEED_I2V_MAX_CLIP_SEC); default CLIP_DURATION_SEC.
    If image_url_override is set (e.g. fixed Redis portrait URL), skip upload — saves credits and keeps pixels stable.
    Returns list of local clip paths (successful only).
    """
    image_url = (image_url_override or "").strip()
    if not image_url:
        image_url = upload_file(avatar_face_path, api_key)
    if not image_url:
        return []
    prompts = motion_prompts if motion_prompts and len(motion_prompts) >= len(segment_texts) else motion_prompts_for_segments(segment_texts)
    duration = clamp_wan_i2v_duration_sec(
        clip_duration_sec if clip_duration_sec is not None else CLIP_DURATION_SEC
    )
    paths: List[str] = []
    continuity = _i2v_frame_continuity_enabled()
    ffmpeg_exe = _get_ffmpeg_exe()
    current_image_url = image_url
    for i, prompt in enumerate(prompts):
        if i >= len(segment_texts):
            break
        out_path = os.path.join(output_dir, f"clip_{i:02d}.mp4")
        p = generate_i2v_clip(
            current_image_url,
            prompt,
            api_key,
            out_path,
            duration_sec=duration,
            i2v_model=i2v_model,
            resolution=i2v_resolution,
            job_id=job_id,
        )
        if p:
            paths.append(p)
            if continuity and i + 1 < len(segment_texts):
                frame_png = os.path.join(output_dir, f"_continuity_frame_{i:02d}.png")
                next_url: Optional[str] = None
                if extract_last_frame_png(p, frame_png, ffmpeg_exe=ffmpeg_exe):
                    next_url = upload_file(frame_png, api_key)
                if next_url and _http_image_url(next_url):
                    current_image_url = next_url
        else:
            break
    return paths
