# KLIPAURA

## 🚀 Deploy to Railway (One-Click)

1. Go to [Railway.app](https://railway.app) → New Project → Deploy from GitHub → select **KLIPAURA**
2. Add these Environment Variables (from `.env.example`):
   - `WAVESPEED_API_KEY`, `ELEVENLABS_API_KEY`, `R2_*` keys, `REDIS_URL`, etc.
   - `NEXT_PUBLIC_MC_SKIP_LOGIN=1` (optional for easier local-like dev)
3. Railway will auto-detect services from `.railway.toml`
4. Deploy — you will get live URLs for the dashboard and API.

**Autonomous AI affiliate short-form video factory** (clean repo, consolidated from earlier iterations).

## Current status

The **core loop is implemented and testable**: product URL → affiliate-aware script → **55/45 split-screen** video with avatar → optional funnel; **config-driven** affiliates (`config/affiliate_programs.json`) and avatars (`config/avatars.json`); **HITL** (`klip-dispatch/hitl_server.py`, port **8080**) plus **`klip-avatar/worker.py`** on the **same Redis**; manifests with **heartbeats** and **`pipeline_stage_history`**; **`diagnose_job.py`** and **`test_full_pipeline.py`** for verification. Reliability still depends on **external APIs** (WaveSpeed, ElevenLabs, Groq, R2) and correct **`.env`** / **`klip-avatar/core_v1/.env`**. Mission Control **Next.js** (`klip-mission-control/`) is optional for local UI.

---

## How to run KLIPAURA

This is the **primary** daily path: set **`PYTHONPATH`**, start **HITL**, start **Worker**, then run the **full pipeline test** (or enqueue from the UI).

### Prerequisites

- Python **3.11+**, **Node 18+** (if using Next), **Redis** (local or Upstash — see `.env.example`).
- Copy **`.env.example`** → **`.env`**; mirror pipeline keys into **`klip-avatar/core_v1/.env`** (Groq, WaveSpeed, ElevenLabs, R2, etc.).

From the repo root (PowerShell):

```powershell
cd E:\KLIPAURA
$env:PYTHONPATH="E:\KLIPAURA;E:\KLIPAURA\klip-scanner;E:\KLIPAURA\klip-funnel"
```

Linux/macOS: `export PYTHONPATH="$PWD:$PWD/klip-scanner:$PWD/klip-funnel"`.

### Two terminals (minimal)

| # | Role | Command |
|---|------|---------|
| **1** | **HITL API** (8080) | `python -m uvicorn hitl_server:app --app-dir klip-dispatch --host 127.0.0.1 --port 8080` |
| **2** | **Worker** | `python klip-avatar/worker.py` |

Shortcut: `scripts/run-hitl-server.ps1`.

### Recommended full pipeline test (after 1 + 2)

Default product is a **Roborock** short link (`https://amzn.to/4cewwZo`); default avatar **`theanikaglow`**. Override with **`--product-url`** / **`--avatar-id`**, or set **`KLIP_TEST_PRODUCT_URL`**.

```powershell
python scripts/test_full_pipeline.py --progress-sec 15
```

Explicit Roborock (full Amazon URL) if you prefer:

```powershell
python scripts/test_full_pipeline.py `
  --product-url "https://www.amazon.com/Roborock-Self-Emptying-Robot-Vacuum-Cleaner/dp/B0C7W5Z5Z5" `
  --avatar-id theanikaglow `
  --progress-sec 15
```

Success prints **`SUCCESS: Full pipeline completed`**, **`r2_url`**, **`funnel_url`**, and elapsed time.

### Optional: Mission Control API + Next.js

**API** (`/api/v1/*`) from **`klip-mission-control/`**:

```powershell
python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

**Next.js UI:**

```bash
cd klip-mission-control
npm install
npm run dev
```

Open **http://localhost:3000** — **`/mc/avatars`**, **`/mc/pipeline`**.

### Quick smoke (enqueue only)

```powershell
python scripts/smoke_affiliate_job.py --base-url http://127.0.0.1:8080 `
  --product-url "https://www.amazon.com/dp/B0EXAMPLE" `
  --avatar-id theanikaglow --generate-funnel
```

---

## Quick Debug & Test

### One-command optimized test

After HITL + Worker + Redis:

```powershell
python scripts/test_full_pipeline.py --progress-sec 15
```

Use **`KLIP_MC_BASE_URL`** if HITL is not on `http://127.0.0.1:8080`.

### Troubleshooting checklist

| Problem | What to do |
|---------|------------|
| **QUEUED** forever | 1) **Worker running?** Same **`PYTHONPATH`** and **`.env`** as HITL. 2) **Same Redis** (`REDIS_URL` / Upstash) for API and worker. 3) **Queue depth:** one worker runs one job at a time—later jobs wait. 4) **Port 8080** is this repo’s HITL, not another app. |
| **PROCESSING** very long | Often **normal** (LLM + WaveSpeed + render + upload). Run **`python scripts/diagnose_job.py <job-uuid>`** — check **`pipeline_stage`**, **`pipeline_stage_history`**, **`updated_at`**. If a stage never advances, check worker logs for WaveSpeed / ElevenLabs / R2; see **`KLIP_PIPELINE_SUBPROCESS_TIMEOUT_SEC`** on the worker if a subprocess hangs. |

### Using `diagnose_job.py`

```powershell
python scripts/diagnose_job.py YOUR-JOB-UUID
```

Shows manifest summary, time since update, last **`pipeline_stage_history`** entries, log tail, and a short status (**running / likely stuck / completed**). Use the UUID printed by **`test_full_pipeline.py`** after **`POST /api/jobs`**.

---

## Troubleshooting (extended)

| Symptom | What to check |
|---------|----------------|
| **`voice_not_found` (ElevenLabs)** | Avatar **`social_config.json`** — **`elevenlabs_voice_id`** must match your ElevenLabs account. |
| **WaveSpeed timeout / slow video** | Provider load / network; check logs and **`pipeline_stage`**. |
| **Redis connection errors** | **`REDIS_URL`** / Upstash in **`.env`**; firewall/VPN. |
| **`test_full_pipeline` fails immediately** | HITL not reachable or wrong **`--base-url`** / **`KLIP_MC_BASE_URL`**. |

---

## Deploying to production

**UI:** `https://app.klipaura.com` (Next.js). **API:** `https://api.klipaura.com` (**`hitl_server`**). Set **`NEXT_PUBLIC_API_BASE_URL=https://api.klipaura.com`** at Next **build** time; on the API, **`CORS_ALLOW_ORIGINS=https://app.klipaura.com`**. **`FUNNEL_PUBLIC_BASE_URL`** for public funnel URLs. Full step-by-step: **`docs/DEPLOYMENT.md`**. Env details: **`docs/MISSION_CONTROL.md`**.

**Docker:** `docker compose --profile local-redis up --build klip-api klip-worker` (see root **`docker-compose.yml`**).

---

## Repo layout

```
KLIPAURA/
  config/                 # affiliate_programs.json, avatars.json, templates.json
  docs/                   # MISSION_CONTROL.md, DEPLOYMENT.md
  infrastructure/         # job manifests, Redis helpers
  klip-avatar/            # worker.py + core_v1 pipeline
  klip-dispatch/          # hitl_server.py (canonical HITL for production)
  klip-scanner/           # klip_scanner/, klip_selector/ (hyphen folder; stable imports)
  klip-mission-control/   # Next.js + main.py (optional; contains legacy hitl_server for MC-only flows)
  klip-core/              # shared klip_core
  klip-funnel/            # funnel HTML
  ARCHIVES/               # optional legacy snapshots (see ARCHIVES/README.md)
  jobs/ outputs/ scripts/
```

Architecture:

```
klip_selector → Redis (klip:jobs:pending) → klip-avatar/worker → ugc_pipeline → HITL → publisher → revenue.jsonl
```

Optional: `scheduler.py` (autopilot profile in Docker).

---

## HOW TO START A SESSION

1. Open **this folder** (`E:\KLIPAURA`) in Cursor  
2. Open `SESSION_START.md` and use the prompt block  

## CURRENT STATE

- **`TASK_TRACKER.md`** — task progress  
- **`.cursor/rules/`** — project constitution  

## PRODUCTION DEPLOY (checklist)

1. **Environment:** `.env` at repo root; pipeline keys in **`klip-avatar/core_v1/.env`**.  
2. **Redis:** Upstash or **`REDIS_URL`**.  
3. **Smoke:** run **`scripts/test_full_pipeline.py`** against staging/production API if exposed.  

## COST: ~$27/month | BREAK-EVEN: 2–3 sales

---

## KLIPAURA status summary

| Working | Extend later |
|---------|----------------|
| Affiliate scan → selector → Redis queue | Zerino auto-post |
| **ugc_pipeline** — voice, WaveSpeed, lipsync, R2 | Advanced avatar flows |
| HITL API — enqueue, manifests | More funnel templates |
| Mission Control UI — **`/mc/*`** | Deeper analytics |
| **`diagnose_job.py`**, stage history | — |
