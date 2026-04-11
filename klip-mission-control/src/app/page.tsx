'use client'

import Link from 'next/link'
import { useState, useEffect, useCallback, useMemo, type FormEvent } from 'react'
import {
  Activity,
  Zap,
  User,
  ShoppingBag,
  Rocket,
  TrendingUp,
  AlertTriangle,
  Skull,
  CheckCircle,
  XCircle,
  Clock,
  BarChart3,
  LayoutDashboard,
  ExternalLink,
  LogOut,
  PanelLeftClose,
  Radar,
  Sparkles,
  Coins,
  Film,
  Users,
  Workflow,
} from 'lucide-react'
import { RunPipelinePanel } from '../components/RunPipelinePanel'
import { PregenDecisionPanel } from '../components/PregenDecisionPanel'
import {
  getApiBase,
  getGitSha,
  getHitlPublicUrl,
  mcSkipLogin,
  MC_DEFAULT_LOGIN_PASSWORD,
  MC_DEFAULT_LOGIN_USERNAME,
} from '../lib/mc-client'

interface Module {
  name: string
  enabled: boolean
  status: string
  jobs_processed: number
  jobs_failed: number
}

interface Job {
  id: string
  module: string
  job_type: string
  status: string
  progress: number
  created_at: string
  hitl_requested: boolean
  payload?: Record<string, unknown>
  result?: Record<string, unknown> | null
}

/** Same field order as ``hitl_server._video_url_for_job`` (HTTP URLs only). */
function videoPreviewUrl(job: Job): string | null {
  const result = job.result
  const payload = job.payload || {}
  for (const key of ['r2_url', 'video_url', 'public_video_url'] as const) {
    const a = (result?.[key] ?? payload[key]) as unknown
    if (typeof a === 'string' && a.trim().startsWith('http')) return a.trim()
  }
  return null
}

interface Event {
  id: string
  module: string
  event_type: string
  severity: string
  message: string
  timestamp: string
}

interface Metrics {
  total_jobs: number
  jobs_by_status: Record<string, number>
  active_kill_switches: string[]
  redis_connected: boolean
  uptime_seconds: number
}

interface QueueOverview {
  jobs_pending: number
  hitl_pending: number
  dlq: number
  global_paused: boolean
}

const moduleIcons: Record<string, React.ReactNode> = {
  'klip-selector': <ShoppingBag className="w-5 h-5" />,
  'klip-avatar': <User className="w-5 h-5" />,
  'klip-funnel': <TrendingUp className="w-5 h-5" />,
  'klip-aventure': <Rocket className="w-5 h-5" />,
  'klip-trader': <TrendingUp className="w-5 h-5 opacity-60" />,
}

function StatusBadge({ status }: { status: string }) {
  const styles: Record<string, string> = {
    running: 'bg-emerald-500/15 text-emerald-300 border-emerald-500/35',
    completed: 'bg-klipaura-500/15 text-klipaura-200 border-klipaura-500/35',
    failed: 'bg-red-500/15 text-red-300 border-red-500/35',
    pending: 'bg-amber-500/15 text-amber-200 border-amber-500/35',
    awaiting_hitl: 'bg-klipaura-500/20 text-klipaura-200 border-klipaura-500/40',
    cancelled: 'bg-slate-500/20 text-slate-400 border-slate-600',
  }

  return (
    <span
      className={`px-2 py-1 rounded-full text-xs font-medium border ${
        styles[status] || styles.pending
      }`}
    >
      {status.replace(/_/g, ' ').toUpperCase()}
    </span>
  )
}

function SeverityBadge({ severity }: { severity: string }) {
  const s = (severity || 'info').toLowerCase()
  const colors: Record<string, string> = {
    debug: 'text-slate-500',
    info: 'text-klipaura-400',
    success: 'text-emerald-400',
    warning: 'text-amber-400',
    error: 'text-red-400',
    critical: 'text-red-500 font-bold',
  }
  return (
    <span className={`text-xs font-mono ${colors[s] || colors.info}`}>{severity}</span>
  )
}

function getToken(): string | null {
  if (typeof window === 'undefined') return null
  return sessionStorage.getItem('mc_token')
}

function setToken(t: string | null) {
  if (typeof window === 'undefined') return
  if (t) sessionStorage.setItem('mc_token', t)
  else sessionStorage.removeItem('mc_token')
}

type MainTab = 'overview' | 'run' | 'decisions' | 'hitl' | 'jobs' | 'events'

