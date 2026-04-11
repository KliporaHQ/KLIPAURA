import { NextResponse } from 'next/server'

export const dynamic = 'force-dynamic'

/**
 * Same-origin probe for FastAPI + Redis (Mission Control backend).
 * Implemented as a Route Handler (not next.config rewrites) so we always
 * hit uvicorn at loopback — avoids /api/:path* accidentally proxying to
 * FastAPI /api/health (which does not exist; real route is GET /health).
 */
function fastapiHealthUrl(): string {
  const fallback = 'http://127.0.0.1:8000/health'
  const raw = (process.env.MC_INTERNAL_API_URL || '').trim().replace(/\/$/, '')
  if (!raw) return fallback
  let base = raw
  if (!/^https?:\/\//i.test(base)) base = `http://${base}`
  try {
    const u = new URL(base)
    const host = u.hostname
    if (host === '127.0.0.1' || host === 'localhost') {
      return `${base}/health`
    }
    if (/\.(up\.)?railway\.app$/i.test(host) || /\.vercel\.app$/i.test(host)) {
      return fallback
    }
    return `${base}/health`
  } catch {
    return fallback
  }
}

export async function GET() {
  const url = fastapiHealthUrl()
  try {
    const r = await fetch(url, {
      cache: 'no-store',
      signal: AbortSignal.timeout(10000),
    })
    const text = await r.text()
    const ct = r.headers.get('content-type') || 'application/json'
    return new NextResponse(text, { status: r.status, headers: { 'content-type': ct } })
  } catch (e) {
    return NextResponse.json(
      {
        status: 'unreachable',
        hint: 'FastAPI not reachable from Next (check uvicorn on :8000 in the same container).',
        detail: String(e),
      },
      { status: 503 },
    )
  }
}
