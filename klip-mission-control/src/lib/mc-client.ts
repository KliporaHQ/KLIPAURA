/**
 * Shared Mission Control browser API helpers (session token + same-origin base).
 */

/** Default operator fields.  Read from env at build time; empty if not set.
 *  Supports both old (`ADMIN_USERNAME`/`ADMIN_PASSWORD`) and new (`MC_DEFAULT_USER`/`MC_DEFAULT_PASS`) names. */
export const MC_DEFAULT_LOGIN_USERNAME =
  process.env.NEXT_PUBLIC_MC_DEFAULT_USER ||
  process.env.NEXT_PUBLIC_ADMIN_USERNAME ||
  ''
export const MC_DEFAULT_LOGIN_PASSWORD =
  process.env.NEXT_PUBLIC_MC_DEFAULT_PASS ||
  process.env.NEXT_PUBLIC_ADMIN_PASSWORD ||
  ''

/**
 * When true, dashboard /avatar /credits skip the sign-in screen (testing).
 * Set `NEXT_PUBLIC_MC_SKIP_LOGIN=0` (or `false` / `off` / `no`) at build time to require login again.
 * Default `MC_LOGIN_GATE_TEMP_DISABLED` is false so production requires login unless env explicitly enables skip.
 */
const MC_LOGIN_GATE_TEMP_DISABLED = false

export function mcSkipLogin(): boolean {
  const v = (process.env.NEXT_PUBLIC_MC_SKIP_LOGIN || '').trim().toLowerCase()
  if (v === '0' || v === 'false' || v === 'off' || v === 'no') return false
  if (['1', 'true', 'yes', 'on'].includes(v)) return true
  return MC_LOGIN_GATE_TEMP_DISABLED
}

export function getMcToken(): string | null {
  if (typeof window === 'undefined') return null
  return sessionStorage.getItem('mc_token')
}

export function setMcToken(t: string | null): void {
  if (typeof window === 'undefined') return
  if (t) sessionStorage.setItem('mc_token', t)
  else sessionStorage.removeItem('mc_token')
}

export function getApiBase(): string {
  return (
    (
      process.env.NEXT_PUBLIC_API_BASE_URL ||
      process.env.NEXT_PUBLIC_API_URL ||
      process.env.NEXT_PUBLIC_MC_API_URL ||
      ''
    ).trim()
  )
}

export function getGitSha(): string {
  return process.env.NEXT_PUBLIC_GIT_SHA || 'dev'
}

/**
 * Public URL of the HITL review UI (separate Railway service from Mission Control).
 * Set `NEXT_PUBLIC_HITL_URL` in Railway (and rebuild) to e.g. `https://hitl-production.up.railway.app`.
 * In production builds, if unset, returns `null` — do not fall back to localhost.
 * In local dev (`next dev`), defaults to `http://localhost:8080` when unset.
 * Docker/Railway: set `NEXT_PUBLIC_HITL_URL` at **build** time (Dockerfile ARG / compose build.args / Railway build-time env), then rebuild — not only container runtime env.
 */
export function getHitlPublicUrl(): string | null {
  const raw = (process.env.NEXT_PUBLIC_HITL_URL || '').trim()
  if (raw) return raw.replace(/\/$/, '')
  if (process.env.NODE_ENV === 'production') return null
  return 'http://localhost:8080'
}

export function createApiFetch(baseUrl: string) {
  return async function apiFetch(path: string, init?: RequestInit): Promise<Response> {
    const headers = new Headers(init?.headers)
    headers.set('Content-Type', headers.get('Content-Type') || 'application/json')
    const tok = getMcToken()
    if (tok) headers.set('Authorization', `Bearer ${tok}`)
    let signal = init?.signal
    if (!signal) {
      const c = new AbortController()
      const t = setTimeout(() => c.abort(), 20000)
      signal = c.signal
      try {
        return await fetch(`${baseUrl}${path}`, { ...init, headers, signal })
      } finally {
        clearTimeout(t)
      }
    }
    return fetch(`${baseUrl}${path}`, { ...init, headers, signal })
  }
}
