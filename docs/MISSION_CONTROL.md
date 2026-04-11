# Mission Control deployment (subdomain-ready)

Run the **FastAPI** app from repo root (`klip-dispatch/hitl_server.py`) and the **Next.js** dashboard (`klip-mission-control/`) as separate services. Point your public DNS so the dashboard can live on **`app.klipaura.com`** or **`mc.klipaura.com`**, and the HITL / enqueue API on another host (e.g. **`api.klipaura.com`**) or a separate Railway service.

The repo uses **two** Python HTTP surfaces in local dev:

- **`main.py` / klip-core** (often port **8000**): `/api/v1/*`, `/api/events/*` — metrics, jobs list, auth.
- **`hitl_server.py`** (often port **8080**): `/api/jobs`, `/api/avatars`, `/api/dashboard/*` — enqueue affiliate jobs, avatars, recent-job rows for the Phase 4 UI.

Next.js `next.config.js` rewrites **both**: `/api/v1/*` → `MC_INTERNAL_API_URL` (default 8000), and `/api/jobs`, `/api/avatars`, `/api/dashboard/*` → **`MC_INTERNAL_HITL_URL`** (default **8080**). When the browser uses **cross-origin** `NEXT_PUBLIC_API_BASE_URL`, no rewrites apply; set **CORS** on `hitl_server` instead.

## Environment variables

### Next.js (`klip-mission-control`)

| Variable | Purpose |
|----------|---------|
| `NEXT_PUBLIC_API_BASE_URL` | Browser-facing base URL for the **HITL** API when UI and API differ (e.g. `https://api.klipaura.com`). Used by `getApiBase()` for `fetch` to `/api/jobs`, `/api/avatars`, `/api/dashboard/recent-jobs`. |
| `NEXT_PUBLIC_API_URL` | Legacy/alternate name for the same value. If `NEXT_PUBLIC_API_BASE_URL` is unset, `src/lib/mc-client.ts` falls back to this. |
| `NEXT_PUBLIC_HITL_URL` | Public URL of a separate HITL review UI if you split services (optional). |
| `NEXT_PUBLIC_MC_SKIP_LOGIN` | Set to `0` / `false` in production to require login (see `mc-client.ts`). |
| `MC_INTERNAL_API_URL` | **Server-side only** (Next rewrites): URL of **main** FastAPI (`main.py`) **inside** the same Docker network or loopback — must **not** be a public Railway edge URL. Default `http://127.0.0.1:8000`. Used for `/api/v1/*` and `/api/events/*`. |
| `MC_INTERNAL_HITL_URL` | **Server-side only**: `hitl_server` URL for same-origin rewrites to `/api/jobs`, `/api/avatars`, `/api/dashboard/*`. Default `http://127.0.0.1:8080`. |

Build-time: set `NEXT_PUBLIC_*` at **build** time for production so the client bundle embeds the correct API origin.

### FastAPI (`klip-dispatch`)

| Variable | Purpose |
|----------|---------|
| `CORS_ALLOW_ORIGINS` | Comma-separated list of allowed browser origins, e.g. `https://app.klipaura.com,https://mc.klipaura.com`. **`CORS_ALLOW_ORIGINS=*`** explicitly allows all origins. If **unset**, `hitl_server` defaults to **local Next.js dev origins only** (localhost / 127.0.0.1 on ports 3000–3001), **not** `*`. |
| `FUNNEL_PUBLIC_BASE_URL` | Public base URL for published funnel HTML (R2/CDN). Worker and `POST /api/jobs/{id}/generate-funnel` use this when building `funnel_url`. See root `.env.example`. |
| `AFFILIATE_PROGRAMS_PATH` | Optional override path to `affiliate_programs.json` (default: `config/affiliate_programs.json`). |
| `JOBS_DIR` | Manifest/job folder (default: `<repo>/jobs`). |
| Redis / DB | See root `.env.example`. |

### Docker / worker

| Variable | Purpose |
|----------|---------|
| `PYTHONPATH` | Must include repo root and `klip-scanner` so `klip_scanner` / `klip_selector` imports resolve. The root `Dockerfile` sets `PYTHONPATH=/app:/app/klip-scanner`. |

## CORS

`hitl_server.py` reads `CORS_ALLOW_ORIGINS`. For a dashboard on `https://app.klipaura.com` calling an API on `https://api.klipaura.com`, set on the API service:

`CORS_ALLOW_ORIGINS=https://app.klipaura.com`

Add `https://mc.klipaura.com` if you use that hostname for the same UI build.

If you use **cookies** or `Authorization` from the browser across origins, ensure the API sets appropriate headers and that the origin list matches your deployed UI hostname exactly (scheme + host + port).

## Suggested production mapping

- `app.klipaura.com` **or** `mc.klipaura.com` → Next.js (Mission Control UI). Pick one primary; both are valid hostnames for the same build.
- `api.klipaura.com` → uvicorn `hitl_server:app` (enqueue, avatars, dashboard JSON).

Ensure TLS on both; set **`NEXT_PUBLIC_API_BASE_URL=https://api.klipaura.com`** when building the Next app so the client calls the API directly (CORS required).

**Local full stack:** run `hitl_server` on **8080**, main API on **8000**, `next dev` on **3000** with empty `NEXT_PUBLIC_API_BASE_URL` so the browser uses same-origin `/api/*` rewrites, or set `NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8080` and allow CORS for `http://localhost:3000`.
