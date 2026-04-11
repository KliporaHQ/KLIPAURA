# KLIPAURA — MASTER TASK TRACKER

> **How this works:** At the start of every Cursor session, paste the prompt from `SESSION_START.md`.
> The AI will read this file, find the first incomplete task, and continue from there.
> After completing a task, the AI updates the checkbox `[ ]` → `[x]` and adds a result note.
>
> **CTO (technical lead):** Cursor agent — prioritizes **time-to-revenue**, shipping order, and production hardening.
>
> **Current Avatar:** `@theanikaglow` (DO NOT spawn next avatar until $1K P&L confirmed)
> **Current Phase:** Phases 1–2 in production; Phase 3–5 wired in Mission Control + trader hooks — remaining: manual gates (3.8, 4.8, 5.7), credentials (`social_config.json`, `OPS_API_KEY` on Railway), optional third Railway service (`railway.scheduler.toml` + `AUTOPILOT_MODE=1`).

---

## CTO — INCOME FAST PATH (do this before Phase 3+)

**Goal:** First affiliate commission in the shortest time. Automation is secondary to proof of revenue.

| Priority | Action | Why |
|----------|--------|-----|
| P0 | Keep pipeline **green** (`FINAL_VIDEO.mp4`) | No video → no link, no commission |
| P1 | **HITL approve** → `revenue.jsonl` line always (manual or auto) | Ledger + accountability |
| P2 | **Public video URL** for auto-post (`R2_*` env + upload in worker) | Zernio API requires HTTPS URL |
| P3 | `social_config.json`: `getlate_api_key` + `tiktok_account_id` (from Zernio dashboard) | **Auto** TikTok via `POST https://zernio.com/api/v1/posts` |
| P4 | If P2–P3 skip: **manual** post to TikTok with product link in bio + track in Associates | Still valid income path |
| P5 | Deploy **API + worker** to Railway (2.7) when local loop is proven | Scale without your laptop |

**Outcomes (what “done” looks like):**

1. **Minimum viable income:** Video approved → ledger entry → you post manually (status `MANUAL_PUBLISH_REQUIRED`) → first sale logged in Amazon Associates.
2. **Automated:** Same + Zernio returns `200/201` → status `PUBLISHED` + `post_url` in `revenue.jsonl` detail.
3. **Scale:** Phase 3 selector + Phase 4 scheduler only after (1) or (2) is repeatable weekly.

---

## PHASE 0 — CLEAN WORKSPACE
**Goal:** Single repo. Verified pipeline runs. No legacy imports.
**Validation Gate:** `python -m pipeline.ugc_pipeline` with a test URL produces `FINAL_VIDEO.mp4`

| # | Task | Status | Result / Notes |
|---|------|--------|----------------|
| 0.1 | Copy `klip-avatar/core_v1/` into repo | `[x]` | Done. |
| 0.2 | Extract `infrastructure/storage.py` | `[x]` | Added `infrastructure/storage.py` (R2/S3 store). |
| 0.3 | Extract `infrastructure/redis_client.py` | `[x]` | Added `infrastructure/redis_client.py`. |
| 0.4 | Extract `klip-dispatch/publisher.py` skeleton | `[x]` | Added `klip-dispatch/publisher.py`. |
| 0.5 | Fix `_load_phase2()` importlib anti-pattern in `klip-avatar/core_v1/pipeline/ugc_pipeline.py` → replace with direct imports | `[x]` | Direct imports from `first_affiliate_phase2_output`; removed `_load_phase2()`. |
| 0.6 | Update `path_bootstrap.py` to add `scripts/` and `services/` to sys.path | `[x]` | `path_bootstrap.py` inserts `core_v1/scripts` and `core_v1/services`. |
| 0.7 | Create `.env` with all required keys (never commit) | `[x]` | Root `.env` and `klip-avatar/core_v1/.env` in sync. |
| 0.8 | **GATE:** Run `python -m pipeline.ugc_pipeline` with a test product URL. Confirm `FINAL_VIDEO.mp4` produced. | `[x]` | **PASS** (exit 0). `klip-avatar/core_v1/outputs/final_publish/FINAL_VIDEO.mp4` (~9.8 MB). Run hit WaveSpeed credit/cap warnings (Ken Burns fallbacks); ship gate still passed. |

