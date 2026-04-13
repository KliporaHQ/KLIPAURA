#!/usr/bin/env python3
"""
Phase 7 — End-to-end validation script.

Runs all 10 validation steps from the KLIPAURA Global System Build Plan.
Steps 1-6 can run without a live worker; Step 7+ require the worker running.

Usage (from repo root):
    python scripts/validate_phase7.py
    python scripts/validate_phase7.py --skip-worker-steps   # steps 1-6 only
    python scripts/validate_phase7.py --step 2              # single step
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time
from pathlib import Path

# ── Windows UTF-8 output fix ──────────────────────────────────────────────────
# Prevents UnicodeEncodeError on cp1252 terminals when printing box-drawing/emoji.
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

_REPO = Path(__file__).resolve().parents[1]
_SCANNER = _REPO / "klip-scanner"
for _p in [str(_REPO), str(_SCANNER)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    from dotenv import load_dotenv, find_dotenv
    load_dotenv(find_dotenv(usecwd=False), override=False)
except ImportError:
    pass

PASS = "✅ PASS"
FAIL = "❌ FAIL"
SKIP = "⏭  SKIP"

results: list[tuple[int, str, str]] = []


def step(n: int, description: str):
    def decorator(fn):
        fn._step_n = n
        fn._step_desc = description
        return fn
    return decorator


def run_step(n: int, fn, *args, **kwargs) -> bool:
    print(f"\n{'─'*60}", flush=True)
    print(f"Step {n}: {fn._step_desc}", flush=True)
    try:
        fn(*args, **kwargs)
        results.append((n, fn._step_desc, PASS))
        print(f"  → {PASS}", flush=True)
        return True
    except AssertionError as exc:
        results.append((n, fn._step_desc, f"{FAIL}: {exc}"))
        print(f"  → {FAIL}: {exc}", flush=True)
        return False
    except Exception as exc:
        results.append((n, fn._step_desc, f"{FAIL}: {type(exc).__name__}: {exc}"))
        print(f"  → {FAIL}: {type(exc).__name__}: {exc}", flush=True)
        return False


# ── Step 1 — Environment & AvatarLoader ──────────────────────────────────────

@step(1, "Environment loaded + AvatarLoader sees active avatar")
def step1():
    groq = os.getenv("GROQ_API_KEY")
    assert groq, "GROQ_API_KEY not set in .env"
    elevenlabs = os.getenv("ELEVENLABS_API_KEY")
    assert elevenlabs, "ELEVENLABS_API_KEY not set in .env"

    from infrastructure.avatar_loader import AvatarLoader
    loader = AvatarLoader()
    report = loader.validate_all()
    active = loader.list_active()
    assert active, f"No active avatars. Report: {report}"
    print(f"  Active avatars: {[a['avatar_id'] for a in active]}", flush=True)


# ── Step 2 — ProductPassport creation + validation ────────────────────────────

@step(2, "ProductPassport.new() + is_valid() roundtrip")
def step2():
    from infrastructure.product_passport import ProductPassport
    pp = ProductPassport.new(
        network="manual",
        title="Roborock Q10 VF+ Robot Vacuum",
        images=[
            "https://m.media-amazon.com/images/I/71QXnkFjhEL.jpg",
            "https://m.media-amazon.com/images/I/71xyz.jpg",
            "https://m.media-amazon.com/images/I/81abc.jpg",
        ],
        price="AED 1,299",
        description="Robot vacuum cleaner with auto-empty dock.",
        affiliate_url="https://amzn.to/4cewwZo",
        commission_rate=4.5,
        score=75.0,
        avatar_id="",
        video_format="SplitFormat",
        category="home",
    )
    valid, reason = pp.is_valid()
    assert valid, f"Passport invalid: {reason}"
    assert pp.passport_id.startswith("pp-"), f"Bad passport_id: {pp.passport_id}"
    # Roundtrip
    restored = ProductPassport.from_json(pp.to_json())
    assert restored.passport_id == pp.passport_id
    assert restored.title == pp.title
    print(f"  passport_id: {pp.passport_id}", flush=True)


# ── Step 3 — Redis connectivity ───────────────────────────────────────────────

@step(3, "Redis ping + ProductPassport save/load roundtrip")
def step3():
    from infrastructure.redis_client import get_redis_client, RedisConfigError
    try:
        r = get_redis_client()
    except RedisConfigError as exc:
        raise AssertionError(f"Redis not configured: {exc}")

    ok = r.ping()
    assert ok, "Redis ping failed"

    from infrastructure.product_passport import ProductPassport
    pp = ProductPassport.new(
        network="manual",
        title="Phase7 Test Product",
        images=["https://example.com/a.jpg", "https://example.com/b.jpg", "https://example.com/c.jpg"],
        price="AED 99",
        description="Test",
        affiliate_url="https://example.com/aff",
        commission_rate=5.0,
        score=70.0,
        avatar_id="",
        video_format="SplitFormat",
        category="home",
    )
    pp.save(r, ttl_seconds=300)
    loaded = ProductPassport.load(r, pp.passport_id)
    assert loaded is not None, "ProductPassport.load() returned None"
    assert loaded.title == pp.title
    print(f"  Redis OK — passport {pp.passport_id} saved and loaded", flush=True)


# ── Step 4 — Selector scoring + format routing ────────────────────────────────

@step(4, "Opportunity scoring + format routing")
def step4():
    sys.path.insert(0, str(_REPO / "klip-selector"))
    from scoring.opportunity_engine import score_product, is_above_threshold
    from routing.format_router import format_for_product

    score = score_product(
        commission_rate=4.5,
        trend_score=0.7,
        category="beauty",
        price="AED 299",
    )
    assert 0 < score <= 100, f"Score out of range: {score}"
    print(f"  Score: {score:.1f}", flush=True)

    fmt = format_for_product("fitness", "amazon")
    assert fmt in ("LipsyncFormat", "SplitFormat", "FullscreenFormat",
                   "StaticNarrationFormat", "TextForwardFormat"), f"Unknown format: {fmt}"
    print(f"  Format for fitness/amazon: {fmt}", flush=True)


# ── Step 5 — push_passport queue push ─────────────────────────────────────────

@step(5, "push_passport queues a product (live Redis)")
def step5():
    sys.path.insert(0, str(_REPO / "klip-selector"))
    from infrastructure.redis_client import get_redis_client, RedisConfigError
    from infrastructure.queue_names import JOBS_PENDING
    from infrastructure.product_passport import ProductPassport
    from publisher import push_passport

    try:
        r = get_redis_client()
    except RedisConfigError as exc:
        raise AssertionError(f"Redis not configured: {exc}")

    before = r.llen(JOBS_PENDING)

    pp = ProductPassport.new(
        network="manual",
        title="Phase7 Queue Test Product",
        images=["https://example.com/1.jpg", "https://example.com/2.jpg", "https://example.com/3.jpg"],
        price="AED 149",
        description="Validation test product",
        affiliate_url="https://example.com/aff/phase7test",
        commission_rate=5.0,
        score=72.0,
        avatar_id="",
        video_format="SplitFormat",
        category="home",
        status="queued",
    )

    ok, result = push_passport(pp, r, skip_score_check=True)
    assert ok, f"push_passport failed: {result}"

    after = r.llen(JOBS_PENDING)
    assert after == before + 1, f"Queue depth didn't increase: before={before} after={after}"
    print(f"  Queued job={result} passport={pp.passport_id} queue_depth={after}", flush=True)


# ── Step 6 — FormatEngine registry ───────────────────────────────────────────

@step(6, "FormatEngine FORMAT_REGISTRY contains all 10 formats")
def step6():
    sys.path.insert(0, str(_REPO / "klip-avatar" / "core_v1"))
    from pipeline.format_engine import FORMAT_REGISTRY, FALLBACK_CHAIN

    expected = {
        "SplitFormat", "LipsyncFormat", "FullscreenFormat", "TextForwardFormat",
        "StaticNarrationFormat", "BeforeAfterFormat", "ComparisonFormat",
        "CountdownFormat", "DemoSequenceFormat", "HookRevealFormat",
    }
    missing = expected - set(FORMAT_REGISTRY.keys())
    assert not missing, f"Missing formats: {missing}"
    assert len(FALLBACK_CHAIN) == 4, f"Expected 4-step fallback chain, got {len(FALLBACK_CHAIN)}"
    print(f"  Formats: {sorted(FORMAT_REGISTRY.keys())}", flush=True)
    print(f"  Fallback chain: {[f.name for f in FALLBACK_CHAIN]}", flush=True)


# ── Step 7 — ugc_pipeline --help shows new args ───────────────────────────────

@step(7, "ugc_pipeline accepts --passport-file and --video-format args")
def step7():
    import subprocess
    result = subprocess.run(
        [sys.executable, "-m", "pipeline.ugc_pipeline", "--help"],
        cwd=str(_REPO / "klip-avatar" / "core_v1"),
        capture_output=True,
        text=True,
        timeout=30,
        env={**os.environ, "PYTHONPATH": str(_REPO / "klip-avatar" / "core_v1")},
    )
    output = result.stdout + result.stderr
    assert "--passport-file" in output, "--passport-file arg not found in --help output"
    assert "--video-format" in output, "--video-format arg not found in --help output"
    print(f"  --passport-file: ✓   --video-format: ✓", flush=True)


# ── Step 8 — Avatar pause/resume via API ──────────────────────────────────────

@step(8, "AvatarLoader.update_status() pause/resume cycle")
def step8():
    from infrastructure.avatar_loader import AvatarLoader
    loader = AvatarLoader()

    active_before = loader.list_active()
    assert active_before, "No active avatars to test with"
    avatar_id = active_before[0]["avatar_id"]

    # Pause
    ok = loader.update_status(avatar_id, "paused")
    assert ok, f"update_status('paused') returned False for {avatar_id}"

    # Verify paused
    loader2 = AvatarLoader()  # fresh instance, no cache
    active_after_pause = loader2.list_active()
    assert not any(a["avatar_id"] == avatar_id for a in active_after_pause), \
        f"{avatar_id} still active after pause"

    # Resume
    ok = loader2.update_status(avatar_id, "active")
    assert ok, f"update_status('active') returned False for {avatar_id}"

    loader3 = AvatarLoader()
    active_after_resume = loader3.list_active()
    assert any(a["avatar_id"] == avatar_id for a in active_after_resume), \
        f"{avatar_id} not active after resume"

    print(f"  {avatar_id}: paused → resumed ✓", flush=True)


# ── Step 9 — Telegram send_telegram (non-fatal if not configured) ─────────────

@step(9, "Telegram send_telegram (informational — skips if no token)")
def step9():
    from infrastructure.telegram_notify import send_telegram
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    if not token:
        print("  TELEGRAM_BOT_TOKEN not set — skipping live send (non-fatal)", flush=True)
        return
    ok = send_telegram("🧪 KLIPAURA Phase 7 validation ping — system OK")
    # Non-fatal: Telegram could be rate-limited
    print(f"  Telegram send result: {'OK' if ok else 'FAILED (non-fatal)'}", flush=True)


# ── Step 10 — R2 storage config check ────────────────────────────────────────

@step(10, "R2 storage configuration detected")
def step10():
    from infrastructure.storage import r2_configured
    configured = r2_configured()
    if not configured:
        print("  R2 not configured (R2_BUCKET / R2_ACCESS_KEY / R2_SECRET_ACCESS_KEY missing)", flush=True)
        print("  → This is OK for local dev; required for Railway deployment", flush=True)
    else:
        print("  R2 configured ✓", flush=True)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Phase 7 end-to-end validation")
    parser.add_argument("--skip-worker-steps", action="store_true",
                        help="Skip steps that require a live worker")
    parser.add_argument("--step", type=int, default=0,
                        help="Run only this step number")
    args = parser.parse_args()

    all_steps = [
        (1, step1), (2, step2), (3, step3), (4, step4), (5, step5),
        (6, step6), (7, step7), (8, step8), (9, step9), (10, step10),
    ]

    print("=" * 60)
    print("KLIPAURA Phase 7 — End-to-End Validation")
    print("=" * 60)

    passed = 0
    failed = 0
    for n, fn in all_steps:
        if args.step and n != args.step:
            continue
        success = run_step(n, fn)
        if success:
            passed += 1
        else:
            failed += 1

    print("\n" + "=" * 60)
    print(f"Results: {passed} passed, {failed} failed")
    for n, desc, status in results:
        print(f"  Step {n:2d}: {status}  {desc}")
    print("=" * 60)

    raise SystemExit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
