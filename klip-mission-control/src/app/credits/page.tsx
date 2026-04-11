'use client'

import Link from 'next/link'
import { useCallback, useEffect, useState, type FormEvent } from 'react'
import {
  ArrowLeft,
  Coins,
  LayoutDashboard,
  Loader2,
  LogOut,
  Search,
  ShieldAlert,
} from 'lucide-react'
import { CreditsProviderTable, type ProviderAggregate } from '../../components/CreditsProviderTable'
import {
  createApiFetch,
  getApiBase,
  getMcToken,
  mcSkipLogin,
  MC_DEFAULT_LOGIN_PASSWORD,
  MC_DEFAULT_LOGIN_USERNAME,
  setMcToken,
} from '../../lib/mc-client'

type CapStatus = {
  last_24h_spend_usd: number
  last_30d_spend_usd: number
  burn_rate_usd_per_hour: number
  daily_cap_usd: number | null
  monthly_cap_usd: number | null
  daily_remaining_usd: number | null
  daily_cap_breach: boolean
  monthly_cap_breach: boolean
  wavespeed_max_i2v_per_hour: number | null
  notes: string[]
}

type TraceEvent = {
  id?: string
  ts?: string
  provider?: string
  operation?: string
  amount_usd?: number
  estimate?: boolean
  job_id?: string
  avatar_id?: string
  stage?: string | null
}

