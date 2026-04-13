#!/usr/bin/env python3
"""
End-to-end test: enqueue affiliate job via HITL API, poll manifest until terminal state, print r2_url + funnel_url.

**Start these first:** HITL API (port 8080), `klip-avatar/worker.py`, and Redis (see `.env`).
This script only POSTs jobs and polls — it does not start the pipeline.

Requires: hitl_server on BASE_URL (default http://127.0.0.1:8080), worker consuming Redis.

Optimized one-command test (Roborock product, default avatar; after PYTHONPATH + HITL + worker):

  python scripts/test_full_pipeline.py --progress-sec 15

Default product URL is a short Amazon link (Roborock). Override with --product-url or env KLIP_TEST_PRODUCT_URL.

Usage:
  python scripts/test_full_pipeline.py
  python scripts/test_full_pipeline.py --timeout-sec 3600 --progress-sec 15
  python scripts/test_full_pipeline.py --product-url "https://www.amazon.com/..."
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

# Default smoke product: short link (resolves to Amazon). Override: --product-url or KLIP_TEST_PRODUCT_URL.
_DEFAULT_TEST_PRODUCT = "https://amzn.to/4cewwZo"


def main() -> int:
    p = argparse.ArgumentParser(description="POST /api/jobs and poll manifest until HITL_PENDING or DEAD_LETTER")
    p.add_argument(
        "--base-url",
        default=(os.environ.get("KLIP_MC_BASE_URL") or "http://127.0.0.1:8080").rstrip("/"),
        help="Mission Control (hitl_server) base URL",
    )
    p.add_argument(
        "--product-url",
        default=(os.environ.get("KLIP_TEST_PRODUCT_URL") or _DEFAULT_TEST_PRODUCT).strip(),
        help="Amazon product URL (default: Roborock short link or KLIP_TEST_PRODUCT_URL)",
    )
    p.add_argument("--avatar-id", default=os.environ.get("ACTIVE_AVATAR_ID", ""))
    p.add_argument("--affiliate-program-id", default="example_program")
    p.add_argument("--timeout-sec", type=int, default=5400, help="Max wait for terminal status (default 90m)")
    p.add_argument("--poll-sec", type=float, default=8.0, help="Sleep between manifest reads")
    p.add_argument(
        "--progress-sec",
        type=float,
        default=15.0,
        help="Print live progress every N seconds while waiting (0=disable)",
    )
    p.add_argument(
        "--generate-funnel",
        default=True,
        action=argparse.BooleanOptionalAction,
        help="Request funnel generation (default: true; use --no-generate-funnel to skip)",
    )
    args = p.parse_args()

    body = {
        "product_url": args.product_url,
        "avatar_id": args.avatar_id,
        "affiliate_program_id": args.affiliate_program_id,
        "affiliate_fields": {"product_id": "e2e-test", "affiliate_tag": "test-tag"},
        "layout_mode": "affiliate_split_55_45",
        "generate_funnel": bool(args.generate_funnel),
    }
    url = f"{args.base_url}/api/jobs"
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
    print("POST /api/jobs:", json.dumps(out, indent=2))
    if not job_id:
        return 3

    repo = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    if repo not in sys.path:
        sys.path.insert(0, repo)
    from infrastructure.job_state import read_manifest

    deadline = time.time() + max(60, args.timeout_sec)
    t_start = time.time()
    last_st = None
    next_progress = time.time() + (args.progress_sec if args.progress_sec > 0 else 1e9)

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
            next_progress = time.time() + args.progress_sec
            ps = m.get("pipeline_stage") or "—"
            pd = (m.get("pipeline_detail") or "")[:160]
            ua = m.get("updated_at") or "—"
            print(
                f"[progress] t+{int(time.time() - t_start)}s "
                f"status={st} stage={ps} updated_at={ua}",
                flush=True,
            )
            if pd:
                print(f"           detail: {pd}", flush=True)

        if st == "HITL_PENDING":
            elapsed = time.time() - t_start
            r2 = m.get("r2_url")
            fu = m.get("funnel_url")
            print()
            print("SUCCESS: Full pipeline completed")
            print(f"Total time: {elapsed:.1f}s ({elapsed / 60.0:.2f} min)")
            print("Video URL (r2_url):", r2)
            print("Local final_video_path:", m.get("final_video_path"))
            print("Funnel URL:", fu)
            print("funnel_error:", m.get("funnel_error"))
            return 0
        if st == "DEAD_LETTER":
            print("\nFAILED — DEAD_LETTER")
            print("error:", m.get("error"))
            print("log_tail:", (m.get("log_tail") or "")[-2000:])
            return 4
        if st == "FAILED":
            print("\nFAILED — FAILED (e.g. subprocess timeout)")
            print("error:", m.get("error"))
            print("log_tail:", (m.get("log_tail") or "")[-2000:])
            return 6
        time.sleep(args.poll_sec)

    print("\nTIMEOUT waiting for HITL_PENDING / DEAD_LETTER / FAILED", file=sys.stderr)
    try:
        m = read_manifest(str(job_id))
        print(json.dumps(m, indent=2)[:4000])
    except Exception:
        pass
    return 5


if __name__ == "__main__":
    raise SystemExit(main())