export default function MissionControl() {
  const [modules, setModules] = useState<Module[]>([])
  const [jobs, setJobs] = useState<Job[]>([])
  const [events, setEvents] = useState<Event[]>([])
  const [metrics, setMetrics] = useState<Metrics | null>(null)
  const [queues, setQueues] = useState<QueueOverview | null>(null)
  const [killSwitchActive, setKillSwitchActive] = useState(false)
  const [activeTab, setActiveTab] = useState<MainTab>('overview')
  const [loading, setLoading] = useState(true)
  const [loginUser, setLoginUser] = useState(MC_DEFAULT_LOGIN_USERNAME)
  const [loginPass, setLoginPass] = useState(MC_DEFAULT_LOGIN_PASSWORD)
  const [authError, setAuthError] = useState('')
  const [loggedIn, setLoggedIn] = useState(() => mcSkipLogin())
  const [toast, setToast] = useState<{ type: 'ok' | 'err'; text: string } | null>(null)
  const [jobActionId, setJobActionId] = useState<string | null>(null)
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false)
  const [selectedHitlId, setSelectedHitlId] = useState<string | null>(null)

  const hitlJobs = useMemo(
    () => jobs.filter((j) => j.status === 'awaiting_hitl'),
    [jobs],
  )

  const selectedHitlJob = useMemo(
    () => hitlJobs.find((j) => j.id === selectedHitlId) ?? hitlJobs[0] ?? null,
    [hitlJobs, selectedHitlId],
  )

  useEffect(() => {
    if (hitlJobs.length === 0) {
      setSelectedHitlId(null)
      return
    }
    if (!selectedHitlId || !hitlJobs.some((j) => j.id === selectedHitlId)) {
      setSelectedHitlId(hitlJobs[0].id)
    }
  }, [hitlJobs, selectedHitlId])

  // Empty = same-origin (Next rewrites /api/v1 → main API; /api/jobs,/api/avatars → HITL).
  // Set NEXT_PUBLIC_API_BASE_URL when UI and API are on different origins (see docs/MISSION_CONTROL.md).
  const baseUrl = getApiBase()
  const hitlUrl = getHitlPublicUrl()

  const showToast = useCallback((type: 'ok' | 'err', text: string) => {
    setToast({ type, text })
    window.setTimeout(() => setToast(null), 6000)
  }, [])

  const apiFetch = useCallback(
    async (path: string, init?: RequestInit) => {
      const headers = new Headers(init?.headers)
      headers.set('Content-Type', headers.get('Content-Type') || 'application/json')
      const tok = getToken()
      if (tok) headers.set('Authorization', `Bearer ${tok}`)
      return fetch(`${baseUrl}${path}`, { ...init, headers })
    },
    [baseUrl],
  )

  const refreshJobs = useCallback(async () => {
    const jobsRes = await apiFetch('/api/v1/jobs?limit=50').catch(() => ({ ok: false, json: async () => [] }))
    if (jobsRes.ok) setJobs(await jobsRes.json())
  }, [apiFetch])

  useEffect(() => {
    const boot = async () => {
      if (mcSkipLogin()) {
        setLoggedIn(true)
        return
      }
      if (typeof window !== 'undefined' && getToken()) {
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

  useEffect(() => {
    if (!loggedIn) {
      setLoading(false)
      return
    }
    const bootSignal = (): AbortSignal | undefined => {
      if (typeof AbortSignal !== 'undefined' && 'timeout' in AbortSignal) {
        return AbortSignal.timeout(22_000)
      }
      return undefined
    }
    const fetchData = async () => {
      const sig = bootSignal()
      const init = sig ? { signal: sig } : {}
      try {
        const [modulesRes, jobsRes, eventsRes, metricsRes, killRes, qRes] = await Promise.all([
          apiFetch('/api/v1/modules', init).catch(() => ({ ok: false, json: async () => [] })),
          apiFetch('/api/v1/jobs?limit=50', init).catch(() => ({ ok: false, json: async () => [] })),
          apiFetch('/api/v1/events?limit=50', init).catch(() => ({ ok: false, json: async () => [] })),
          apiFetch('/api/v1/metrics', init).catch(() => ({ ok: false, json: async () => ({}) })),
          apiFetch('/api/v1/kill-switch', init).catch(() => ({ ok: false, json: async () => [] })),
          apiFetch('/api/v1/queues/overview', init).catch(() => ({ ok: false, json: async () => null })),
        ])

        if (modulesRes.ok) setModules(await modulesRes.json())
        if (jobsRes.ok) setJobs(await jobsRes.json())
        if (eventsRes.ok) setEvents(await eventsRes.json())
        if (metricsRes.ok) setMetrics(await metricsRes.json())
        if (killRes.ok) {
          const killData = await killRes.json()
          setKillSwitchActive(killData.length > 0)
        }
        if (qRes.ok) setQueues(await qRes.json())
      } catch (err) {
        console.error('Failed to fetch data:', err)
      } finally {
        setLoading(false)
      }
    }

    fetchData()
    const pollSig = (): AbortSignal | undefined => {
      if (typeof AbortSignal !== 'undefined' && 'timeout' in AbortSignal) {
        return AbortSignal.timeout(18_000)
      }
      return undefined
    }
    const interval = setInterval(async () => {
      const ps = pollSig()
      const init = ps ? { signal: ps } : {}
      try {
        const [modulesRes, jobsRes, metricsRes, killRes, qRes] = await Promise.all([
          apiFetch('/api/v1/modules', init).catch(() => ({ ok: false, json: async () => [] })),
          apiFetch('/api/v1/jobs?limit=50', init).catch(() => ({ ok: false, json: async () => [] })),
          apiFetch('/api/v1/metrics', init).catch(() => ({ ok: false, json: async () => ({}) })),
          apiFetch('/api/v1/kill-switch', init).catch(() => ({ ok: false, json: async () => [] })),
          apiFetch('/api/v1/queues/overview', init).catch(() => ({ ok: false, json: async () => null })),
        ])
        if (modulesRes.ok) setModules(await modulesRes.json())
        if (jobsRes.ok) setJobs(await jobsRes.json())
        if (metricsRes.ok) setMetrics(await metricsRes.json())
        if (killRes.ok) {
          const killData = await killRes.json()
          setKillSwitchActive(killData.length > 0)
        }
        if (qRes.ok) setQueues(await qRes.json())
      } catch (err) {
        console.error('Poll failed:', err)
      }
    }, 5000)
    return () => clearInterval(interval)
  }, [loggedIn, apiFetch])

  useEffect(() => {
    if (!loggedIn) return
    const es = new EventSource(`${baseUrl}/api/events/stream`)
    es.onmessage = (e) => {
      try {
        const event = JSON.parse(e.data) as Event
        setEvents((prev) =>
          [{ ...event, id: event.id || crypto.randomUUID?.() || String(Date.now()) }, ...prev].slice(0, 50),
        )
      } catch {
        /* ignore */
      }
    }
    es.onerror = () => {
      es.close()
    }
    return () => {
      es.close()
    }
  }, [loggedIn, baseUrl])

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
        setToken(token)
        setLoggedIn(true)
        setLoading(true)
      }
    } catch {
      setAuthError('Network error')
    }
  }

  const handleLogout = () => {
    setToken(null)
    if (mcSkipLogin()) {
      setModules([])
      setJobs([])
      setEvents([])
      return
    }
    setLoggedIn(false)
    setModules([])
    setJobs([])
    setEvents([])
  }

  const handleKillSwitch = async () => {
    if (!confirm('EMERGENCY STOP: This will halt ALL modules. Continue?')) return

    try {
      await apiFetch('/api/v1/kill-switch', {
        method: 'POST',
        body: JSON.stringify({ scope: 'global', reason: 'Manual emergency stop from Mission Control' }),
      })
      setKillSwitchActive(true)
      showToast('ok', 'Global kill switch activated.')
    } catch (err) {
      console.error('Failed to trigger kill switch:', err)
      showToast('err', 'Could not trigger kill switch.')
    }
  }

  const handleClearKillSwitch = async () => {
    try {
      await apiFetch('/api/v1/kill-switch?scope=global', { method: 'DELETE' })
      setKillSwitchActive(false)
      showToast('ok', 'Kill switch cleared.')
    } catch (err) {
      console.error('Failed to clear kill switch:', err)
      showToast('err', 'Could not clear kill switch.')
    }
  }

  const handleQueuePause = async () => {
    try {
      await apiFetch('/api/v1/queue/pause', { method: 'POST' })
      const r = await apiFetch('/api/v1/queues/overview')
      if (r.ok) setQueues(await r.json())
      showToast('ok', 'Global queue paused.')
    } catch (err) {
      console.error('Pause failed:', err)
      showToast('err', 'Pause failed.')
    }
  }

  const handleQueueResume = async () => {
    try {
      await apiFetch('/api/v1/queue/pause', { method: 'DELETE' })
      const r = await apiFetch('/api/v1/queues/overview')
      if (r.ok) setQueues(await r.json())
      showToast('ok', 'Global queue resumed.')
    } catch (err) {
      console.error('Resume failed:', err)
      showToast('err', 'Resume failed.')
    }
  }

  const handleApproveJob = async (jobId: string) => {
    setJobActionId(jobId)
    try {
      const r = await apiFetch(`/api/v1/jobs/${encodeURIComponent(jobId)}/approve`, { method: 'POST' })
      if (!r.ok) {
        const t = await r.text()
        showToast('err', t || 'Approve failed')
        return
      }
      showToast('ok', 'Job approved and re-queued.')
      await refreshJobs()
    } catch (e) {
      showToast('err', e instanceof Error ? e.message : 'Approve failed')
    } finally {
      setJobActionId(null)
    }
  }

  const handleRejectJob = async (jobId: string) => {
    const reason = window.prompt('Rejection reason (optional):') ?? ''
    setJobActionId(jobId)
    try {
      const q = reason.trim() ? `?reason=${encodeURIComponent(reason.trim())}` : ''
      const r = await apiFetch(`/api/v1/jobs/${encodeURIComponent(jobId)}/reject${q}`, { method: 'POST' })
      if (!r.ok) {
        const t = await r.text()
        showToast('err', t || 'Reject failed')
        return
      }
      showToast('ok', 'Job rejected.')
      await refreshJobs()
    } catch (e) {
      showToast('err', e instanceof Error ? e.message : 'Reject failed')
    } finally {
      setJobActionId(null)
    }
  }

  const formatUptime = (seconds: number) => {
    const d = Math.floor(seconds / 86400)
    const h = Math.floor((seconds % 86400) / 3600)
    const m = Math.floor((seconds % 3600) / 60)
    return d > 0 ? `${d}d ${h}h ${m}m` : h > 0 ? `${h}h ${m}m` : `${m}m`
  }

  const formatTime = (ts: string) => {
    const date = new Date(ts)
    return Number.isNaN(date.getTime()) ? ts : date.toLocaleString()
  }

  const navBtn = (tab: MainTab, label: string, icon: React.ReactNode) => (
    <button
      type="button"
      key={tab}
      onClick={() => setActiveTab(tab)}
      className={`w-full flex items-center gap-3 px-3 py-2.5 min-h-11 rounded-lg text-sm font-medium transition-colors touch-manipulation ${
        activeTab === tab
          ? 'bg-klipaura-600/25 text-slate-100 border border-klipaura-500/40'
          : 'text-slate-400 hover:text-slate-100 hover:bg-slate-800/80 border border-transparent'
      }`}
    >
      {icon}
      {!sidebarCollapsed && label}
    </button>
  )

  const mobileNavBtn = (tab: MainTab, short: string, icon: React.ReactNode) => (
    <button
      type="button"
      key={`m-${tab}`}
      onClick={() => setActiveTab(tab)}
      className={`flex flex-col items-center justify-center gap-0.5 min-h-[3.25rem] px-0.5 rounded-xl text-[10px] font-semibold transition-colors touch-manipulation leading-tight ${
        activeTab === tab
          ? 'text-slate-100 bg-klipaura-600/25 border border-klipaura-500/40'
          : 'text-slate-500 border border-transparent active:bg-slate-800/80'
      }`}
    >
      <span className="shrink-0">{icon}</span>
      <span className="text-center w-full truncate px-0.5">{short}</span>
    </button>
  )

  if (!loggedIn && !mcSkipLogin()) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-slate-950 px-4">
        <div className="mc-panel p-8 max-w-md w-full">
          <h1 className="text-2xl font-bold mb-1 text-center text-slate-100">Mission Control</h1>
          <p className="text-slate-500 text-sm mb-6 text-center">KLIPAURA OS — operator sign-in</p>
          <form onSubmit={handleLogin} className="space-y-4">
            <input
              type="text"
              placeholder="Username"
              value={loginUser}
              onChange={(e) => setLoginUser(e.target.value)}
              className="w-full px-4 py-2.5 rounded-lg bg-slate-950 border border-slate-600 text-slate-100 text-sm focus:ring-2 focus:ring-klipaura-500/50 focus:border-klipaura-500 outline-none"
            />
            <input
              type="password"
              placeholder="Password"
              value={loginPass}
              onChange={(e) => setLoginPass(e.target.value)}
              className="w-full px-4 py-2.5 rounded-lg bg-slate-950 border border-slate-600 text-slate-100 text-sm focus:ring-2 focus:ring-klipaura-500/50 focus:border-klipaura-500 outline-none"
            />
            {authError && <p className="text-red-400 text-sm">{authError}</p>}
            <button
              type="submit"
              className="w-full py-2.5 rounded-lg bg-gradient-to-r from-klipaura-600 to-klipaura-400 hover:from-klipaura-500 hover:to-klipaura-300 text-white font-semibold text-sm"
            >
              Sign in
            </button>
          </form>
          <p className="text-xs text-slate-600 mt-4 text-center leading-relaxed">
            Credentials are read from <code className="text-slate-500">MC_ADMIN_USER</code> /{' '}
            <code className="text-slate-500">MC_ADMIN_PASSWORD</code> (or AGENTS.md aliases{' '}
            <code className="text-slate-500">ADMIN_USERNAME</code> / <code className="text-slate-500">ADMIN_PASSWORD</code>
            ). Values must match exactly (case-sensitive). Set <code className="text-slate-500">JWT_SECRET_KEY</code>{' '}
            on the server. If this still fails, inspect Network →{' '}
            <code className="text-slate-500">/api/v1/auth/login</code> (401 = wrong user/pass or empty password env).
          </p>
          <p className="text-[10px] text-slate-700 mt-3 text-center font-mono tracking-wide" id="mc-build-sha">
            build {getGitSha()}
          </p>
        </div>
      </div>
    )
  }

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-slate-950">
        <div className="text-center">
          <div className="spinner mx-auto mb-4"></div>
          <p className="text-slate-500 text-sm">Connecting to Mission Control…</p>
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-[100dvh] min-h-screen flex bg-slate-950 text-slate-100 relative">
      {toast && (
        <div
          className={`fixed top-4 right-4 z-[100] max-w-md px-4 py-3 rounded-lg shadow-xl border text-sm ${
            toast.type === 'ok'
              ? 'bg-emerald-950/95 border-emerald-700 text-emerald-100'
              : 'bg-red-950/95 border-red-800 text-red-100'
          }`}
          role="status"
        >
          {toast.text}
        </div>
      )}

      <aside
        className={`hidden md:flex shrink-0 border-r border-slate-800 bg-slate-900/95 flex-col transition-all duration-200 ${
          sidebarCollapsed ? 'w-[4.5rem]' : 'w-56'
        }`}
      >
        <div className="p-3 border-b border-slate-800 flex items-center gap-2">
          <Zap className="w-8 h-8 text-klipaura-400 shrink-0" />
          {!sidebarCollapsed && (
            <div>
              <div className="font-bold text-slate-100 leading-tight">KLIPAURA</div>
              <div className="text-[10px] text-slate-500 uppercase tracking-wider">Mission Control</div>
            </div>
          )}
        </div>
        <nav className="flex-1 p-2 space-y-1 flex flex-col">
          {navBtn('overview', 'Overview', <LayoutDashboard className="w-5 h-5 shrink-0" />)}
          <Link
            href="/avatar-studio"
            className="w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-colors text-cyan-300/95 hover:bg-cyan-950/30 border border-transparent hover:border-cyan-500/25"
          >
            <Sparkles className="w-5 h-5 shrink-0" />
            {!sidebarCollapsed && 'Avatar Studio'}
          </Link>
          <Link
            href="/credits"
            className="w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-colors text-amber-200/90 hover:bg-amber-500/10 border border-transparent hover:border-amber-500/25"
          >
            <Coins className="w-5 h-5 shrink-0" />
            {!sidebarCollapsed && 'Credits'}
          </Link>
          <Link
            href="/mc/avatars"
            className="w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-colors text-sky-200/90 hover:bg-sky-500/10 border border-transparent hover:border-sky-500/25"
          >
            <Users className="w-5 h-5 shrink-0" />
            {!sidebarCollapsed && 'Avatar Management'}
          </Link>
          <Link
            href="/mc/pipeline"
            className="w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-colors text-emerald-200/90 hover:bg-emerald-500/10 border border-transparent hover:border-emerald-500/25"
          >
            <Workflow className="w-5 h-5 shrink-0" />
            {!sidebarCollapsed && 'Affiliate Pipeline'}
          </Link>
          {navBtn('run', 'Run pipeline', <Radar className="w-5 h-5 shrink-0" />)}
          {navBtn('decisions', 'Decision queue', <AlertTriangle className="w-5 h-5 shrink-0" />)}
          {navBtn('hitl', 'HITL queue', <Film className="w-5 h-5 shrink-0" />)}
          {navBtn('jobs', 'Jobs', <Activity className="w-5 h-5 shrink-0" />)}
          {navBtn('events', 'Events', <Zap className="w-5 h-5 shrink-0" />)}
          {hitlUrl ? (
            <a
              href={hitlUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-3 px-3 py-2 rounded-lg text-xs text-slate-500 hover:text-klipaura-400 border border-transparent hover:border-slate-700"
              title="Optional standalone HITL server (same approve/reject via Mission Control API)"
            >
              <ExternalLink className="w-4 h-4 shrink-0 opacity-70" />
              {!sidebarCollapsed && 'Legacy HITL UI'}
            </a>
          ) : null}
        </nav>
        <div className="p-2 border-t border-slate-800">
          <button
            type="button"
            onClick={() => setSidebarCollapsed((c) => !c)}
            className="w-full flex items-center justify-center gap-2 py-2 text-slate-500 hover:text-slate-300 text-xs"
            title="Toggle sidebar"
          >
            <PanelLeftClose className={`w-4 h-4 transition-transform ${sidebarCollapsed ? 'rotate-180' : ''}`} />
          </button>
          {!mcSkipLogin() && (
            <button
              type="button"
              onClick={handleLogout}
              className="w-full flex items-center gap-3 px-3 py-2 rounded-lg text-sm text-slate-500 hover:text-slate-200 hover:bg-slate-800"
            >
              <LogOut className="w-5 h-5 shrink-0" />
              {!sidebarCollapsed && 'Log out'}
            </button>
          )}
        </div>
      </aside>

      <div className="flex-1 flex flex-col min-w-0">
        <header className="border-b border-slate-800 bg-slate-900/80 backdrop-blur-sm sticky top-0 z-40 px-3 sm:px-4 py-3 flex flex-wrap items-center gap-3 justify-between safe-pt">
          <div className="flex flex-wrap items-center gap-4 text-sm">
            <span className={`status-dot ${metrics?.redis_connected ? 'online' : 'offline'}`} />
            <span className="text-slate-400">
              Redis {metrics?.redis_connected ? 'connected' : 'disconnected'}
            </span>
            <span className="text-slate-600">|</span>
            <span className="flex items-center gap-1.5 text-slate-400">
              <Clock className="w-4 h-4" />
              {metrics ? formatUptime(metrics.uptime_seconds) : '—'}
            </span>
            {queues && (
              <>
                <span className="text-slate-600 hidden sm:inline">|</span>
                <span className="text-slate-500 hidden sm:inline">
                  Pending <span className="text-slate-200 font-mono">{queues.jobs_pending}</span> · HITL{' '}
                  <span className="text-slate-200 font-mono">{queues.hitl_pending}</span> · DLQ{' '}
                  <span className="text-amber-400/90 font-mono">{queues.dlq}</span>
                </span>
              </>
            )}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {queues?.global_paused ? (
              <button
                type="button"
                onClick={handleQueueResume}
                className="px-3 py-1.5 rounded-lg bg-amber-600/90 hover:bg-amber-500 text-white text-xs font-semibold"
              >
                Resume queue
              </button>
            ) : (
              <button
                type="button"
                onClick={handleQueuePause}
                className="px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs"
              >
                Pause queue
              </button>
            )}
            {killSwitchActive ? (
              <button
                type="button"
                onClick={handleClearKillSwitch}
                className="px-3 py-1.5 rounded-lg bg-emerald-700 hover:bg-emerald-600 text-white text-xs font-semibold inline-flex items-center gap-1"
              >
                <CheckCircle className="w-3.5 h-3.5" />
                Clear kill
              </button>
            ) : (
              <button
                type="button"
                onClick={handleKillSwitch}
                className="px-3 py-1.5 rounded-lg bg-red-700 hover:bg-red-600 text-white text-xs font-semibold inline-flex items-center gap-1"
              >
                <Skull className="w-3.5 h-3.5" />
                Emergency stop
              </button>
            )}
          </div>
        </header>

        <div className="md:hidden border-b border-slate-800/90 bg-slate-950/80 px-2 py-2 overflow-x-auto scrollbar-touch flex gap-2 shrink-0">
          <Link
            href="/avatar-studio"
            className="shrink-0 inline-flex items-center gap-1.5 rounded-full border border-cyan-500/30 bg-cyan-950/30 px-3 py-2 text-xs font-medium text-cyan-100 touch-manipulation"
          >
            <Sparkles className="w-3.5 h-3.5" />
            Avatar Studio
          </Link>
          <Link
            href="/credits"
            className="shrink-0 inline-flex items-center gap-1.5 rounded-full border border-amber-500/25 bg-amber-950/20 px-3 py-2 text-xs font-medium text-amber-100/95 touch-manipulation"
          >
            <Coins className="w-3.5 h-3.5" />
            Credits
          </Link>
          <Link
            href="/mc/avatars"
            className="shrink-0 inline-flex items-center gap-1.5 rounded-full border border-sky-500/25 bg-sky-950/20 px-3 py-2 text-xs font-medium text-sky-100/95 touch-manipulation"
          >
            <Users className="w-3.5 h-3.5" />
            Avatars
          </Link>
          <Link
            href="/mc/pipeline"
            className="shrink-0 inline-flex items-center gap-1.5 rounded-full border border-emerald-500/25 bg-emerald-950/20 px-3 py-2 text-xs font-medium text-emerald-100/95 touch-manipulation"
          >
            <Workflow className="w-3.5 h-3.5" />
            Pipeline
          </Link>
        </div>

        <main className="flex-1 p-3 sm:p-4 md:p-6 overflow-y-auto pb-[calc(5.5rem+env(safe-area-inset-bottom))] md:pb-6">
          {activeTab === 'overview' && (
            <div className="space-y-8 max-w-7xl mx-auto">
              {(hitlJobs.length > 0 || (metrics?.jobs_by_status?.awaiting_hitl ?? 0) > 0) && (
                <div className="mc-panel p-4 flex flex-wrap items-center justify-between gap-3 border-klipaura-500/35 bg-klipaura-950/25">
                  <div>
                    <p className="text-sm font-medium text-klipaura-200">Human review (HITL)</p>
                    <p className="text-xs text-slate-500 mt-0.5">
                      {hitlJobs.length || metrics?.jobs_by_status?.awaiting_hitl || 0} job(s) awaiting approval
                      before publish.
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={() => setActiveTab('hitl')}
                    className="px-4 py-2 rounded-lg bg-klipaura-600 hover:bg-klipaura-500 text-white text-sm font-semibold inline-flex items-center gap-2"
                  >
                    <Film className="w-4 h-4" />
                    Open HITL queue
                  </button>
                </div>
              )}

              <div className="grid md:grid-cols-2 gap-4">
                <Link
                  href="/avatar?generate_funnel=1"
                  className="mc-panel p-6 border-klipaura-500/35 bg-gradient-to-br from-klipaura-950/50 to-slate-900/80 hover:border-klipaura-400/50 transition-colors group"
                >
                  <div className="flex items-start gap-4">
                    <div className="p-3 rounded-xl bg-klipaura-500/20 shrink-0">
                      <Rocket className="w-8 h-8 text-klipaura-300" />
                    </div>
                    <div className="min-w-0">
                      <h3 className="text-lg font-semibold text-slate-100 group-hover:text-klipaura-200">
                        Generate Video + Funnel
                      </h3>
                      <p className="text-sm text-slate-400 mt-1 leading-relaxed">
                        Paste an affiliate link, pick a saved Studio persona, and queue split-screen video plus a mobile
                        landing page (video embed, product copy, CTA, disclosure).
                      </p>
                      <p className="text-sm text-klipaura-400 font-medium mt-3">Open Avatar page →</p>
                    </div>
                  </div>
                </Link>
                <Link
                  href="/mc/pipeline"
                  className="mc-panel p-6 border-slate-700 hover:border-slate-500/60 transition-colors group"
                >
                  <div className="flex items-start gap-4">
                    <div className="p-3 rounded-xl bg-slate-800/80 shrink-0">
                      <TrendingUp className="w-8 h-8 text-emerald-300/90" />
                    </div>
                    <div className="min-w-0">
                      <h3 className="text-lg font-semibold text-slate-100 group-hover:text-emerald-200/90">
                        Affiliate Pipeline &amp; funnel URLs
                      </h3>
                      <p className="text-sm text-slate-400 mt-1 leading-relaxed">
                        Recent jobs list public video and hosted HTML funnel links from manifests when workers finish.
                      </p>
                      <p className="text-sm text-emerald-400/90 font-medium mt-3">View recent outputs →</p>
                    </div>
                  </div>
                </Link>
              </div>

              {queues && (
                <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                  {[
                    ['Pending', queues.jobs_pending, 'text-slate-100'],
                    ['HITL', queues.hitl_pending, 'text-klipaura-200'],
                    ['DLQ', queues.dlq, 'text-amber-400'],
                    ['Queue pause', queues.global_paused ? 'ON' : 'off', 'text-slate-300'],
                  ].map(([label, val, cls]) => (
                    <div key={label as string} className="mc-panel p-4">
                      <p className="text-slate-500 text-xs uppercase tracking-wide">{label as string}</p>
                      <p className={`text-2xl font-mono mt-1 ${cls}`}>{val as string | number}</p>
                    </div>
                  ))}
                </div>
              )}
              <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                <div className="mc-panel p-5">
                  <p className="text-slate-500 text-xs">Total jobs</p>
                  <p className="text-3xl font-bold text-slate-100 mt-1">{metrics?.total_jobs ?? 0}</p>
                </div>
                <div className="mc-panel p-5">
                  <p className="text-slate-500 text-xs">Running</p>
                  <p className="text-3xl font-bold text-emerald-400 mt-1">
                    {metrics?.jobs_by_status?.running ?? 0}
                  </p>
                </div>
                <div className="mc-panel p-5">
                  <p className="text-slate-500 text-xs">Pending</p>
                  <p className="text-3xl font-bold text-amber-200 mt-1">
                    {metrics?.jobs_by_status?.pending ?? 0}
                  </p>
                </div>
                <div className="mc-panel p-5">
                  <p className="text-slate-500 text-xs">Awaiting HITL</p>
                  <p className="text-3xl font-bold text-klipaura-300 mt-1">
                    {metrics?.jobs_by_status?.awaiting_hitl ?? 0}
                  </p>
                </div>
              </div>

              <div>
                <h2 className="text-lg font-semibold text-slate-100 mb-3 flex items-center gap-2">
                  <BarChart3 className="w-5 h-5 text-klipaura-400" />
                  Modules
                </h2>
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
                  {modules.map((mod) => (
                    <div key={mod.name} className="mc-panel p-4 hover:border-klipaura-500/30 transition-colors">
                      <div className="flex items-center gap-3 mb-3">
                        <div
                          className={`p-2 rounded-lg ${
                            mod.enabled ? 'bg-klipaura-500/20 text-klipaura-300' : 'bg-slate-800 text-slate-500'
                          }`}
                        >
                          {moduleIcons[mod.name] || <Zap className="w-5 h-5" />}
                        </div>
                        <div>
                          <h3 className="font-medium text-slate-100 capitalize">
                            {mod.name.replace('klip-', '')}
                          </h3>
                          <span className={`status-dot ${mod.enabled ? 'online' : 'offline'} mr-1`} />
                          <span className="text-xs text-slate-500">{mod.enabled ? 'Enabled' : 'Off'}</span>
                        </div>
                      </div>
                      <div className="text-xs text-slate-500 space-y-1">
                        <div className="flex justify-between">
                          <span>Processed</span>
                          <span className="font-mono text-slate-400">{mod.jobs_processed}</span>
                        </div>
                        <div className="flex justify-between">
                          <span>Failed</span>
                          <span className="font-mono text-red-400/80">{mod.jobs_failed}</span>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              </div>

              <div>
                <div className="flex items-center justify-between mb-3">
                  <h2 className="text-lg font-semibold text-slate-100">Recent activity</h2>
                  <button
                    type="button"
                    onClick={() => setActiveTab('events')}
                    className="text-sm text-klipaura-400 hover:text-klipaura-300"
                  >
                    View all →
                  </button>
                </div>
                <div className="mc-panel divide-y divide-slate-800/80">
                  {events.slice(0, 6).map((event) => (
                    <div key={event.id} className="event-entry flex items-center gap-3 px-3">
                      <SeverityBadge severity={event.severity} />
                      <span className="text-[10px] text-slate-600 font-mono w-16 shrink-0">
                        {(event.module || '').replace('klip-', '')}
                      </span>
                      <span className="flex-1 text-sm text-slate-300 truncate">{event.message}</span>
                      <span className="text-[10px] text-slate-600 shrink-0">{formatTime(event.timestamp)}</span>
                    </div>
                  ))}
                  {events.length === 0 && (
                    <div className="py-10 text-center text-slate-600 text-sm">No recent events</div>
                  )}
                </div>
              </div>
            </div>
          )}

          {activeTab === 'run' && (
            <div className="max-w-3xl mx-auto">
              <RunPipelinePanel
                apiFetch={apiFetch}
                onSuccess={(msg) => showToast('ok', msg)}
                onError={(msg) => showToast('err', msg)}
              />
            </div>
          )}

          {activeTab === 'decisions' && (
            <div className="max-w-5xl mx-auto">
              <PregenDecisionPanel
                apiFetch={apiFetch}
                onSuccess={(msg) => showToast('ok', msg)}
                onError={(msg) => showToast('err', msg)}
              />
            </div>
          )}

          {activeTab === 'hitl' && (
            <div className="max-w-6xl mx-auto space-y-6">
              <div>
                <h2 className="text-lg font-semibold text-slate-100">HITL queue</h2>
                <p className="text-sm text-slate-500 mt-1 max-w-2xl">
                  Preview outputs and approve or reject here — Mission Control is the single operator surface. Same API as
                  the Jobs tab: <code className="text-slate-400">POST /api/v1/jobs/:id/approve</code> and{' '}
                  <code className="text-slate-400">/reject</code>.
                </p>
              </div>
              {hitlJobs.length === 0 ? (
                <div className="mc-panel p-12 text-center text-slate-500 text-sm">
                  No jobs in <span className="text-klipaura-400">awaiting_hitl</span> status. When the avatar pipeline
                  requests review, they will appear here.
                </div>
              ) : (
                <div className="grid grid-cols-1 lg:grid-cols-5 gap-6">
                  <div className="lg:col-span-2 space-y-2">
                    <p className="text-xs text-slate-500 uppercase tracking-wide px-1">Pending review</p>
                    {hitlJobs.map((j) => (
                      <button
                        key={j.id}
                        type="button"
                        onClick={() => setSelectedHitlId(j.id)}
                        className={`w-full text-left mc-panel p-3 transition-colors ${
                          selectedHitlJob?.id === j.id
                            ? 'border-klipaura-500/50 bg-klipaura-950/30'
                            : 'hover:border-slate-600'
                        }`}
                      >
                        <p className="text-sm font-medium text-slate-200">{j.job_type}</p>
                        <p className="text-[11px] text-slate-500 font-mono truncate">{j.id}</p>
                        <p className="text-xs text-slate-500 mt-1 capitalize">
                          {j.module.replace('klip-', '')}
                        </p>
                      </button>
                    ))}
                  </div>
                  <div className="lg:col-span-3 mc-panel p-4 space-y-4">
                    {selectedHitlJob && (
                      <>
                        <div className="aspect-[9/16] max-h-[min(72vh,720px)] mx-auto bg-black rounded-lg overflow-hidden flex items-center justify-center border border-slate-800">
                          {videoPreviewUrl(selectedHitlJob) ? (
                            <video
                              key={selectedHitlJob.id}
                              src={videoPreviewUrl(selectedHitlJob)!}
                              controls
                              playsInline
                              className="w-full h-full object-contain"
                            />
                          ) : (
                            <p className="text-slate-500 text-sm px-4 text-center leading-relaxed">
                              No HTTP video URL on this job. Pipeline should set{' '}
                              <code className="text-klipaura-400">result.r2_url</code> or{' '}
                              <code className="text-klipaura-400">video_url</code> when moving to awaiting HITL.
                            </p>
                          )}
                        </div>
                        {typeof selectedHitlJob.payload?.product_url === 'string' && (
                          <p
                            className="text-xs text-slate-500 truncate"
                            title={selectedHitlJob.payload.product_url}
                          >
                            Product: {selectedHitlJob.payload.product_url}
                          </p>
                        )}
                        {selectedHitlJob.payload &&
                        (selectedHitlJob.payload as { generate_funnel?: boolean }).generate_funnel ? (
                          <p className="text-xs text-slate-500">
                            Landing page: check{' '}
                            <Link href="/mc/pipeline" className="text-klipaura-400 hover:underline">
                              Affiliate Pipeline
                            </Link>{' '}
                            for the hosted funnel URL when the job completes (manifest{' '}
                            <code className="text-slate-500">funnel_url</code>).
                          </p>
                        ) : null}
                        <div className="flex flex-wrap gap-3 justify-center pt-2">
                          <button
                            type="button"
                            disabled={jobActionId === selectedHitlJob.id}
                            onClick={() => handleApproveJob(selectedHitlJob.id)}
                            className="inline-flex items-center gap-2 px-5 py-2.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white font-semibold text-sm disabled:opacity-50"
                          >
                            <CheckCircle className="w-5 h-5" />
                            Approve
                          </button>
                          <button
                            type="button"
                            disabled={jobActionId === selectedHitlJob.id}
                            onClick={() => handleRejectJob(selectedHitlJob.id)}
                            className="inline-flex items-center gap-2 px-5 py-2.5 rounded-lg bg-red-600/90 hover:bg-red-500 text-white font-semibold text-sm disabled:opacity-50"
                          >
                            <XCircle className="w-5 h-5" />
                            Reject
                          </button>
                        </div>
                        {hitlUrl ? (
                          <p className="text-center text-xs text-slate-600 pt-2">
                            Optional standalone UI:{' '}
                            <a
                              href={hitlUrl}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="text-klipaura-400 hover:underline"
                            >
                              {hitlUrl.replace(/^https?:\/\//, '')}
                            </a>
                          </p>
                        ) : null}
                      </>
                    )}
                  </div>
                </div>
              )}
            </div>
          )}

          {activeTab === 'jobs' && (
            <div className="space-y-4 max-w-7xl mx-auto">
              <div className="flex items-center justify-between">
                <h2 className="text-lg font-semibold text-slate-100">Jobs</h2>
                <span className="text-slate-500 text-sm">{jobs.length} loaded</span>
              </div>
              <div className="mc-panel overflow-x-auto">
                <table className="w-full min-w-[640px]">
                  <thead>
                    <tr className="border-b border-slate-800 text-left text-[10px] uppercase tracking-wider text-slate-500">
                      <th className="px-4 py-3">Job</th>
                      <th className="px-4 py-3">Module</th>
                      <th className="px-4 py-3">Status</th>
                      <th className="px-4 py-3">Progress</th>
                      <th className="px-4 py-3">Created</th>
                      <th className="px-4 py-3">Actions</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-800/80">
                    {jobs.map((job) => (
                      <tr key={job.id} className="hover:bg-slate-800/30">
                        <td className="px-4 py-3">
                          <p className="font-medium text-slate-200 text-sm">{job.job_type}</p>
                          <p className="text-[11px] text-slate-600 font-mono">{job.id.slice(0, 12)}…</p>
                        </td>
                        <td className="px-4 py-3 text-sm text-slate-400 capitalize">
                          {job.module.replace('klip-', '')}
                        </td>
                        <td className="px-4 py-3">
                          <StatusBadge status={job.status} />
                        </td>
                        <td className="px-4 py-3">
                          <div className="flex items-center gap-2">
                            <div className="progress-bar w-20">
                              <div className="progress-bar-fill" style={{ width: `${job.progress}%` }} />
                            </div>
                            <span className="text-xs text-slate-500">{job.progress}%</span>
                          </div>
                        </td>
                        <td className="px-4 py-3 text-xs text-slate-500 whitespace-nowrap">
                          {formatTime(job.created_at)}
                        </td>
                        <td className="px-4 py-3">
                          {job.status === 'awaiting_hitl' ? (
                            <div className="flex gap-1">
                              <button
                                type="button"
                                disabled={jobActionId === job.id}
                                onClick={() => handleApproveJob(job.id)}
                                className="p-1.5 rounded-md text-emerald-400 hover:bg-emerald-500/10 disabled:opacity-40"
                                title="Approve"
                              >
                                <CheckCircle className="w-5 h-5" />
                              </button>
                              <button
                                type="button"
                                disabled={jobActionId === job.id}
                                onClick={() => handleRejectJob(job.id)}
                                className="p-1.5 rounded-md text-red-400 hover:bg-red-500/10 disabled:opacity-40"
                                title="Reject"
                              >
                                <XCircle className="w-5 h-5" />
                              </button>
                              {hitlUrl ? (
                                <a
                                  href={`${hitlUrl}/`}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  className="p-1.5 rounded-md text-klipaura-400 hover:bg-klipaura-500/15 inline-flex"
                                  title="Open HITL"
                                >
                                  <ExternalLink className="w-5 h-5" />
                                </a>
                              ) : (
                                <span
                                  className="p-1.5 rounded-md text-slate-600 inline-flex cursor-help"
                                  title="Set NEXT_PUBLIC_HITL_URL to your deployed HITL URL"
                                >
                                  <ExternalLink className="w-5 h-5 opacity-50" />
                                </span>
                              )}
                            </div>
                          ) : (
                            <span className="text-slate-600 text-xs">—</span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {jobs.length === 0 && (
                  <div className="py-16 text-center text-slate-600 text-sm">No jobs in Mission Control store</div>
                )}
              </div>
            </div>
          )}

          {activeTab === 'events' && (
            <div className="space-y-4 max-w-5xl mx-auto">
              <h2 className="text-lg font-semibold text-slate-100">Event log</h2>
              <div className="mc-panel max-h-[70vh] overflow-y-auto">
                {events.map((event) => (
                  <div key={event.id} className="event-entry px-4">
                    <div className="flex items-start gap-3">
                      <SeverityBadge severity={event.severity} />
                      <div className="flex-1 min-w-0">
                        <div className="flex flex-wrap items-center gap-2 mb-0.5">
                          <span className="text-[10px] bg-slate-800 text-slate-400 px-1.5 py-0.5 rounded font-mono">
                            {(event.module || '').replace('klip-', '')}
                          </span>
                          {event.event_type && (
                            <span className="text-[10px] text-klipaura-400">{event.event_type}</span>
                          )}
                        </div>
                        <p className="text-sm text-slate-300">{event.message}</p>
                      </div>
                      <span className="text-[10px] text-slate-600 shrink-0 whitespace-nowrap">
                        {formatTime(event.timestamp)}
                      </span>
                    </div>
                  </div>
                ))}
                {events.length === 0 && (
                  <div className="py-16 text-center text-slate-600 text-sm">No events</div>
                )}
              </div>
            </div>
          )}
        </main>

        <footer className="hidden md:block border-t border-slate-800 py-4 text-center text-slate-600 text-xs">
          KLIPAURA OS · Mission Control · build <span className="font-mono">{getGitSha()}</span>
        </footer>
      </div>

      <nav
        className="md:hidden fixed bottom-0 inset-x-0 z-50 border-t border-slate-800 bg-slate-900/95 backdrop-blur-md safe-pb grid grid-cols-6 gap-0.5 px-0.5 pt-1 shadow-[0_-12px_32px_rgba(0,0,0,0.35)]"
        aria-label="Mission Control sections"
      >
        {mobileNavBtn('overview', 'Home', <LayoutDashboard className="w-5 h-5" />)}
        {mobileNavBtn('run', 'Run', <Radar className="w-5 h-5" />)}
        {mobileNavBtn('decisions', 'Queue', <AlertTriangle className="w-5 h-5" />)}
        {mobileNavBtn('hitl', 'HITL', <Film className="w-5 h-5" />)}
        {mobileNavBtn('jobs', 'Jobs', <Activity className="w-5 h-5" />)}
        {mobileNavBtn('events', 'Events', <Zap className="w-5 h-5" />)}
      </nav>
    </div>
  )
}