---

## PHASE 1 — CLOSE THE REVENUE LOOP
**Goal:** Full manual loop: product URL → video → HITL approve → TikTok post → revenue logged.
**Validation Gate:** Manually run pipeline → HITL shows video → APPROVE → TikTok posts → `revenue.jsonl` has 1 entry.

### 1A — Job Filesystem + R2 Upload

| # | Task | Status | Result / Notes |
|---|------|--------|----------------|
| 1A.1 | Create `infrastructure/job_state.py` with `create_manifest`, `update_manifest`, `read_manifest` | `[x]` | `infrastructure/job_state.py`; `JOBS_DIR` defaults to `<repo>/jobs`. |
| 1A.2 | Create `infrastructure/storage.py` for Cloudflare R2 (boto3, S3-compatible) | `[x]` | Phase 0 (same file). |
| 1A.3 | Add R2 upload + Redis push to end of `ugc_pipeline.py` `main()` after ship gate pass | `[~]` | R2 upload + HITL Redis push implemented in **`klip-avatar/worker.py`** after successful pipeline (not yet in `ugc_pipeline.py` inline). |
| 1A.4 | Confirm R2 and Redis are optional (skip gracefully if env vars missing) | `[x]` | Worker skips R2 if unset; API returns 503 without Redis. |

### 1B — HITL Server

| # | Task | Status | Result / Notes |
|---|------|--------|----------------|
| 1B.1 | Create `klip-dispatch/hitl_server.py` (FastAPI) | `[x]` | `uvicorn hitl_server:app --app-dir klip-dispatch`. |
| 1B.2 | `GET /` → Mission Control + HITL: ops snapshot (`GET /api/ops/summary`), 9:16 player, vanilla JS | `[x]` | Queues, revenue rollup, `MISSION_CONTROL_URL` + `PUBLIC_SITE_URL` links |
| 1B.3 | `GET /api/next-job` → pops from `klip:hitl:pending`, returns job details + R2 URL | `[x]` | JSON includes `r2_url` and `final_video_path`; video served from `/api/jobs/{id}/video`. |
| 1B.4 | `POST /api/approve/{job_id}` → update manifest → call `publisher.publish_job()` | `[x]` | `publish_job()` → Zernio when public HTTPS video + `social_config.json`; else manual path + `revenue.jsonl`. |
| 1B.5 | `POST /api/reject/{job_id}` → update manifest REJECTED → blacklist product 7d in Redis | `[x]` | `SETEX` `klip:blacklist:{sha256(url)}` 7d. |
| 1B.6 | `POST /api/regenerate/{job_id}` → re-push original payload to `klip:jobs:pending` | `[x]` | New `job_id` per regenerate. |
| 1B.7 | Test HITL server locally — confirm video plays and approve/reject work | `[x]` | **Verified 2026-04-02:** `uvicorn` + worker; `POST /api/jobs` → pipeline → HITL; `/api/next-job` + `/api/approve` OK; WaveSpeed I2V hit credit limits, Ken Burns fallback still shipped |

### 1C — Publisher + Revenue Tracker

| # | Task | Status | Result / Notes |
|---|------|--------|----------------|
| 1C.1 | Create `klip-dispatch/publisher.py` — wraps GetLate/Zeroino API | `[x]` | `publish_job()` → Zernio `POST /v1/posts` when `r2_url` + `getlate_api_key` + `tiktok_account_id`; else **manual** path + ledger line |
| 1C.2 | Publisher reads credentials from `data/avatars/{avatar_id}/social_config.json` ONLY — never from `.env` | `[x]` | Keys: `getlate_api_key`, `getlate_base_url` (default `https://zernio.com/api`), `tiktok_account_id` |
| 1C.3 | Create `data/avatars/theanikaglow/social_config.json` with real keys | `[~]` | **Template:** `social_config.example.json` — copy fields into your real `social_config.json` (not committed). |
| 1C.4 | Create `klip-dispatch/revenue_tracker.py` — `log()` and `get_summary()` appending to `revenue.jsonl` | `[x]` | Root `revenue.jsonl` gitignored; `check_spawn_milestone()` helper |
| 1C.5 | **GATE:** Full manual test: product URL → video → HITL approve → post → check `revenue.jsonl` | `[~]` | **2026-04-02:** End-to-end local run → ledger line in `revenue.jsonl`; publish **manual** until `social_config.json` has Zernio/TikTok — then re-run approve path for auto-post |

