/** @type {import('next').NextConfig} */
const path = require('path')

/**
 * Next rewrites run inside the MC Docker image and must hit uvicorn on loopback.
 * If MC_INTERNAL_API_URL is set to the public Railway hostname, every /api/* proxy fails (500).
 */
function resolveMcInternalApiUrl() {
  const fallback = 'http://127.0.0.1:8000'
  let raw = (process.env.MC_INTERNAL_API_URL || '').trim().replace(/\/$/, '')
  if (!raw) return fallback
  if (!/^https?:\/\//i.test(raw)) raw = `http://${raw}`
  try {
    const u = new URL(raw)
    const host = u.hostname
    if (host === '127.0.0.1' || host === 'localhost') return raw
    if (/\.(up\.)?railway\.app$/i.test(host) || /\.vercel\.app$/i.test(host)) {
      console.warn(
        '[next.config] MC_INTERNAL_API_URL must not be a public edge URL; using http://127.0.0.1:8000',
      )
      return fallback
    }
    return raw
  } catch {
    return fallback
  }
}

// Server-side only: uvicorn inside the MC container (browser uses same-origin /api via rewrites).
const internalApi = resolveMcInternalApiUrl()

/** HITL (`hitl_server`) for `/api/avatars`, `/api/jobs`, `/api/dashboard/*` when the browser uses same-origin paths (empty `NEXT_PUBLIC_API_BASE_URL`). */
function resolveMcInternalHitlUrl() {
  const fallback = 'http://127.0.0.1:8080'
  let raw = (process.env.MC_INTERNAL_HITL_URL || '').trim().replace(/\/$/, '')
  if (!raw) return fallback
  if (!/^https?:\/\//i.test(raw)) raw = `http://${raw}`
  try {
    const u = new URL(raw)
    const host = u.hostname
    if (host === '127.0.0.1' || host === 'localhost') return raw
    if (/\.(up\.)?railway\.app$/i.test(host) || /\.vercel\.app$/i.test(host)) {
      console.warn(
        '[next.config] MC_INTERNAL_HITL_URL must not be a public edge URL; using http://127.0.0.1:8080',
      )
      return fallback
    }
    return raw
  } catch {
    return fallback
  }
}

const internalHitl = resolveMcInternalHitlUrl()

function resolveDistDir() {
  const d = process.env.NEXT_DIST_DIR
  if (!d || !String(d).trim()) return '.next'
  const s = String(d).trim()
  return path.isAbsolute(s) ? s : path.resolve(process.cwd(), s)
}

const nextConfig = {
  output: 'standalone',
  reactStrictMode: true,
  distDir: resolveDistDir(),
  async rewrites() {
    // Do NOT use `/api/:path*` — it maps `/api/health` → FastAPI `/api/health` (404).
    // Health is served by `src/app/api/health/route.ts` (proxies to GET /health).
    return [
      { source: '/api/v1/:path*', destination: `${internalApi}/api/v1/:path*` },
      { source: '/api/events/:path*', destination: `${internalApi}/api/events/:path*` },
      // HITL Mission Control API (affiliate jobs, avatars, dashboard rows) — same-origin in dev/Docker.
      { source: '/api/avatars', destination: `${internalHitl}/api/avatars` },
      { source: '/api/avatars/:path*', destination: `${internalHitl}/api/avatars/:path*` },
      { source: '/api/jobs', destination: `${internalHitl}/api/jobs` },
      { source: '/api/jobs/:path*', destination: `${internalHitl}/api/jobs/:path*` },
      { source: '/api/dashboard/:path*', destination: `${internalHitl}/api/dashboard/:path*` },
    ]
  },
};

module.exports = nextConfig;
