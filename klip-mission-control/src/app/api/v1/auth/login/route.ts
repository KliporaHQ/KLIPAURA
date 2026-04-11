import { NextRequest, NextResponse } from 'next/server'
import {
  createAccessToken,
  jwtAlgorithm,
  loginPasswordConfigured,
  verifyCredentials,
} from '@/lib/mc-auth'

/**
 * HS256 login is handled in-process (same env as FastAPI) so we never depend on
 * Node fetch to 127.0.0.1:8000. Non-HS256 JWT_ALGORITHM still proxies to uvicorn.
 */
function internalApiBase(): string {
  return (process.env.MC_INTERNAL_API_URL || 'http://127.0.0.1:8000').replace(
    /\/$/,
    '',
  )
}

async function fetchUpstream(
  url: string,
  init: RequestInit,
): Promise<Response> {
  const attempts = parseInt(process.env.MC_LOGIN_PROXY_ATTEMPTS || '25', 10)
  const baseDelayMs = parseInt(process.env.MC_LOGIN_PROXY_DELAY_MS || '400', 10)
  let lastErr: unknown
  for (let i = 0; i < attempts; i++) {
    try {
      return await fetch(url, {
        ...init,
        signal: AbortSignal.timeout(15_000),
      })
    } catch (e) {
      lastErr = e
      if (i < attempts - 1) {
        await new Promise((r) => setTimeout(r, baseDelayMs * (i + 1)))
      }
    }
  }
  throw lastErr
}

export async function POST(request: NextRequest) {
  const bodyText = await request.text()
  const contentType =
    request.headers.get('content-type') || 'application/json'

  if (jwtAlgorithm() === 'HS256') {
    let body: { username?: unknown; password?: unknown }
    try {
      body = bodyText ? JSON.parse(bodyText) : {}
    } catch {
      return NextResponse.json({ detail: 'Invalid JSON body' }, { status: 422 })
    }
    if (!loginPasswordConfigured()) {
      return NextResponse.json(
        {
          detail:
            'Server misconfigured: set MC_ADMIN_PASSWORD or ADMIN_PASSWORD in the environment',
        },
        { status: 503 },
      )
    }
    const username =
      typeof body.username === 'string' ? body.username : 'admin'
    const password =
      typeof body.password === 'string' ? body.password : ''
    if (!password) {
      return NextResponse.json({ detail: 'Password required' }, { status: 422 })
    }
    if (!verifyCredentials(username, password)) {
      return NextResponse.json({ detail: 'Invalid credentials' }, { status: 401 })
    }
    try {
      const token = await createAccessToken(username)
      return NextResponse.json({
        access_token: token,
        token_type: 'bearer',
      })
    } catch (e) {
      console.error('[login] createAccessToken', e)
      return NextResponse.json(
        { detail: 'Token creation failed' },
        { status: 500 },
      )
    }
  }

  const url = `${internalApiBase()}/api/v1/auth/login`
  let upstream: Response
  try {
    upstream = await fetchUpstream(url, {
      method: 'POST',
      headers: { 'Content-Type': contentType },
      body: bodyText,
      cache: 'no-store',
    })
  } catch {
    return NextResponse.json(
      {
        detail:
          'Mission Control API unreachable from the UI process. For HS256 (default), login no longer requires uvicorn; if you use JWT_ALGORITHM other than HS256, set MC_INTERNAL_API_URL or fix FastAPI startup (see Railway logs).',
      },
      { status: 502 },
    )
  }
  const text = await upstream.text()
  return new NextResponse(text, {
    status: upstream.status,
    headers: {
      'Content-Type':
        upstream.headers.get('content-type') || 'application/json; charset=utf-8',
    },
  })
}