---

## PHASE 2 — QUEUE WORKER
**Goal:** `POST /api/jobs` triggers the pipeline. No terminal needed.
**Validation Gate:** POST a product URL → 5 min later video appears in HITL automatically.

| # | Task | Status | Result / Notes |
|---|------|--------|----------------|
| 2.1 | Create `klip-avatar/worker.py` — Upstash Redis BLPOP consumer | `[x]` | `infrastructure/queue_names.py` + `redis_client.blpop`. |
| 2.2 | Worker sets env vars, creates manifest, runs `pipeline.ugc_pipeline` as subprocess | `[x]` | `cwd=klip-avatar/core_v1`, `KLIP_PIPELINE_RUN=1`. |
| 2.3 | Worker retries up to 3× on failure (re-push with `retry_count++`) | `[x]` | Max 3 requeues after failure; then DLQ. |
| 2.4 | Worker pushes to `klip:dlq` after 3rd failure | `[x]` | |
| 2.5 | Add `POST /api/jobs` endpoint to `hitl_server.py` — accepts `{"product_url": "..."}`, pushes to `klip:jobs:pending` | `[x]` | Optional `avatar_id` (default `theanikaglow`). |
| 2.6 | Create `railway.toml` with `klip-api` and `klip-worker` services defined | `[x]` | `railway.toml` (API) + `railway.worker.toml` (worker). |
| 2.7 | Deploy worker + API to Railway | `[x]` | **2026-04-02:** Project `klipaura`, service `klip-api`, URL `https://klip-api-production.up.railway.app`. Single service runs `worker.py` + uvicorn (shared `/app/jobs`). |
| 2.8 | **GATE:** POST product URL → 5 min later video in HITL — zero terminal actions | `[~]` | **2026-04-02:** Railway live; enqueue `POST https://klip-api-production.up.railway.app/api/jobs` (no local terminals). Confirm end-to-end on cloud + WaveSpeed credits. |

---

## PHASE 3 — KLIP-SELECTOR (PRODUCT DISCOVERY)
**Goal:** Auto-score `products.csv` and enqueue jobs (on demand or via `scheduler.py`).
**Validation Gate:** `python -m klip_selector.selector_worker` → jobs in `klip:jobs:pending` → worker → HITL.

| # | Task | Status | Result / Notes |
|---|------|--------|----------------|
| 3.1 | Create `klip_selector/uae_filter.py` — compliance gate (keyword blocklist) | `[x]` | |
| 3.2 | Create `klip_selector/layout_router.py` — category → layout hint | `[x]` | |
| 3.3 | Create `klip_selector/scorer.py` — commission 60% + trend 40% + Redis sentiment boost | `[x]` | Reads `klip:market:sentiment` |
| 3.4 | Create `klip_selector/manual_feeder.py` — reads `products.csv` (default repo root) | `[x]` | |
| 3.5 | Create `products.csv` with 10 sample beauty/lifestyle products | `[x]` | |
| 3.6 | Create `klip_selector/selector_worker.py` — load → filter → score → push top N | `[x]` | `SELECTOR_LIMIT` (default 5) |
| 3.7 | Blacklist check — selector skips URLs; `POST /api/jobs` + regenerate reject blacklisted URLs | `[x]` | Redis `klip:blacklist:{sha256}` |
| 3.8 | **GATE:** Run selector → worker → 5 videos in HITL | `[ ]` | **Manual** |

---

