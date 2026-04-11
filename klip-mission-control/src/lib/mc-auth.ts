/**
 * Mirrors klip_mc/security.py env handling and HS256 JWT issuance so dashboard
 * login works without a loopback fetch to uvicorn (fixes Railway "API unreachable").
 */
import { timingSafeEqual } from 'crypto'
import { SignJWT } from 'jose'

export function envVal(...keys: string[]): string {
  for (const k of keys) {
    const raw = process.env[k]
    if (!raw) continue
    const v = raw.trim().replace(/^\ufeff/, '').trim()
    if (v) return v
  }
  return ''
}

export function loginPasswordConfigured(): boolean {
  return !!envVal('MC_ADMIN_PASSWORD', 'ADMIN_PASSWORD')
}

function timingSafeEq(a: string, b: string): boolean {
  const bufa = Buffer.from(a, 'utf8')
  const bufb = Buffer.from(b, 'utf8')
  if (bufa.length !== bufb.length) return false
  return timingSafeEqual(bufa, bufb)
}

export function verifyCredentials(username: string, password: string): boolean {
  const userExpected =
    envVal('MC_ADMIN_USER', 'ADMIN_USERNAME', 'ADMIN_USER') || 'admin'
  const passExpected = envVal('MC_ADMIN_PASSWORD', 'ADMIN_PASSWORD')
  if (!passExpected) return false
  const u = username.trim().replace(/^\ufeff/, '').trim()
  const p = password.trim().replace(/^\ufeff/, '').trim()
  return timingSafeEq(userExpected, u) && timingSafeEq(passExpected, p)
}

export function jwtAlgorithm(): string {
  return (process.env.JWT_ALGORITHM || 'HS256').trim()
}

function jwtExpHours(): number {
  try {
    const h = parseInt(
      (process.env.JWT_EXPIRATION_HOURS || '24').trim() || '24',
      10,
    )
    return Number.isFinite(h) ? h : 24
  } catch {
    return 24
  }
}

export async function createAccessToken(subject: string): Promise<string> {
  const secret = envVal('JWT_SECRET_KEY', 'JWT_SECRET') || 'dev-secret-change-in-production'
  const hours = jwtExpHours()
  const exp = Math.floor(Date.now() / 1000) + hours * 3600
  const key = new TextEncoder().encode(secret)
  return await new SignJWT({})
    .setProtectedHeader({ alg: 'HS256' })
    .setSubject(subject)
    .setExpirationTime(exp)
    .sign(key)
}
