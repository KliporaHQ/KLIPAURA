#!/usr/bin/env python3
"""
One-command automated full stack: start HITL (if needed), start worker, POST job, poll manifest.

- HITL: uvicorn hitl_server:app --app-dir klip-dispatch on http://127.0.0.1:8080 if /openapi.json not OK.
- Worker: klip-avatar/worker.py unless --skip-start-worker.
- POST: Roborock short URL, active avatar from registry, generate_funnel, affiliate_split_55_45.
- Poll until status in HITL_PENDING | COMPLETED | FAILED | DEAD_LETTER (default timeout 600s; full renders often need --timeout-sec 3600).
- PYTHONPATH: <repo>;<repo>/klip-scanner;<repo>/klip-funnel (via os.pathsep).
- On exit, timeout, or success: terminates only Popen processes this script started (unless --keep-services).

Usage:
  python scripts/run_full_auto_test.py
  python scripts/run_full_auto_test.py --skip-start-hitl --skip-start-worker
  python scripts/run_full_auto_test.py --timeout-sec 3600 --keep-services
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

# Terminal manifest statuses (case-insensitive match for COMPLETED)
_TERMINAL = frozenset({"HITL_PENDING", "COMPLETED", "FAILED", "DEAD_LETTER"})


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _pythonpath_env(repo: Path) -> dict[str, str]:
    env = os.environ.copy()
    parts = [str(repo), str(repo / "klip-scanner"), str(repo / "klip-funnel")]
    sep = os.pathsep
    extra = sep.join(parts)
    if env.get("PYTHONPATH"):
        env["PYTHONPATH"] = extra + sep + env["PYTHONPATH"]
    else:
        env["PYTHONPATH"] = extra
    return env


def _hitl_openapi_ok(base: str, timeout: float = 3.0) -> bool:
    url = base.rstrip("/") + "/openapi.json"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return 200 <= resp.status < 300
    except (urllib.error.URLError, OSError, ValueError):
        return False


def _wait_hitl(base: str, max_sec: float = 90.0, interval: float = 1.0) -> bool:
    deadline = time.time() + max_sec
    while time.time() < deadline:
        if _hitl_openapi_ok(base, timeout=2.0):
            return True
        time.sleep(interval)
    return False


def _local_http_listen(base: str) -> tuple[str, int] | None:
    u = urlparse(base)
    if u.scheme != "http":
        return None
    h = (u.hostname or "").lower()
    if h not in ("127.0.0.1", "localhost", "::1"):
        return None
    port = u.port if u.port is not None else 80
    bind_host = "127.0.0.1" if h != "::1" else "::1"
    return (bind_host, port)


def _normalize_status(st: str | None) -> str:
    s = (st or "").strip()
    if not s:
        return ""
    if s.lower() == "completed":
        return "COMPLETED"
    return s.upper()


def _is_terminal(st: str | None) -> bool:
    return _normalize_status(st) in _TERMINAL


def _is_success_terminal(st: str | None) -> bool:
    n = _normalize_status(st)
    return n in ("HITL_PENDING", "COMPLETED")


def _stop_proc(proc: subprocess.Popen[str] | None, name: str) -> None:
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.terminate()
        try:
            proc.wait(timeout=12)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
    except Exception as e:
        print(f"[run_full_auto_test] warning stopping {name}: {e}", file=sys.stderr)


def main() -> int:
    ap = argparse.ArgumentParser(description="Auto-start HITL + worker and run full pipeline test")
    ap.add_argument(
        "--base-url",
        default=os.environ.get("KLIP_MC_BASE_URL") or "http://127.0.0.1:8080",
        help="HITL base URL",
    )
    ap.add_argument("--skip-start-hitl", action="store_true", help="Do not spawn HITL")
    ap.add_argument("--skip-start-worker", action="store_true", help="Do not spawn worker")
    ap.add_argument(
        "--keep-services",
        action="store_true",
        help="Leave HITL/worker running after exit (only processes started here)",
    )
    ap.add_argument("--ready-sleep", type=float, default=6.0, help="Seconds after worker start before POST")
    ap.add_argument("--timeout-sec", type=float, default=600.0, help="Max wait for terminal manifest (default 10m)")
    ap.add_argument("--poll-sec", type=float, default=2.0, help="Sleep between manifest reads")
    ap.add_argument("--progress-sec", type=float, default=15.0, help="Print progress every N seconds")
    ap.add_argument("--product-url", default="https://amzn.to/4cewwZo")
    ap.add_argument("--avatar-id", default=os.environ.get("ACTIVE_AVATAR_ID", ""))
    ap.add_argument("--affiliate-program-id", default="example_program")
    args = ap.parse_args()

    repo = _repo_root()
    os.chdir(repo)
    env = _pythonpath_env(repo)
    base = args.base_url.rstrip("/")

    hitl_proc: subprocess.Popen[str] | None = None
    worker_proc: subprocess.Popen[str] | None = None
    log_dir = repo / "outputs" / "run_full_auto_test_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")

    try:
        # --- 1. HITL ---
        if not args.skip_start_hitl:
            if _hitl_openapi_ok(base):
                print("[run_full_auto_test] HITL already responding:", base, flush=True)
            else:
                listen = _local_http_listen(base)
                if listen is None:
                    print(
                        "[run_full_auto_test] ERROR: HITL not up; auto-start needs local http "
                        "(e.g. http://127.0.0.1:8080). Use --skip-start-hitl if HITL runs elsewhere.",
                        file=sys.stderr,
                    )
                    return 11
                bind_host, bind_port = listen
                hitl_log = open(log_dir / f"hitl-{stamp}.log", "w", encoding="utf-8", errors="replace")
                hitl_cmd = [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "hitl_server:app",
                    "--app-dir",
                    "klip-dispatch",
                    "--host",
                    bind_host,
                    "--port",
                    str(bind_port),
                ]
                print("[run_full_auto_test] Starting HITL:", " ".join(hitl_cmd), flush=True)
                cflags = 0
                if sys.platform == "win32" and hasattr(subprocess, "CREATE_NO_WINDOW"):
                    cflags = subprocess.CREATE_NO_WINDOW
                hitl_proc = subprocess.Popen(
                    hitl_cmd,
                    cwd=repo,
                    env=env,
                    stdout=hitl_log,
                    stderr=subprocess.STDOUT,
                    creationflags=cflags,
                )
                if not _wait_hitl(base):
                    print(
                        "[run_full_auto_test] ERROR: HITL did not become ready (see",
                        hitl_log.name,
                        ")",
                        file=sys.stderr,
                    )
                    return 10
                print("[run_full_auto_test] HITL ready.", flush=True)

        # --- 2. Worker ---
        if not args.skip_start_worker:
            worker_log = open(log_dir / f"worker-{stamp}.log", "w", encoding="utf-8", errors="replace")
            worker_cmd = [sys.executable, str(repo / "klip-avatar" / "worker.py")]
            print("[run_full_auto_test] Starting worker:", " ".join(worker_cmd), flush=True)
            cflags = 0
            if sys.platform == "win32" and hasattr(subprocess, "CREATE_NO_WINDOW"):
                cflags = subprocess.CREATE_NO_WINDOW
            worker_proc = subprocess.Popen(
                worker_cmd,
                cwd=repo,
                env=env,
                stdout=worker_log,
                stderr=subprocess.STDOUT,
                creationflags=cflags,
            )
            time.sleep(max(0.0, args.ready_sleep))
            print("[run_full_auto_test] Worker start delay done. Log:", worker_log.name, flush=True)

        # --- 3. POST /api/jobs ---
        body = {
            "product_url": args.product_url,
            "avatar_id": args.avatar_id,
            "affiliate_program_id": args.affiliate_program_id,
            "affiliate_fields": {"product_id": "e2e-test", "affiliate_tag": "test-tag"},
            "layout_mode": "affiliate_split_55_45",
            "generate_funnel": True,
        }
        url = f"{base}/api/jobs"
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            print("HTTP", e.code, e.read().decode("utf-8", "replace")[:800], file=sys.stderr)
            return 1
        except urllib.error.URLError as e:
            print("Request failed:", e, file=sys.stderr)
            return 2

        out = json.loads(raw)
        job_id = out.get("job_id")
        print("POST /api/jobs:", json.dumps(out, indent=2), flush=True)
        if not job_id:
            return 3

        if str(repo) not in sys.path:
            sys.path.insert(0, str(repo))
        from infrastructure.job_state import read_manifest

        # --- 4. Poll until terminal ---
        deadline = time.time() + max(30.0, float(args.timeout_sec))
        t_start = time.time()
        last_st: str | None = None
        next_progress = time.time() + max(0.1, float(args.progress_sec))

        while time.time() < deadline:
            try:
                m = read_manifest(str(job_id))
            except Exception as e:
                print("read_manifest:", e, file=sys.stderr)
                time.sleep(args.poll_sec)
                continue

            st = (m.get("status") or "").strip()
            if st != last_st:
                print(f"[poll] status={st} updated_at={m.get('updated_at')}", flush=True)
                last_st = st

            if args.progress_sec > 0 and time.time() >= next_progress:
                next_progress = time.time() + float(args.progress_sec)
                ps = m.get("pipeline_stage") or "—"
                ua = m.get("updated_at") or "—"
                elapsed = time.time() - t_start
                print(
                    f"[progress] t+{int(elapsed)}s status={st} pipeline_stage={ps} updated_at={ua}",
                    flush=True,
                )

            if _is_terminal(st):
                elapsed = time.time() - t_start
                r2 = m.get("r2_url")
                fu = m.get("funnel_url")
                ferr = m.get("funnel_error")
                err = m.get("error")
                lt = m.get("log_tail") or ""

                print()
                print("=" * 60)
                print("FINAL SUMMARY — run_full_auto_test")
                print("=" * 60)
                print("job_id:        ", job_id)
                print("final status:  ", st)
                print("r2_url:        ", r2)
                print("funnel_url:    ", fu)
                print("funnel_error:  ", ferr)
                print("error field:   ", err)
                if lt:
                    tail = lt[-2000:] if len(lt) > 2000 else lt
                    print("log_tail:      ", tail)
                print("total time:    ", f"{elapsed:.1f}s ({elapsed / 60.0:.2f} min)")
                print("=" * 60)

                if _is_success_terminal(st):
                    return 0
                if st == "DEAD_LETTER":
                    return 4
                if st == "FAILED":
                    return 6
                return 7

            time.sleep(args.poll_sec)

        # Timeout
        elapsed = time.time() - t_start
        try:
            m = read_manifest(str(job_id))
        except Exception:
            m = {}
        st = (m.get("status") or "").strip()
        print("\nTIMEOUT waiting for terminal status", file=sys.stderr)
        print(
            json.dumps(
                {
                    "job_id": job_id,
                    "last_status": st,
                    "timeout_sec": args.timeout_sec,
                    "elapsed_sec": elapsed,
                },
                indent=2,
            )[:4000],
            file=sys.stderr,
        )
        return 5

    finally:
        if not args.keep_services:
            _stop_proc(worker_proc, "worker")
            _stop_proc(hitl_proc, "hitl")


if __name__ == "__main__":
    raise SystemExit(main())