## PHASE 4 — AUTOPILOT SCHEDULER
**Goal:** System runs twice daily, completely unattended.
**Validation Gate:** Set `AUTOPILOT_MODE=1`, leave 48h, return to 20 videos in HITL.

| # | Task | Status | Result / Notes |
|---|------|--------|----------------|
| 4.1 | Create `scheduler.py` at repo root using Python `schedule` library | `[x]` | |
| 4.2 | Morning cycle — UAE 06:00 → UTC time via `Asia/Dubai` conversion | `[x]` | Run host with `TZ=UTC` for predictable fires |
| 4.3 | Evening cycle — UAE 19:00 → UTC | `[x]` | `SELECTOR_UAE_*_HOUR` env overrides |
| 4.4 | Health check every 30 min via `infrastructure/system_guardian.py` | `[x]` | |
| 4.5 | `AUTOPILOT_MODE=0` → print schedule only, do not execute cycles | `[x]` | |
| 4.6 | Video budget gating | `[x]` | `scheduler_budget.py` + worker `bump_daily_video_count()` on HITL; scheduler + `POST /api/selector/run` gate on `video_budget_allows()`; `GET /api/autopilot/status` + ops snapshot |
| 4.7 | Add `klip-scheduler` Railway config | `[x]` | `railway.scheduler.toml` |
| 4.8 | **GATE:** `AUTOPILOT_MODE=1` → 48h unattended → 20 videos in HITL | `[ ]` | **Manual** |

---

## PHASE 5 — TRADER SIGNAL + SPAWN GATE
**Goal:** $1K P&L milestone fires spawn alert. KLIP-TRADER sentiment influences product scoring.
**Validation Gate:** $1K P&L reached → Telegram alert fires → spawn gate acknowledged.

| # | Task | Status | Result / Notes |
|---|------|--------|----------------|
| 5.1 | Create `klip_trader/signal_emitter.py` — writes `klip:market:sentiment` to Redis (TTL 300s) | `[x]` | `python -m klip_trader.signal_emitter` |
| 5.2 | Wire `signal_emitter` into trader execution loop | `[x]` | `klip_trader.hooks.emit_trader_sentiment(score)` for KLIP-TRADER; `python -m klip_trader.signal_emitter --loop`; `POST /api/market/sentiment` (with `X-Ops-Key`) |
| 5.3 | `klip_selector/scorer.py` reads market sentiment from Redis | `[x]` | |
| 5.4 | `revenue_tracker.check_spawn_milestone()` + ledger notify | `[x]` | Telegram once per avatar/threshold via flag file under `JOBS_DIR` |
| 5.5 | Telegram on spawn milestone | `[x]` | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `SPAWN_MILESTONE_USD` |
| 5.6 | `@kaelvancereview` stub | `[x]` | `klip-avatar/core_v1/data/avatars/kaelvancereview/social_config.example.json` (locked) |
| 5.7 | **GATE:** $1K in ledger → Telegram → spawn logged | `[ ]` | **Manual** |

---

## AVATAR ACTIVATION CHECKLIST

### @theanikaglow (ACTIVE)
- [x] Avatar created and configured
- [ ] GetLate/Zeroino account created (own account — not shared)
- [ ] TikTok profile live
- [ ] Instagram profile live
- [ ] YouTube channel live
- [ ] Amazon Associates tag registered
- [ ] `data/avatars/theanikaglow/social_config.json` populated with own credentials
- [ ] First video posted successfully

### @kaelvancereview (LOCKED — spawn at $1,000 P&L)
- [ ] **LOCKED** — do not activate until `@theanikaglow` earns $1,000

### @aria.vedaai (LOCKED — spawn at $2,000 P&L)
- [ ] **LOCKED** — do not activate until `@theanikaglow` earns $2,000

---

## REVENUE LEDGER SUMMARY

| Date | Avatar | Posts | Est. Revenue | Phase |
|------|--------|-------|-------------|-------|
| (Update manually or ask AI to read revenue.jsonl) | | | | |

---

## DECISIONS LOG

