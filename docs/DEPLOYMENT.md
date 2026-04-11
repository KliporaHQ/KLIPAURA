# KLIPAURA — deployment guide (Railway & similar)

This guide assumes you run **two public surfaces**:

| Hostname | Service | Purpose |
|----------|---------|---------|
| **`app.klipaura.com`** | Next.js (`klip-mission-control`) | Mission Control UI |
| **`api.klipaura.com`** | **`hitl_server`** (`klip-dispatch/hitl_server.py`) | Enqueue jobs, avatars, dashboard JSON for the UI |

**Authoritative API image:** root `Dockerfile` runs `uvicorn hitl_server:app --app-dir klip-dispatch`. Use that for the API service. (A separate `hitl_server` under `klip-mission-control/` exists for legacy/dev wiring—**do not** deploy that as your primary public API unless you know you need it.)

---

## 1. Railway: two services + worker (recommended)

### Service A — API (`api.klipaura.com`)

- **Root directory:** repo root (same as this monorepo).
- **Build:** use root `Dockerfile` (or Nixpacks with `pip install -r requirements.txt` + same `CMD` as Dockerfile).
- **Start command** (if not using Dockerfile CMD):

  ```bash
  uvicorn hitl_server:app --app-dir klip-dispatch --host 0.0.0.0 --port ${PORT:-8080}
  ```

- **Port:** Railway sets `PORT`; map public HTTPS to this service and attach custom domain **`api.klipaura.com`**.

### Service B — Next.js UI (`app.klipaura.com`)

- **Root directory:** `klip-mission-control/`.
- **Build command:** `npm ci` (or `npm install`) then `npm run build`.
- **Start:** `npm start` (or platform default for Next).
- **Build-time env** (required for split UI/API):

  ```bash
  NEXT_PUBLIC_API_BASE_URL=https://api.klipaura.com
  ```

  Set **`NEXT_PUBLIC_API_URL`** only if you use older fallbacks; prefer **`NEXT_PUBLIC_API_BASE_URL`**.

- Attach custom domain **`app.klipaura.com`**.

### Service C — Worker (same Redis as API)

- **Root directory:** repo root; same Docker image as API or a worker-specific start:

  ```bash
  python klip-avatar/worker.py
  ```

- **Critical:** **`REDIS_URL`** (or Upstash) must be **identical** to the API service. Mirror **`.env`** from repo root; copy pipeline secrets into **`klip-avatar/core_v1/.env`** on the worker (Groq, WaveSpeed, ElevenLabs, R2, etc.).

Optional: **scheduler** — `python scheduler.py` with `AUTOPILOT_MODE=1` (see `.env.example`), usually a fourth service or cron.

---

## 2. Required environment variables

### API (`hitl_server` / `klip-dispatch`)

| Variable | Notes |
|----------|--------|
| **`REDIS_URL`** or **`UPSTASH_REDIS_REST_URL`** + **`UPSTASH_REDIS_REST_TOKEN`** | Same Redis as worker. |
| **`CORS_ALLOW_ORIGINS`** | e.g. `https://app.klipaura.com` (comma-separate multiple origins). **Required** when the browser calls `https://api.klipaura.com` from `https://app.klipaura.com`. |
| **`FUNNEL_PUBLIC_BASE_URL`** | Public base for funnel HTML (R2/CDN), e.g. `https://cdn.klipaura.com`. |
| **`JOBS_DIR`** | Persisted volume path in production (e.g. `/app/jobs`). |
| **`OPS_API_KEY`** | If you use protected ops routes (see `.env.example`). |

### Next.js (build-time for client bundle)

| Variable | Notes |
|----------|--------|
| **`NEXT_PUBLIC_API_BASE_URL`** | `https://api.klipaura.com` — browser talks to API directly. |

### Server-side only (optional rewrites in Docker)

| Variable | Notes |
|----------|--------|
| **`MC_INTERNAL_API_URL`** | Internal URL of `main.py` if you run Mission Control “full” API (default dev: `http://127.0.0.1:8000`). |
| **`MC_INTERNAL_HITL_URL`** | Internal HITL URL for same-container rewrites (default dev: `http://127.0.0.1:8080`). |

For **split** `app` + `api`, rely on **`NEXT_PUBLIC_API_BASE_URL`** + **CORS**; internal URLs are for all-in-one Docker or local dev.

### Worker

| Variable | Notes |
|----------|--------|
| Same **`REDIS_URL`** / Upstash as API. | |
| Pipeline keys | Mirror **`klip-avatar/core_v1/.env`** (WaveSpeed, Groq, ElevenLabs, R2, etc.). |
| **`PYTHONPATH`** | Root `Dockerfile` sets `/app:/app/klip-scanner:/app/klip-funnel`. |

See also **`.env.example`** and **`docs/MISSION_CONTROL.md`**.

---

## 3. Docker Compose (local or VPS)

Root **`docker-compose.yml`** defines:

- **`klip-api`** — port **8080**, `uvicorn hitl_server:app --app-dir klip-dispatch`.
- **`klip-worker`** — `python klip-avatar/worker.py`.
- **`redis`** — profile **`local-redis`** for bundled Redis.

Example:

```bash
docker compose --profile local-redis up --build klip-api klip-worker
```

Point **`.env`** at Upstash instead of local Redis if you prefer no `redis` service. For production on a VPS, put **TLS** in front (Caddy, nginx, or Cloudflare) and set **`CORS_ALLOW_ORIGINS`** + **`NEXT_PUBLIC_API_BASE_URL`** like Railway.

---

## 4. Subdomains: Cloudflare vs Railway

### Railway

1. Add **custom domain** on each service: **`api.klipaura.com`** → API service, **`app.klipaura.com`** → Next service.
2. Railway provides DNS targets (CNAME or ALIAS). Use **HTTPS** (automatic certificates).
3. Set env vars **after** domains are live so you do not bake wrong URLs into builds.

### Cloudflare (DNS in front of Railway or any host)

1. Create **CNAME** records: `api` → Railway hostname, `app` → Railway hostname (or your origin).
2. **Proxy** (orange cloud) is fine for most setups; ensure SSL mode is **Full (strict)** when origin has valid certs.
3. **CORS** on the API must list **`https://app.klipaura.com`** exactly (scheme + host).

### Checklist before going live

- [ ] **`NEXT_PUBLIC_API_BASE_URL=https://api.klipaura.com`** at **Next build** time.
- [ ] **`CORS_ALLOW_ORIGINS`** includes **`https://app.klipaura.com`**.
- [ ] API + worker share **Redis** and **jobs** storage (volume or shared `JOBS_DIR` if multi-instance).
- [ ] **`FUNNEL_PUBLIC_BASE_URL`** matches your public funnel host.
- [ ] Secrets only in platform env / secrets manager, not in git.

---

## 5. Smoke after deploy

From your machine (or CI):

```bash
curl -sS https://api.klipaura.com/docs
```

Local parity: `python scripts/test_full_pipeline.py --base-url https://api.klipaura.com` (requires auth/network as configured).

For more detail on Next rewrites and variable names, see **`docs/MISSION_CONTROL.md`**.
