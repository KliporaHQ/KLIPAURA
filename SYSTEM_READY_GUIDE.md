# KLIPAURA System — Ready to Use

This guide is the daily reference after bug hunts, UI polish, timeout fixes, and save-persona wiring. For rollback of older edits, look for `.bak` files next to modified sources.

---

## Quick start (daily workflow)

1. **Start backend services** (from repo root, with `.env` loaded):
   - Mission Control FastAPI on **:8000** (uvicorn `klip-mission-control/main:app`).
   - Optional: HITL dispatch on **:8080** (`hitl_server` in `klip-dispatch`) — if not running, the Mission Control UI falls back to FastAPI for jobs/avatars/dashboard data (amber notice is normal).
   - **Redis** reachable (Upstash or local) and **`klip-avatar/worker.py`** if you want jobs to run past enqueue.

2. **Start the dashboard** (`klip-mission-control`):
   ```bash
   cd klip-mission-control
   npm run dev
   ```
   Open **http://localhost:3000**.

3. **Avatar Studio** — **http://localhost:3000/avatar-studio**
   - Enter a description, adjust presets, click **Generate**.
   - Use **Test Voice** and **Test Lip-Sync** when a session exists.
   - **Save as Persona** persists to Redis (requires MC API + Redis + R2 for full generate path).

4. **Video + funnel** — use **Video + Funnel** (or go to **http://localhost:3000/avatar**) with persona selected:
   - Paste an Amazon or affiliate product URL (e.g. `https://amzn.to/4cewwZo`).
   - Add optional disclosure text if your program requires it.
   - Submit the job and wait for the worker to finish (manifest under `jobs/<job_id>/`).

5. **Outputs**
   - Final video: typically **9:16**, **affiliate split** layout (`affiliate_split_55_45` — product panel upper ~55%, avatar/lip-sync lower ~45%; exact pixels depend on render settings).
   - Funnel: HTML page with embedded video, CTA, and fields your funnel builder adds (verify in job manifest / R2 URLs when `generate_funnel` is true).

---

## Key features

- **Prompt-based AI avatar** — composite prompt → portrait (WaveSpeed) → R2 + Redis persona record; not real-person cloning.
- **Split-screen affiliate videos** — product/visuals in the top panel, talking avatar in the bottom panel (layout mode `affiliate_split_55_45`).
- **Funnel pages** — generated when requested; include video embed + branding fields from your funnel pipeline.
- **Framer Motion** — Avatar Studio uses motion for preview, buttons, saved personas row, and loading overlays.
- **HITL optional locally** — `/mc/pipeline` and `/mc/avatars` use a short timeout and can show an amber banner and load **Mission Control on :8000** directly when the HITL proxy (:8080) is down.

---

## Important notes

- **Both** Next.js and FastAPI should be running for the full UI + `/api/v1/*` flows. Login can be skipped in dev with `NEXT_PUBLIC_MC_SKIP_LOGIN=1` in `.env.local` (do not use in production).
- **Personas** — stored in Redis (`avatar:persona:*`) and on disk/R2 as part of generate; **Save as Persona** confirms the current session record.
- **Videos and funnels** — paths and public URLs land in job **manifest** and R2 when configured (`R2_*` env vars).
- **Production** — add **affiliate disclosures** and program-compliant text; this repo does not provide legal advice.

---

## Troubleshooting

| Symptom | What to check |
|--------|----------------|
| Amber box on `/mc/pipeline` or `/mc/avatars` | HITL on :8080 not running — **normal** in local dev; data may still load from :8000. |
| **Personas not saving** | Redis up? R2 configured? Check MC logs and `POST /api/v1/avatar-studio/save-persona` response. |
| **Slow or stuck jobs** | Worker running? Redis queue `klip:jobs:pending`? WaveSpeed / ElevenLabs keys and credits? |
| **Pipeline errors** | `jobs/<job_id>/manifest.json` for `status`, `error`, `pipeline_stage`. |
| **Next.js won’t load** | `npm install` in `klip-mission-control`; ensure port **3000** free. |

---

## Automated smoke test (Roborock sample link)

With HITL on **8080** and env set:

```bash
cd E:\KLIPAURA
set PYTHONPATH=E:\KLIPAURA;E:\KLIPAURA\klip-scanner;E:\KLIPAURA\klip-funnel
python scripts/test_full_pipeline.py --product-url "https://amzn.to/4cewwZo" --avatar-id theanikaglow --progress-sec 15
```

Start **`klip-avatar/worker.py`** so the job leaves `queued`. Full completion depends on API keys, credits, and FFmpeg.

---

## Rollback

- Bug-hunt and polish edits that touched source files may have **`.bak`** copies alongside the originals. Restore with:
  ```powershell
  Copy-Item path\to\file.bak path\to\file -Force
  ```

---

## Version

- Guide generated for KLIPAURA repo layout: `E:\KLIPAURA`.
- Layout split: `affiliate_split_55_45` in job payload matches the intended top/bottom affiliate UGC style; validate visually on your first successful render.