| Date | Decision | Reason |
|------|----------|--------|
| 2026-04-02 | Created KLIPAURA clean workspace | Phase 0 start |
| 2026-04-02 | Avatar isolation rule: each avatar gets own GetLate/Zeroino API and platform profiles | No cross-posting |
| 2026-04-02 | KLIP-TRADER stays testnet (`IS_TESTNET=true`) until affiliate revenue proven | Risk management |
| 2026-04-02 | n8n permanently archived — Python-native scheduling only | Previous n8n failures |
| 2026-04-02 | Phase 0 complete — infrastructure/, klip-dispatch/, path_bootstrap + ugc_pipeline import fix, gate green | Ready for Phase 1 (R2, HITL, publisher) |
| 2026-04-02 | Phase 2 code shipped — worker, HITL FastAPI, POST /api/jobs, railway.toml pair | Deploy + gate 2.7–2.8 manual |
| 2026-04-02 | **CTO takeover:** revenue-first; `revenue_tracker.py` + real `publish_job` (Zernio + manual fallback); approve writes ledger; fix reject blacklist `h` undefined; `social_config.example.json` | Income path = pipeline → HITL → approve → ledger (+ optional TikTok API) |
| 2026-04-02 | Phase 3–5 code: `klip_selector`, blacklist on enqueue, `scheduler.py` + budget, `klip_trader.signal_emitter`, spawn Telegram, Docker/compose, `scripts/verify_stack.py`, `.env.example`, `railway.scheduler.toml` | Production packaging; manual gates 1B.7, 1C.5, 2.7–2.8, 3.8, 4.8, 5.7 remain |
| 2026-04-02 | klipaura.com (Flask `anika-glow-affiliate`): optional footer link via `KLIP_STUDIO_URL` → deployed HITL; `.env` extended with `JOBS_DIR`, `PORT`, `PUBLIC_SITE_URL` (sync root + `core_v1/.env`) | Next: deploy `klip-api` on Railway, set `KLIP_STUDIO_URL` on the **website** Railway service to that API URL |
| 2026-04-02 | **Mission Control:** not a separate Next.js app — **HITL FastAPI** is the ops hub (queues + `/api/ops/summary` + links to `MISSION_CONTROL_URL` for Core V1 UI and `PUBLIC_SITE_URL` for klipaura.com). **klipaura.com** = keep domain + Anika brand; marketing site unchanged unless you decide a rebrand. | Single pane of glass for revenue loop; Core V1 dashboard stays optional sidecar |
| 2026-04-02 | **Local E2E smoke:** `uvicorn` + `worker.py` on **127.0.0.1:8080**; `POST /api/jobs` → pipeline → HITL → approve → `revenue.jsonl` + `MANUAL_PUBLISH_REQUIRED` until Zernio keys in `social_config.json`. WaveSpeed I2V showed **insufficient credits**; pipeline used fallbacks. | Remaining: **2.7 Railway**, fill **social_config**, top up WaveSpeed if you want I2V |
| 2026-04-02 | **CTO:** System is **built** for the affiliate loop; “pending” in tracker = **deploy + gates + credentials + UX depth**, not missing core code. **Product = your URL** → `POST /api/jobs` (now **Enqueue** form on `/`) → worker **extracts** from page + env images → `ugc_pipeline` builds video. Batch path = `products.csv` + selector. | Next: Railway, richer panels optional |
| 2026-04-02 | **Railway:** `railway init` → `klipaura` / `klip-api`; merged `requirements.txt` + root `nixpacks.toml` (ffmpeg); one container runs worker + API; `JOBS_DIR=/app/jobs`; `MISSION_CONTROL_URL` set to public URL | Split API/worker needs shared volume or R2-only manifests; `railway.worker.toml` reserved for that |
| 2026-04-02 | **Phase 3–5 ops:** `GET /api/autopilot/status`, `POST /api/selector/run` + `POST /api/market/sentiment` (header `X-Ops-Key` + env `OPS_API_KEY`); `infrastructure/autopilot_info.py` shared with `scheduler.py`; `klip_trader.hooks` for trader→Redis sentiment | Manual soak gates 3.8 / 4.8 / 5.7 unchanged; add `OPS_API_KEY` on Railway for remote selector |
