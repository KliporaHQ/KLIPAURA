#!/usr/bin/env python3
"""
Redis consumer: pop `klip:jobs:pending`, run `python -m pipeline.ugc_pipeline` in core_v1, push to HITL on success.

Run from repo root:
  python klip-avatar/worker.py

Requires: UPSTASH + token or REDIS_URL, `.env` at repo root, pipeline keys in core_v1/.env.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
_KLIP_SCANNER_ROOT = _REPO / "klip-scanner"
if _KLIP_SCANNER_ROOT.is_dir() and str(_KLIP_SCANNER_ROOT) not in sys.path:
    sys.path.insert(0, str(_KLIP_SCANNER_ROOT))
_KLIP_FUNNEL_ROOT = _REPO / "klip-funnel"
if _KLIP_FUNNEL_ROOT.is_dir() and str(_KLIP_FUNNEL_ROOT) not in sys.path:
    sys.path.insert(0, str(_KLIP_FUNNEL_ROOT))

from infrastructure.avatar_registry import resolve_elevenlabs_voice_id
from infrastructure.job_state import create_manifest, touch_manifest_stage, update_manifest
from infrastructure.queue_names import DLQ, HITL_PENDING, JOBS_PENDING, QUEUE_GLOBAL_PAUSED_KEY
from infrastructure.redis_client import LocalRedis, RedisConfigError, get_redis_client

_MAX_RETRIES = 3
_CORE_V1 = _REPO / "klip-avatar" / "core_v1"

_WORKER_HEARTBEAT_KEY = "klip:worker:heartbeat"
_WORKER_HEARTBEAT_TTL_SECONDS = 120
_WORKER_HEARTBEAT_INTERVAL_SECONDS = 10

_TEMPLATES_PATH = _REPO / "config" / "templates.json"
_TEMPLATES_CACHE: dict[str, dict] | None = None


def _safe_template_id(raw: str) -> str:
    return "".join(c for c in (raw or "").strip().lower() if c.isalnum() or c in ("-", "_"))


def _template_scene_types(template_id: str) -> list[str]:
    global _TEMPLATES_CACHE
    tid = _safe_template_id(template_id)
    if not tid:
        return []
    if _TEMPLATES_CACHE is None:
        cache: dict[str, dict] = {}
        try:
            if _TEMPLATES_PATH.is_file():
                data = json.loads(_TEMPLATES_PATH.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    for t in data:
                        if not isinstance(t, dict):
                            continue
                        k = _safe_template_id(str(t.get("id") or ""))
                        sts = t.get("scene_types")
                        if not k:
                            continue
                        if not isinstance(sts, list) or not all(isinstance(x, str) for x in sts):
                            sts = []
                        knobs = t.get("knobs")
                        if not isinstance(knobs, dict):
                            knobs = {}
                        cache[k] = {
                            "scene_types": [str(x).strip() for x in sts if str(x).strip()],
                            "knobs": knobs,
                        }
        except Exception:
            cache = {}
        _TEMPLATES_CACHE = cache
    entry = (_TEMPLATES_CACHE or {}).get(tid) or {}
    sts2 = entry.get("scene_types") if isinstance(entry, dict) else None
    if not isinstance(sts2, list):
        return []
    return [str(x).strip() for x in sts2 if str(x).strip()]


def _template_knobs(template_id: str) -> dict:
    global _TEMPLATES_CACHE
    tid = _safe_template_id(template_id)
    if not tid:
        return {}
    if _TEMPLATES_CACHE is None:
        _template_scene_types(tid)
    entry = (_TEMPLATES_CACHE or {}).get(tid) or {}
    knobs = entry.get("knobs") if isinstance(entry, dict) else None
    return knobs if isinstance(knobs, dict) else {}


def _parse_split_ratio(raw: object) -> float | None:
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        v = float(raw)
        if 0.2 <= v <= 0.8:
            return v
        return None
    s = str(raw).strip().lower()
    if not s:
        return None
    if "/" in s:
        try:
            a, b = s.split("/", 1)
            top = float(a.strip())
            bot = float(b.strip())
            if top <= 0 or bot <= 0:
                return None
            return max(0.2, min(0.8, top / (top + bot)))
        except Exception:
            return None
    try:
        v = float(s)
    except ValueError:
        return None
    if 0.2 <= v <= 0.8:
        return v
    return None


def _write_heartbeat(r, *, state: str, job_id: str | None = None, error: str | None = None) -> None:
    try:
        payload = {
            "state": state,
            "job_id": job_id,
            "error": (error or "")[:240] if error else None,
            "pid": os.getpid(),
            "host": socket.gethostname(),
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        r.setex(
            _WORKER_HEARTBEAT_KEY,
            json.dumps(payload),
            _WORKER_HEARTBEAT_TTL_SECONDS,
        )
    except Exception:
        return


def _pop_job_payload(r) -> str | None:
    """Pop one job payload from the pending queue.

    Local redis supports blocking pops. Upstash REST does not reliably support BLPOP,
    so we poll using LPOP when not using LocalRedis.
    """
    if isinstance(r, LocalRedis):
        popped = r.blpop([JOBS_PENDING], timeout=10)
        if not popped:
            return None
        return popped[1]
    return r.lpop(JOBS_PENDING)


def _studio_persona_doc(r: object, persona_id: str) -> dict | None:
    """Load Avatar Studio persona JSON from Redis (``avatar:persona:{uuid}``)."""
    pid = (persona_id or "").strip()
    if not pid:
        return None
    try:
        raw = r.get(f"avatar:persona:{pid}")  # type: ignore[union-attr]
        if raw is None:
            return None
        s = raw if isinstance(raw, str) else str(raw)
        doc = json.loads(s)
        return doc if isinstance(doc, dict) else None
    except Exception:
        return None


def _merge_studio_persona_into_job(job: dict, r: object) -> None:
    """Resolve ``persona_id`` → ``avatar_id`` + ``elevenlabs_voice_id`` from Redis (Phase 3)."""
    pid = str(job.get("persona_id") or "").strip()
    if not pid:
        return
    doc = _studio_persona_doc(r, pid)
    if not doc:
        print(f"[WORKER] persona_id={pid} not found in Redis — using payload avatar_id", flush=True)
        return
    aid = str(doc.get("avatar_id") or "").strip()
    if aid:
        job["avatar_id"] = aid
    vid = str(doc.get("voice_id") or "").strip()
    if vid:
        job["elevenlabs_voice_id"] = vid
    pname = str(doc.get("name") or "").strip()
    if pname:
        job["persona_display_name"] = pname
    pimg = str(doc.get("image_url") or "").strip()
    if pimg:
        job["persona_image_url"] = pimg


def _avatar_dir(avatar_id: str) -> Path:
    safe = "".join(c for c in (avatar_id or "").strip() if c.isalnum() or c in ("_", "-"))
    return _CORE_V1 / "data" / "avatars" / (safe or "default")


def _hydrate_avatar_from_r2(avatar_id: str) -> bool:
    try:
        from infrastructure.storage import create_r2_store, r2_configured

        if not r2_configured():
            return False
        store = create_r2_store()
    except Exception:
        return False

    base = _avatar_dir(avatar_id)
    base.mkdir(parents=True, exist_ok=True)

    ok_any = False
    for name in ("persona.json", "portrait.png", "face.png", "social_config.json"):
        try:
            key = f"avatars/{avatar_id}/{name}"
            dest = base / name
            if dest.is_file():
                ok_any = True
                continue
            if store.download_to_path(key, str(dest)):
                ok_any = True
        except Exception:
            continue

    return ok_any


def _avatar_ready(avatar_id: str) -> bool:
    d = _avatar_dir(avatar_id)
    if not d.is_dir():
        return False
    if not (d / "persona.json").is_file():
        return False
    for name in ("portrait.png", "face.png", "avatar.png", "profile.png"):
        if (d / name).is_file():
            return True
    # fallback: any image file
    try:
        for p in d.iterdir():
            if p.is_file() and p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
                return True
    except OSError:
        return False
    return False


def _load_json_file(path: Path) -> dict:
    try:
        if not path.is_file():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _avatar_defaults(avatar_id: str) -> dict[str, str]:
    base = _avatar_dir(avatar_id)
    persona = _load_json_file(base / "persona.json")
    social = _load_json_file(base / "social_config.json")
    out: dict[str, str] = {}
    cta = (
        (persona.get("cta_line") if isinstance(persona, dict) else None)
        or (persona.get("affiliate_cta_line") if isinstance(persona, dict) else None)
    )
    if isinstance(cta, str) and cta.strip():
        out["cta_line"] = cta.strip()
    sys_override = (
        (persona.get("ugc_script_system_override") if isinstance(persona, dict) else None)
        or (persona.get("script_system_override") if isinstance(persona, dict) else None)
    )
    if isinstance(sys_override, str) and sys_override.strip():
        out["script_system_override"] = sys_override.strip()
    voice = None
    if isinstance(social, dict):
        voice = social.get("elevenlabs_voice_id") or social.get("ELEVENLABS_VOICE_ID")
    if not voice and isinstance(persona, dict):
        voice = persona.get("voice_id")
    if isinstance(voice, str) and voice.strip():
        out["elevenlabs_voice_id"] = voice.strip()
    return out


def _final_video_path() -> Path:
    return _CORE_V1 / "outputs" / "final_publish" / "FINAL_VIDEO.mp4"


def _job_video_path(job_id: str) -> Path:
    return Path(os.getenv("JOBS_DIR", str(_REPO / "jobs"))).resolve() / job_id / "FINAL_VIDEO.mp4"


def _optional_r2_upload(local_file: Path, job_id: str) -> str | None:
    try:
        from infrastructure.storage import r2_configured, upload_to_r2

        if not r2_configured() or not local_file.is_file():
            return None
        key = f"jobs/{job_id}/FINAL_VIDEO.mp4"
        return upload_to_r2(str(local_file), key, content_type="video/mp4")
    except Exception:
        return None


# 15–20s recommended so manifest moves during long WaveSpeed/FFmpeg steps.
_MANIFEST_HB_INTERVAL_SEC = float(os.environ.get("KLIP_WORKER_MANIFEST_HB_SEC", "18") or "18")


def _pipeline_subprocess_timeout_sec() -> int:
    """Max wall time for ``python -m pipeline.ugc_pipeline`` (default 7200s). Set lower to fail fast on hangs."""
    try:
        return max(60, min(86400, int(os.environ.get("KLIP_PIPELINE_SUBPROCESS_TIMEOUT_SEC", "7200") or "7200")))
    except ValueError:
        return 7200


def _run_pipeline(job_id: str, env_overrides: dict[str, str]) -> tuple[int, str]:
    env = os.environ.copy()
    for k, v in (env_overrides or {}).items():
        if v is None:
            continue
        env[str(k)] = str(v)
    env["JOB_ID"] = job_id
    env["KLIP_PIPELINE_RUN"] = "1"
    _pp_parts = [str(_KLIP_SCANNER_ROOT), str(_KLIP_FUNNEL_ROOT), str(_REPO)]
    _old_pp = (env.get("PYTHONPATH") or "").strip()
    if _old_pp:
        _pp_parts.append(_old_pp)
    env["PYTHONPATH"] = os.pathsep.join(_pp_parts)
    timeout_sec = _pipeline_subprocess_timeout_sec()
    print(
        f"[WORKER] job_id={job_id} starting ugc_pipeline subprocess cwd={_CORE_V1} timeout_sec={timeout_sec}",
        flush=True,
    )
    proc = subprocess.Popen(
        [sys.executable, "-m", "pipeline.ugc_pipeline"],
        cwd=str(_CORE_V1),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    stop_hb = threading.Event()
    try:
        touch_manifest_stage(
            job_id,
            "worker:running_subprocess",
            f"ugc_pipeline subprocess started (timeout_sec={timeout_sec})",
        )
    except Exception:
        pass

    def _manifest_heartbeat() -> None:
        n = 0
        interval = max(10.0, min(90.0, _MANIFEST_HB_INTERVAL_SEC))
        t0 = time.time()
        while not stop_hb.is_set():
            if proc.poll() is not None:
                return
            n += 1
            elapsed = int(time.time() - t0)
            try:
                touch_manifest_stage(
                    job_id,
                    "worker:running_subprocess",
                    f"subprocess alive hb#{n} elapsed_sec={elapsed} max_sec={timeout_sec}",
                )
            except Exception:
                pass
            if stop_hb.wait(timeout=interval):
                return

    hb_thread = threading.Thread(target=_manifest_heartbeat, name="manifest-hb", daemon=True)
    hb_thread.start()
    try:
        out, err = proc.communicate(timeout=timeout_sec)
    except subprocess.TimeoutExpired:
        try:
            proc.terminate()
            proc.wait(timeout=30)
        except Exception:
            pass
        try:
            proc.kill()
        except Exception:
            pass
        out, err = proc.communicate()
        tail = (
            (out or "") + "\n" + (err or "") + f"\nTIMEOUT: ugc_pipeline exceeded {timeout_sec}s (KLIP_PIPELINE_SUBPROCESS_TIMEOUT_SEC)"
        )[-12000:]
        try:
            touch_manifest_stage(
                job_id,
                "worker:running_subprocess",
                f"TIMEOUT after {timeout_sec}s — subprocess killed",
            )
            update_manifest(
                job_id,
                status="FAILED",
                error=f"SUBPROCESS_TIMEOUT_{timeout_sec}S",
                log_tail=tail,
            )
        except Exception:
            pass
        stop_hb.set()
        print(f"[WORKER] job_id={job_id} ugc_pipeline TIMEOUT after {timeout_sec}s", flush=True)
        return 124, tail
    finally:
        stop_hb.set()
    code = int(proc.returncode or 0)
    tail = ((out or "") + "\n" + (err or ""))[-12000:]
    print(f"[WORKER] job_id={job_id} ugc_pipeline exited rc={code}", flush=True)
    return code, tail


def main() -> None:
    try:
        r = get_redis_client()
    except RedisConfigError as e:
        print("[WORKER] Redis not configured:", e, flush=True)
        raise SystemExit(2)

    print("[WORKER] Online — queue", JOBS_PENDING, flush=True)
    last_hb = 0.0
    _write_heartbeat(r, state="IDLE")
    while True:
        now = time.time()
        if now - last_hb >= _WORKER_HEARTBEAT_INTERVAL_SECONDS:
            _write_heartbeat(r, state="IDLE")
            last_hb = now
        try:
            paused = bool((r.get(QUEUE_GLOBAL_PAUSED_KEY) or "").strip())
        except Exception:
            paused = False
        if paused:
            _write_heartbeat(r, state="PAUSED")
            last_hb = time.time()
            time.sleep(2)
            continue
        try:
            raw = _pop_job_payload(r)
        except Exception as ex:
            print("[WORKER] blpop error:", ex, flush=True)
            _write_heartbeat(r, state="ERROR", error=str(ex)[:240])
            time.sleep(2)
            continue
        if not raw:
            if not isinstance(r, LocalRedis):
                time.sleep(2)
            continue
        try:
            job = json.loads(raw)
        except json.JSONDecodeError:
            print("[WORKER] Bad job JSON, skipping", flush=True)
            continue

        job_id = job.get("job_id")
        product_url = (job.get("product_url") or "").strip()
        product_page_url = (job.get("product_page_url") or "").strip()
        _merge_studio_persona_into_job(job, r)
        avatar_id = (job.get("avatar_id") or "theanikaglow").strip()
        template_id = (job.get("template_id") or job.get("template") or "").strip()
        retry = int(job.get("retry_count", 0))

        if not job_id or not product_url:
            print("[WORKER] Missing job_id or product_url", flush=True)
            continue

        avatar_path = _avatar_dir(avatar_id)
        if not avatar_path.is_dir() or not _avatar_ready(avatar_id):
            _hydrate_avatar_from_r2(avatar_id)

        if not _avatar_ready(avatar_id):
            err = "AVATAR_NOT_FOUND" if not avatar_path.is_dir() else "AVATAR_INCOMPLETE"
            update_manifest(job_id, status="DEAD_LETTER", error=err, payload=job)
            r.rpush(DLQ, json.dumps(job))
            continue

        defaults = _avatar_defaults(avatar_id)

        try:
            update_manifest(job_id, status="PROCESSING", payload=job)
        except Exception:
            create_manifest(job_id, job)

        _write_heartbeat(r, state="PROCESSING", job_id=job_id)
        last_hb = time.time()

        env_overrides: dict[str, str] = {
            "UGC_PRODUCT_URL": product_page_url or product_url,
            "ACTIVE_AVATAR_ID": avatar_id,
        }
        layout_mode = (job.get("layout_mode") or "").strip()
        if layout_mode:
            env_overrides["KLIP_LAYOUT_MODE"] = layout_mode
        if layout_mode == "affiliate_split_55_45":
            env_overrides.setdefault("AFFILIATE_SPLIT_TOP_RATIO", "0.55")

        affiliate_data = job.get("affiliate_data")
        if isinstance(affiliate_data, dict) and affiliate_data:
            env_overrides["KLIP_AFFILIATE_DATA"] = json.dumps(affiliate_data)
            aff_link = str(affiliate_data.get("affiliate_link") or "").strip()
            if aff_link:
                env_overrides["KLIP_AFFILIATE_LINK"] = aff_link
            if aff_link and not (job.get("cta_line") or "").strip():
                env_overrides["UGC_CTA_LINE"] = f"Shop with my link — {aff_link}"
                env_overrides["AFFILIATE_CTA_OVERLAY"] = aff_link[:220]

        scene_types = _template_scene_types(template_id)
        if scene_types:
            env_overrides["UGC_SCENE_TYPES"] = ",".join(scene_types)

        knobs = _template_knobs(template_id)
        if knobs:
            sr = _parse_split_ratio(knobs.get("split_ratio"))
            if sr is not None:
                env_overrides["AFFILIATE_SPLIT_TOP_RATIO"] = f"{sr:.4f}"
            if knobs.get("caption_bottom_margin") is not None:
                try:
                    bm = int(float(knobs.get("caption_bottom_margin")))
                    env_overrides["CAPTION_BOTTOM_MARGIN"] = str(max(40, min(400, bm)))
                except Exception:
                    pass
            if knobs.get("bottom_face_zoom") is not None:
                try:
                    fz = float(knobs.get("bottom_face_zoom"))
                    env_overrides["AFFILIATE_BOTTOM_FACE_ZOOM"] = f"{max(1.0, min(1.28, fz)):.4f}"
                except Exception:
                    pass
            cz = str(knobs.get("captions") or "").strip().lower()
            if cz in ("bottom", "full"):
                env_overrides["CAPTION_ZONE"] = cz
            ct = str(knobs.get("cta_timing") or "").strip().lower()
            if ct:
                env_overrides["AFFILIATE_CTA_TIMING"] = ct
                env_overrides["CAPTION_CTA_TIMING"] = ct
        # Explicit top-band ratio from Mission Control / Avatar Studio (overrides template knobs).
        astr = job.get("affiliate_split_top_ratio")
        if astr is not None and str(astr).strip() != "":
            try:
                ar = float(astr)
                if 0.25 <= ar <= 0.75:
                    env_overrides["AFFILIATE_SPLIT_TOP_RATIO"] = f"{ar:.4f}"
            except (TypeError, ValueError):
                pass
        if job.get("product_image_urls"):
            vals = job.get("product_image_urls")
            if isinstance(vals, list):
                env_overrides["UGC_PRODUCT_IMAGE_URLS"] = ",".join(str(x).strip() for x in vals if str(x).strip())
            elif isinstance(vals, str):
                env_overrides["UGC_PRODUCT_IMAGE_URLS"] = vals
        if job.get("product_title"):
            env_overrides["UGC_PRODUCT_TITLE"] = str(job.get("product_title") or "")
        if job.get("product_bullets"):
            b = job.get("product_bullets")
            if isinstance(b, list):
                env_overrides["UGC_PRODUCT_BULLETS"] = "\n".join(str(x).strip() for x in b if str(x).strip())
            elif isinstance(b, str):
                env_overrides["UGC_PRODUCT_BULLETS"] = b
        if job.get("cta_line"):
            env_overrides["UGC_CTA_LINE"] = str(job.get("cta_line") or "")
            env_overrides["AFFILIATE_CTA_OVERLAY"] = str(job.get("cta_line") or "")
        elif defaults.get("cta_line"):
            env_overrides["UGC_CTA_LINE"] = defaults["cta_line"]
            env_overrides["AFFILIATE_CTA_OVERLAY"] = defaults["cta_line"]
        if job.get("script_system_override"):
            env_overrides["UGC_SCRIPT_SYSTEM_OVERRIDE"] = str(job.get("script_system_override") or "")
        elif defaults.get("script_system_override"):
            env_overrides["UGC_SCRIPT_SYSTEM_OVERRIDE"] = defaults["script_system_override"]
        if job.get("elevenlabs_voice_id"):
            env_overrides["ELEVENLABS_VOICE_ID"] = str(job.get("elevenlabs_voice_id") or "")
        else:
            reg_voice = resolve_elevenlabs_voice_id(avatar_id)
            if reg_voice:
                env_overrides["ELEVENLABS_VOICE_ID"] = reg_voice
            elif defaults.get("elevenlabs_voice_id"):
                env_overrides["ELEVENLABS_VOICE_ID"] = defaults["elevenlabs_voice_id"]

        print(f"[WORKER] Running pipeline job={job_id} retry={retry}", flush=True)
        code, log_tail = _run_pipeline(job_id, env_overrides)

        if code == 0:
            try:
                touch_manifest_stage(
                    job_id,
                    "pipeline_success",
                    "ugc_subprocess exit 0 — validating FINAL_VIDEO.mp4",
                )
            except Exception:
                pass

        # Subprocess wall-clock timeout: do not retry (manifest already status=FAILED).
        if code == 124:
            r.rpush(DLQ, json.dumps(job))
            print(f"[WORKER] job_id={job_id} pipeline timeout — DLQ, status FAILED", flush=True)
            _write_heartbeat(r, state="ERROR", job_id=job_id, error="pipeline_timeout")
            continue

        if code != 0:
            if retry < _MAX_RETRIES:
                job["retry_count"] = retry + 1
                r.lpush(JOBS_PENDING, json.dumps(job))
                update_manifest(job_id, status="RETRYING", retry_count=retry + 1, log_tail=log_tail)
                print(f"[WORKER] Failed rc={code}, requeued retry={retry + 1}", flush=True)
            else:
                r.rpush(DLQ, json.dumps(job))
                update_manifest(job_id, status="DEAD_LETTER", log_tail=log_tail)
                print(f"[WORKER] Failed rc={code}, moved to DLQ", flush=True)
            _write_heartbeat(r, state="ERROR", job_id=job_id, error=f"pipeline_rc={code}")
            continue

        final_path = _final_video_path()
        if not final_path.is_file():
            job["retry_count"] = retry + 1
            if job["retry_count"] <= _MAX_RETRIES:
                r.lpush(JOBS_PENDING, json.dumps(job))
                update_manifest(job_id, status="RETRYING", error="FINAL_VIDEO_MISSING", log_tail=log_tail)
                print("[WORKER] FINAL_VIDEO.mp4 missing, requeue", flush=True)
            else:
                r.rpush(DLQ, json.dumps(job))
                update_manifest(job_id, status="DEAD_LETTER", error="FINAL_VIDEO_MISSING", log_tail=log_tail)
            continue

        per_job_video = _job_video_path(job_id)
        per_job_video.parent.mkdir(parents=True, exist_ok=True)
        try:
            per_job_video.write_bytes(final_path.read_bytes())
        except Exception:
            per_job_video = final_path

        r2_url = _optional_r2_upload(per_job_video, job_id)
        update_manifest(
            job_id,
            status="HITL_PENDING",
            final_video_path=str(per_job_video.resolve()),
            r2_url=r2_url,
            log_tail=log_tail,
            pipeline_stage="video_ready",
            pipeline_detail=("R2 upload skipped or failed" if not r2_url else "R2 upload ok")[:200],
            persona_id=str(job.get("persona_id") or "").strip() or None,
        )
        funnel_url_attached: str | None = None
        if job.get("generate_funnel"):
            try:
                from klip_funnel.funnel_job import build_and_attach_funnel

                pl = dict(job)
                pl.setdefault("product_url", product_url)
                pl.setdefault("product_page_url", product_page_url or None)
                if r2_url:
                    pl["public_video_url"] = r2_url
                    pl["r2_url"] = r2_url
                touch_manifest_stage(job_id, "funnel_build", "build_and_attach_funnel")
                print(f"[WORKER] job_id={job_id} funnel_build start", flush=True)
                url, ferr = build_and_attach_funnel(_REPO, job_id, pl)
                print(f"[WORKER] job_id={job_id} funnel_build done url={bool(url)}", flush=True)
                if url:
                    funnel_url_attached = url
                    touch_manifest_stage(job_id, "funnel_build", "funnel URL attached")
                    update_manifest(
                        job_id,
                        funnel_url=url,
                        funnel_error=None,
                        pipeline_stage="funnel_ready",
                        outputs={"video_url": r2_url, "funnel_url": url},
                    )
                elif ferr:
                    touch_manifest_stage(job_id, "funnel_build", f"failed: {ferr[:200]}")
                    update_manifest(job_id, funnel_error=ferr[:500], pipeline_stage="funnel_failed")
            except Exception as ex:
                touch_manifest_stage(job_id, "funnel_build", f"exception: {type(ex).__name__}")
                update_manifest(
                    job_id,
                    funnel_error=str(ex)[:400],
                    pipeline_stage="funnel_exception",
                    error=str(ex)[:500],
                )
        try:
            touch_manifest_stage(job_id, "pipeline_success", "ready — pushing to HITL queue")
        except Exception:
            pass
        hitl_payload = json.dumps(
            {
                "job_id": job_id,
                "product_url": product_url,
                "product_page_url": product_page_url or None,
                "avatar_id": avatar_id,
                "persona_id": str(job.get("persona_id") or "").strip() or None,
                "final_video_path": str(per_job_video.resolve()),
                "r2_url": r2_url,
                "funnel_url": funnel_url_attached,
                "generate_funnel": bool(job.get("generate_funnel")),
            }
        )
        r.rpush(HITL_PENDING, hitl_payload)
        try:
            from infrastructure.scheduler_budget import bump_daily_video_count

            bump_daily_video_count()
        except Exception:
            pass
        print("[WORKER] Done — pushed to HITL queue", job_id, flush=True)


if __name__ == "__main__":
    try:
        from dotenv import load_dotenv

        load_dotenv(_REPO / ".env", override=False)
    except ImportError:
        pass
    main()
