import { NextResponse } from 'next/server'

/**
 * Railway and load balancers hit $PORT — this must not depend on the FastAPI
 * sub-process or Next rewrites (standalone rewrites to :8000 can 503 in production).
 */
export async function GET() {
  return NextResponse.json(
    {
      status: 'ok',
      service: 'klip-mission-control-ui',
      hint: 'Full API + Redis health: same-origin /api/health (proxies to FastAPI /health).',
    },
    { status: 200 },
  )
}
