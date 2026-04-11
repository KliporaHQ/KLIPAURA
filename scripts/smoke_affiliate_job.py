#!/usr/bin/env python3
"""
Phase 2/3 smoke: enqueue one job via Mission Control API; show manifest (affiliate, layout, avatar, funnel flags).

Requires: uvicorn hitl_server on BASE_URL (default http://127.0.0.1:8080), Redis configured.

Usage:
  python scripts/smoke_affiliate_job.py
  python scripts/smoke_affiliate_job.py --base-url http://127.0.0.1:8080
  python scripts/smoke_affiliate_job.py --generate-funnel --check-dashboard --open-funnel
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request


def main() -> int:
    p = argparse.ArgumentParser(description="POST /api/jobs with affiliate + 55/45 layout smoke fields")
    p.add_argument(
        "--base-url",
        default=(__import__("os").environ.get("KLIP_MC_BASE_URL") or "http://127.0.0.1:8080").rstrip("/"),
        help="Mission Control (hitl_server) base URL",
    )
    p.add_argument(
        "--product-url",
        default="https://example.com/product/sample-item-123",
        help="Sample product page URL (HTTPS)",
    )
    p.add_argument("--avatar-id", default="theanikaglow", help="Must exist under core_v1/data/avatars/ for full pipeline")
    p.add_argument(
        "--generate-funnel",
        action="store_true",
        help="Set generate_funnel on the job (worker builds HTML after video; needs R2 or local jobs/)",
    )
    p.add_argument(
        "--post-generate-funnel",
        action="store_true",
        help="After enqueue, call POST /api/jobs/{id}/generate-funnel (manual funnel without waiting for worker)",
    )
    p.add_argument(
        "--open-funnel",
        action="store_true",
        help="If manifest has an http(s) funnel_url, open it in the default browser (Phase 4 E2E check).",
    )
    p.add_argument(
        "--check-dashboard",
        action="store_true",
        help="GET /api/dashboard/recent-jobs and print whether this job_id appears with video/funnel fields.",
    )
    args = p.parse_args()

    body = {
        "product_url": args.product_url,
        "avatar_id": args.avatar_id,
        "affiliate_program_id": "example_program",
        "affiliate_fields": {
            "product_id": "sample-item-123",
            "affiliate_tag": "demo-tag-01",
        },
        "layout_mode": "affiliate_split_55_45",
    }
    if args.generate_funnel:
        body["generate_funnel"] = True
    url = f"{args.base_url}/api/jobs"
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        print("HTTP", e.code, e.read().decode("utf-8", "replace")[:800], file=sys.stderr)
        return 1
    except urllib.error.URLError as e:
        print("Request failed:", e, file=sys.stderr)
        print("Start API: python -m uvicorn hitl_server:app --app-dir klip-dispatch --host 127.0.0.1 --port 8080", file=sys.stderr)
        return 2

    out = json.loads(raw)
    job_id = out.get("job_id")
    print("POST /api/jobs:", json.dumps(out, indent=2))

    if not job_id:
        return 3

    murl = f"{args.base_url}/api/jobs/{job_id}/manifest"
    with urllib.request.urlopen(murl, timeout=15) as r2:
        man = json.loads(r2.read().decode("utf-8"))
    payload = man.get("payload") or {}
    print("\nManifest payload (affiliate + layout + avatar + funnel):")
    print("  avatar_id:", payload.get("avatar_id"))
    print("  generate_funnel:", payload.get("generate_funnel"))
    print("  layout_mode:", payload.get("layout_mode"))
    print("  affiliate_data:", json.dumps(payload.get("affiliate_data"), indent=2))
    funnel_url = man.get("funnel_url")
    print("  funnel_url (manifest top-level):", funnel_url)

    if args.check_dashboard:
        dash_url = f"{args.base_url}/api/dashboard/recent-jobs?limit=50"
        try:
            with urllib.request.urlopen(dash_url, timeout=15) as dr:
                dj = json.loads(dr.read().decode("utf-8"))
        except urllib.error.URLError as e:
            print("\nGET /api/dashboard/recent-jobs failed:", e, file=sys.stderr)
        else:
            jobs = dj.get("jobs") if isinstance(dj, dict) else None
            row = None
            if isinstance(jobs, list):
                for x in jobs:
                    if isinstance(x, dict) and str(x.get("job_id")) == str(job_id):
                        row = x
                        break
            print("\nDashboard row for this job:", json.dumps(row, indent=2) if row else "(not in recent list yet)")

    if args.open_funnel and isinstance(funnel_url, str) and funnel_url.startswith("http"):
        import webbrowser

        webbrowser.open(funnel_url)
        print("\nOpened funnel URL in browser.")

    ad = payload.get("affiliate_data")
    if isinstance(ad, dict) and ad.get("affiliate_link"):
        print("\nOK: affiliate_link resolved from config/affiliate_programs.json + affiliate_fields.")
    else:
        print("\nNote: affiliate_data may be empty if program_id unknown or link_template unresolved.", file=sys.stderr)

    print(
        "\nWorker expectation: KLIP_AFFILIATE_DATA, KLIP_LAYOUT_MODE=affiliate_split_55_45, "
        "AFFILIATE_SPLIT_TOP_RATIO=0.55, pipeline uses 55/45 split in video-render engine."
    )

    if args.post_generate_funnel:
        fu = f"{args.base_url}/api/jobs/{job_id}/generate-funnel"
        req2 = urllib.request.Request(fu, data=b"{}", headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req2, timeout=60) as resp:
                print("\nPOST generate-funnel:", resp.read().decode("utf-8", "replace")[:2000])
        except urllib.error.HTTPError as e:
            print("\ngenerate-funnel HTTP", e.code, e.read().decode("utf-8", "replace")[:800], file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
