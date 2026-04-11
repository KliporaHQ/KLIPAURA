import { NextResponse } from 'next/server'

/**
 * Railway / load-balancer liveness for the **Next.js process only**.
 * Does not call FastAPI — avoids failed healthchecks when the UI service runs without uvicorn
 * (split deploy) or during API warmup. Use `GET /api/health` for same-origin FastAPI + Redis probe.
 */
export async function GET() {
  return NextResponse.json({
    status: 'ok',
    service: 'klipaura-frontend',
    timestamp: new Date().toISOString(),
  })
}