export default function CreditsPage() {
  const baseUrl = getApiBase()
  const apiFetch = createApiFetch(baseUrl)
  const [loggedIn, setLoggedIn] = useState(() => mcSkipLogin())
  const [loading, setLoading] = useState(true)
  const [loginUser, setLoginUser] = useState(MC_DEFAULT_LOGIN_USERNAME)
  const [loginPass, setLoginPass] = useState(MC_DEFAULT_LOGIN_PASSWORD)
  const [authError, setAuthError] = useState('')
  const [sinceHours, setSinceHours] = useState(168)
  const [summary, setSummary] = useState<{
    since_hours: number
    total_usd: number
    providers: Record<string, ProviderAggregate>
  } | null>(null)
  const [byAvatar, setByAvatar] = useState<Record<string, number> | null>(null)
  const [capStatus, setCapStatus] = useState<CapStatus | null>(null)
  const [jobTraceId, setJobTraceId] = useState('')
  const [jobTrace, setJobTrace] = useState<{ job_id: string; total_usd: number; events: TraceEvent[] } | null>(
    null,
  )
  const [jobTraceLoading, setJobTraceLoading] = useState(false)

  useEffect(() => {
    const boot = async () => {
      if (mcSkipLogin()) {
        setLoggedIn(true)
        return
      }
      if (typeof window !== 'undefined' && getMcToken()) {
        setLoggedIn(true)
        return
      }
      try {
        const r = await fetch(`${baseUrl}/api/v1/modules`)
        if (r.ok) setLoggedIn(true)
      } catch {
        /* need login */
      }
    }
    boot()
  }, [baseUrl])

  const loadData = useCallback(async () => {
    if (!loggedIn) return
    setLoading(true)
    try {
      const [s, a, c] = await Promise.all([
        apiFetch(`/api/v1/credits/providers/summary?since_hours=${sinceHours}`),
        apiFetch(`/api/v1/credits/by-avatar?since_hours=${sinceHours}`),
        apiFetch('/api/v1/credits/cap-status'),
      ])
      if (s.ok) setSummary(await s.json())
      if (a.ok) setByAvatar((await a.json()).avatars ?? {})
      if (c.ok) setCapStatus(await c.json())
    } catch {
      setSummary(null)
      setByAvatar(null)
      setCapStatus(null)
    } finally {
      setLoading(false)
    }
  }, [apiFetch, loggedIn, sinceHours])

  useEffect(() => {
    loadData()
  }, [loadData])

  const handleLogin = async (e: FormEvent) => {
    e.preventDefault()
    setAuthError('')
    try {
      const r = await fetch(`${baseUrl}/api/v1/auth/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          username: (loginUser || '').trim() || MC_DEFAULT_LOGIN_USERNAME,
          password: loginPass || MC_DEFAULT_LOGIN_PASSWORD,
        }),
      })
      const data = await r.json().catch(() => ({}))
      if (!r.ok) {
        setAuthError((data as { detail?: string }).detail || 'Login failed')
        return
      }
      const token = (data as { access_token?: string }).access_token
      if (token) {
        setMcToken(token)
        setLoggedIn(true)
      }
    } catch {
      setAuthError('Network error')
    }
  }

  const loadJobTrace = async () => {
    const jid = jobTraceId.trim()
    if (!jid) return
    setJobTraceLoading(true)
    try {
      const r = await apiFetch(`/api/v1/credits/jobs/${encodeURIComponent(jid)}/trace`)
      if (r.ok) setJobTrace(await r.json())
      else setJobTrace({ job_id: jid, total_usd: 0, events: [] })
    } catch {
      setJobTrace({ job_id: jid, total_usd: 0, events: [] })
    } finally {
      setJobTraceLoading(false)
    }
  }

  if (!loggedIn && !mcSkipLogin()) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-slate-950 px-4">
        <div className="mc-panel p-8 max-w-md w-full">
          <h1 className="text-xl font-bold mb-1 text-center text-slate-100">Credits Monitor</h1>
          <p className="text-slate-500 text-sm mb-6 text-center">Sign in to Mission Control</p>
          <form onSubmit={handleLogin} className="space-y-4">
            <input
              type="text"
              placeholder="Username"
              value={loginUser}
              onChange={(e) => setLoginUser(e.target.value)}
              className="w-full px-4 py-2.5 rounded-lg bg-slate-950 border border-slate-600 text-slate-100 text-sm"
            />
            <input
              type="password"
              placeholder="Password"
              value={loginPass}
              onChange={(e) => setLoginPass(e.target.value)}
              className="w-full px-4 py-2.5 rounded-lg bg-slate-950 border border-slate-600 text-slate-100 text-sm"
            />
            {authError && <p className="text-red-400 text-sm">{authError}</p>}
            <button
              type="submit"
              className="w-full py-2.5 rounded-lg bg-gradient-to-r from-klipaura-600 to-klipaura-400 text-white font-semibold text-sm"
            >
              Sign in
            </button>
          </form>
          <p className="text-xs text-slate-600 mt-4 text-center">
            <Link href="/" className="text-klipaura-400 hover:text-klipaura-300">
              ← Back to dashboard
            </Link>
          </p>
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100">
      <header className="border-b border-slate-800 bg-slate-900/80 px-4 py-3 flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <Link
            href="/"
            className="inline-flex items-center gap-2 text-sm text-slate-400 hover:text-slate-100"
          >
            <ArrowLeft className="w-4 h-4" />
            Dashboard
          </Link>
          <span className="text-slate-700">|</span>
          <div className="flex items-center gap-2">
            <Coins className="w-5 h-5 text-amber-300/90" />
            <span className="font-semibold text-slate-100">Credits</span>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <label className="text-xs text-slate-500 flex items-center gap-2">
            Window (h)
            <input
              type="number"
              min={24}
              max={8760}
              value={sinceHours}
              onChange={(e) => setSinceHours(Number(e.target.value) || 168)}
              className="w-20 px-2 py-1 rounded bg-slate-950 border border-slate-700 text-slate-100 text-xs font-mono"
            />
          </label>
          <button
            type="button"
            onClick={loadData}
            className="text-xs px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-200"
          >
            Refresh
          </button>
          {!mcSkipLogin() && (
            <button
              type="button"
              onClick={() => {
                setMcToken(null)
                setLoggedIn(false)
              }}
              className="inline-flex items-center gap-1 text-xs text-slate-500 hover:text-slate-200 px-2 py-1"
            >
              <LogOut className="w-3.5 h-3.5" />
              Log out
            </button>
          )}
        </div>
      </header>

      <main className="max-w-5xl mx-auto p-4 md:p-8 space-y-8">
        {loading && !summary && (
          <div className="flex items-center gap-2 text-slate-500 text-sm">
            <Loader2 className="w-4 h-4 animate-spin" />
            Loading credits…
          </div>
        )}

        {capStatus && (
          <section className="space-y-3">
            <h2 className="text-sm font-semibold text-slate-300 flex items-center gap-2">
              <ShieldAlert className="w-4 h-4 text-amber-300/90" />
              Caps & burn rate
            </h2>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              <div className="mc-panel p-4">
                <p className="text-[10px] uppercase tracking-wide text-slate-500">24h spend</p>
                <p className="text-xl font-mono text-slate-100 mt-1">
                  ${capStatus.last_24h_spend_usd?.toFixed(4) ?? '0'}
                </p>
              </div>
              <div className="mc-panel p-4">
                <p className="text-[10px] uppercase tracking-wide text-slate-500">Burn / hour</p>
                <p className="text-xl font-mono text-amber-200/90 mt-1">
                  ${capStatus.burn_rate_usd_per_hour?.toFixed(4) ?? '0'}
                </p>
              </div>
              <div className="mc-panel p-4">
                <p className="text-[10px] uppercase tracking-wide text-slate-500">Daily cap</p>
                <p className="text-xl font-mono text-slate-100 mt-1">
                  {capStatus.daily_cap_usd != null ? `$${capStatus.daily_cap_usd}` : '—'}
                </p>
                {capStatus.daily_cap_breach && (
                  <p className="text-[11px] text-red-400 mt-1">Over daily cap</p>
                )}
              </div>
              <div className="mc-panel p-4">
                <p className="text-[10px] uppercase tracking-wide text-slate-500">I2V / hour limit</p>
                <p className="text-xl font-mono text-slate-100 mt-1">
                  {capStatus.wavespeed_max_i2v_per_hour != null
                    ? capStatus.wavespeed_max_i2v_per_hour
                    : '—'}
                </p>
              </div>
            </div>
            {capStatus.notes?.length > 0 && (
              <ul className="text-xs text-slate-500 list-disc pl-5 space-y-1">
                {capStatus.notes.map((n) => (
                  <li key={n}>{n}</li>
                ))}
              </ul>
            )}
          </section>
        )}

        {summary && (
          <section className="space-y-3">
            <div className="flex flex-wrap items-end justify-between gap-3">
              <div>
                <h2 className="text-sm font-semibold text-slate-300 flex items-center gap-2">
                  <LayoutDashboard className="w-4 h-4 text-klipaura-300" />
                  Provider summary
                </h2>
                <p className="text-xs text-slate-500 mt-1">
                  Last {summary.since_hours}h · total ${summary.total_usd?.toFixed(4) ?? '0'} (estimates
                  included)
                </p>
              </div>
            </div>
            <CreditsProviderTable providers={summary.providers ?? {}} />
          </section>
        )}

        {byAvatar && Object.keys(byAvatar).length > 0 && (
          <section className="space-y-3">
            <h2 className="text-sm font-semibold text-slate-300">Per-avatar spend</h2>
            <div className="mc-panel divide-y divide-slate-800/80">
              {Object.entries(byAvatar).map(([av, amt]) => (
                <div key={av} className="flex justify-between px-4 py-2.5 text-sm">
                  <span className="font-mono text-slate-300">{av}</span>
                  <span className="font-mono text-klipaura-200/90">${amt.toFixed(4)}</span>
                </div>
              ))}
            </div>
          </section>
        )}

        <section className="space-y-3">
          <h2 className="text-sm font-semibold text-slate-300 flex items-center gap-2">
            <Search className="w-4 h-4 text-slate-400" />
            Job cost trace
          </h2>
          <div className="flex flex-wrap gap-2 items-center">
            <input
              type="text"
              placeholder="job_id"
              value={jobTraceId}
              onChange={(e) => setJobTraceId(e.target.value)}
              className="flex-1 min-w-[200px] px-3 py-2 rounded-lg bg-slate-950 border border-slate-700 text-slate-100 text-sm font-mono"
            />
            <button
              type="button"
              onClick={loadJobTrace}
              disabled={jobTraceLoading}
              className="px-4 py-2 rounded-lg bg-klipaura-600 hover:bg-klipaura-500 text-white text-sm font-medium disabled:opacity-50"
            >
              {jobTraceLoading ? 'Loading…' : 'Load'}
            </button>
          </div>
          {jobTrace && (
            <div className="mc-panel p-4 space-y-2">
              <p className="text-xs text-slate-500">
                Job <span className="font-mono text-slate-300">{jobTrace.job_id}</span> · total{' '}
                <span className="font-mono text-klipaura-200">${jobTrace.total_usd?.toFixed(4)}</span>
              </p>
              {jobTrace.events?.length ? (
                <ul className="text-xs space-y-2 max-h-64 overflow-y-auto font-mono text-slate-400">
                  {jobTrace.events.map((ev, i) => (
                    <li key={ev.id || `${ev.ts}-${i}`} className="border-b border-slate-800/60 pb-2">
                      <span className="text-slate-300">{ev.provider}</span> · {ev.operation} · $
                      {Number(ev.amount_usd ?? 0).toFixed(4)}
                      {ev.estimate ? <span className="text-amber-400/90"> (est.)</span> : null}
                      {ev.ts ? <span className="block text-[10px] text-slate-600">{ev.ts}</span> : null}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-sm text-slate-500">No cost rows for this job yet.</p>
              )}
            </div>
          )}
        </section>
      </main>
    </div>
  )
}
