#!/usr/bin/env python3
"""
Start HITL (if needed), start worker, then run scripts/test_full_pipeline.py end-to-end.

Intended for Cursor / automation: one command instead of three manual terminals.

Usage:
  python scripts/run_full_test.py
  python scripts/run_full_test.py --keep-services
  python scripts/run_full_test.py --skip-start-hitl
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse


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
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
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
    """Return (host, port) for uvicorn if base is local HTTP we can spawn; else None (remote / HTTPS)."""
    u = urlparse(base)
    if u.scheme != "http":
        return None
    h = (u.hostname or "").lower()
    if h not in ("127.0.0.1", "localhost", "::1"):
        return None
    port = u.port if u.port is not None else 80
    bind_host = "127.0.0.1" if h != "::1" else "::1"
    return (bind_host, port)


def main() -> int:
    ap = argparse.ArgumentParser(description="Automated HITL + worker + full pipeline test")
    ap.add_argument(
        "--base-url",
        default=os.environ.get("KLIP_MC_BASE_URL") or "http://127.0.0.1:8080",
        help="HITL base URL",
    )
    ap.add_argument("--skip-start-hitl", action="store_true", help="Do not spawn HITL (assume already up)")
    ap.add_argument("--skip-start-worker", action="store_true", help="Do not spawn worker")
    ap.add_argument(
        "--keep-services",
        action="store_true",
        help="Leave HITL/worker running after test (only processes started by this script)",
    )
    ap.add_argument("--ready-sleep", type=float, default=6.0, help="Seconds to wait after worker start")
    ap.add_argument("--progress-sec", type=float, default=15.0, help="Passed to test_full_pipeline.py")
    args, passthrough = ap.parse_known_args()

    repo = _repo_root()
    os.chdir(repo)
    env = _pythonpath_env(repo)

    base = args.base_url.rstrip("/")
    hitl_proc: subprocess.Popen[str] | None = None
    worker_proc: subprocess.Popen[str] | None = None
    log_dir = repo / "outputs" / "run_full_test_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")

    def _stop(proc: subprocess.Popen[str] | None, name: str) -> None:
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
            print(f"[run_full_test] warning stopping {name}: {e}", file=sys.stderr)

    try:
        if not args.skip_start_hitl:
            if _hitl_openapi_ok(base):
                print("[run_full_test] HITL already responding on", base, flush=True)
            else:
                listen = _local_http_listen(base)
                if listen is None:
                    print(
                        "[run_full_test] ERROR: HITL is not up and auto-start only supports "
                        "local http://127.0.0.1:<port> (or localhost). Start HITL manually or use --skip-start-hitl.",
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
                print("[run_full_test] Starting HITL:", " ".join(hitl_cmd), flush=True)
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
                        "[run_full_test] ERROR: HITL did not become ready on",
                        base,
                        "(see",
                        hitl_log.name,
                        ")",
                        file=sys.stderr,
                    )
                    return 10
                print("[run_full_test] HITL is ready.", flush=True)

        if not args.skip_start_worker:
            worker_log = open(log_dir / f"worker-{stamp}.log", "w", encoding="utf-8", errors="replace")
            worker_cmd = [sys.executable, str(repo / "klip-avatar" / "worker.py")]
            print("[run_full_test] Starting worker:", " ".join(worker_cmd), flush=True)
            worker_proc = subprocess.Popen(
                worker_cmd,
                cwd=repo,
                env=env,
                stdout=worker_log,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
            time.sleep(max(0.0, args.ready_sleep))
            print("[run_full_test] Worker start delay done. Logs:", worker_log.name, flush=True)

        test_script = repo / "scripts" / "test_full_pipeline.py"
        test_cmd = [
            sys.executable,
            str(test_script),
            "--base-url",
            base,
            "--product-url",
            "https://amzn.to/4cewwZo",
            "--avatar-id",
            os.environ.get("ACTIVE_AVATAR_ID", ""),
            "--generate-funnel",
            "--progress-sec",
            str(args.progress_sec),
        ]
        test_cmd.extend(passthrough)
        print("[run_full_test] Running:", " ".join(test_cmd), flush=True)
        rc = subprocess.call(test_cmd, cwd=repo, env=env)
        return int(rc)
    finally:
        if not args.keep_services:
            _stop(worker_proc, "worker")
            _stop(hitl_proc, "hitl")


if __name__ == "__main__":
    raise SystemExit(main())
