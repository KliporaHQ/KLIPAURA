'use client'

import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { ExternalLink } from 'lucide-react'
import { McPageHeader } from '@/components/McPageHeader'
import { createApiFetch, getApiBase, getMcToken, mcSkipLogin } from '@/lib/mc-client'

/**
 * next.config.js rewrites `/api/dashboard/*` → HITL (`MC_INTERNAL_HITL_URL`, default :8080).
 * When HITL is down, the browser request via same-origin can hang until TCP times out unless we abort.
 * After a fast abort, we fall back to Mission Control FastAPI on :8000 (same handlers in main.py).
 */
const HITL_PROXY_TIMEOUT_MS = 1800

function missionControlDirectBase(): string {
  return (process.env.NEXT_PUBLIC_MC_DIRECT_URL || 'http://127.0.0.1:8000').replace(/\/$/, '')
}

type RecentJob = {
  job_id: string
  status?: string
  avatar_id?: string
  product_url?: string
  video_url?: string | null
  funnel_url?: string | null
  affiliate_program_id?: string
  generate_funnel?: boolean
}

export default function AffiliatePipelinePage() {
  const base = getApiBase()
  const api = createApiFetch(base)
  const [authed, setAuthed] = useState(() => mcSkipLogin())
  const [recent, setRecent] = useState<RecentJob[]>([])
  const [loadingJobs, setLoadingJobs] = useState(true)
  const [hitlDown, setHitlDown] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [message, setMessage] = useState<{ type: 'ok' | 'err'; text: string } | null>(null)

  const [productUrl, setProductUrl] = useState(
    'https://example.com/product/sample-item-123',
  )
  const [avatarId, setAvatarId] = useState('theanikaglow')
  const [affiliateProgramId, setAffiliateProgramId] = useState('example_program')
  const [productIdField, setProductIdField] = useState('sample-item-123')
  const [affiliateTag, setAffiliateTag] = useState('demo-tag-01')
  const [generateFunnel, setGenerateFunnel] = useState(false)

  const loadRecent = useCallback(async () => {
    setLoadingJobs(true)
    setHitlDown(false)
    const ac = new AbortController()
    const timer = window.setTimeout(() => {
      ac.abort()
    }, HITL_PROXY_TIMEOUT_MS)

    const fetchRecentFromMissionControlDirect = async (): Promise<boolean> => {
      const headers = new Headers({ 'Content-Type': 'application/json' })
      const tok = getMcToken()
      if (tok) headers.set('Authorization', `Bearer ${tok}`)
      const r = await fetch(`${missionControlDirectBase()}/api/dashboard/recent-jobs?limit=30`, {
        headers,
      })
      if (!r.ok) return false
      const j = (await r.json()) as { jobs?: RecentJob[] }
      setRecent(Array.isArray(j.jobs) ? j.jobs : [])
      return true
    }

    try {
      const r = await api('/api/dashboard/recent-jobs?limit=30', { signal: ac.signal })
      window.clearTimeout(timer)
      if (r.ok) {
        const j = (await r.json()) as { jobs?: RecentJob[] }
        setRecent(Array.isArray(j.jobs) ? j.jobs : [])
        return
      }
      setHitlDown(true)
      setLoadingJobs(false)
      try {
        await fetchRecentFromMissionControlDirect()
      } catch {
        setRecent([])
      }
      return
    } catch {
      window.clearTimeout(timer)
      setHitlDown(true)
      setLoadingJobs(false)
      try {
        await fetchRecentFromMissionControlDirect()
      } catch {
        setRecent([])
      }
    } finally {
      window.clearTimeout(timer)
      setLoadingJobs(false)
    }
  }, [api])

  useEffect(() => {
    if (!mcSkipLogin() && typeof window !== 'undefined' && !getMcToken()) {
      window.location.href = '/'
      return
    }
    setAuthed(true)
    void loadRecent()
  }, [loadRecent])

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    setSubmitting(true)
    setMessage(null)
    const body: Record<string, unknown> = {
      product_url: productUrl.trim(),
      avatar_id: avatarId.trim() || 'theanikaglow',
      affiliate_program_id: affiliateProgramId.trim() || undefined,
      affiliate_fields: {
        product_id: productIdField.trim(),
        affiliate_tag: affiliateTag.trim(),
      },
      layout_mode: 'affiliate_split_55_45',
      generate_funnel: generateFunnel,
    }
    try {
      const r = await api('/api/jobs', {
        method: 'POST',
        body: JSON.stringify(body),
      })
      const text = await r.text()
      if (!r.ok) {
        setMessage({ type: 'err', text: `HTTP ${r.status}: ${text.slice(0, 400)}` })
        return
      }
      setMessage({ type: 'ok', text: text.slice(0, 600) })
      await loadRecent()
    } catch (err) {
      setMessage({
        type: 'err',
        text: err instanceof Error ? err.message : 'enqueue failed',
      })
    } finally {
      setSubmitting(false)
    }
  }

  if (!authed) return null

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 p-6 md:p-10">
      <div className="max-w-5xl mx-auto">
        <McPageHeader
          title="Affiliate Pipeline"
          subtitle="POST /api/jobs with avatar_id, affiliate_program_id, and optional generate_funnel."
        />

        {message ? (
          <div
            className={`mb-6 rounded-lg border px-4 py-3 text-sm ${
              message.type === 'ok'
                ? 'border-emerald-800/60 bg-emerald-950/30 text-emerald-100'
                : 'border-red-800/60 bg-red-950/40 text-red-200'
            }`}
          >
            <pre className="whitespace-pre-wrap font-mono text-xs">{message.text}</pre>
          </div>
        ) : null}

        <form
          onSubmit={onSubmit}
          className="rounded-xl border border-slate-800 bg-slate-900/50 p-6 space-y-4 mb-10"
        >
          <div>
            <label className="block text-xs text-slate-500 mb-1">Product URL</label>
            <input
              className="w-full rounded-lg bg-slate-950 border border-slate-700 px-3 py-2 text-sm"
              value={productUrl}
              onChange={(e) => setProductUrl(e.target.value)}
              required
            />
          </div>
          <div className="grid md:grid-cols-2 gap-4">
            <div>
              <label className="block text-xs text-slate-500 mb-1">avatar_id</label>
              <input
                className="w-full rounded-lg bg-slate-950 border border-slate-700 px-3 py-2 text-sm font-mono"
                value={avatarId}
                onChange={(e) => setAvatarId(e.target.value)}
              />
            </div>
            <div>
              <label className="block text-xs text-slate-500 mb-1">affiliate_program_id</label>
              <input
                className="w-full rounded-lg bg-slate-950 border border-slate-700 px-3 py-2 text-sm font-mono"
                value={affiliateProgramId}
                onChange={(e) => setAffiliateProgramId(e.target.value)}
              />
            </div>
          </div>
          <div className="grid md:grid-cols-2 gap-4">
            <div>
              <label className="block text-xs text-slate-500 mb-1">affiliate_fields.product_id</label>
              <input
                className="w-full rounded-lg bg-slate-950 border border-slate-700 px-3 py-2 text-sm"
                value={productIdField}
                onChange={(e) => setProductIdField(e.target.value)}
              />
            </div>
            <div>
              <label className="block text-xs text-slate-500 mb-1">affiliate_fields.affiliate_tag</label>
              <input
                className="w-full rounded-lg bg-slate-950 border border-slate-700 px-3 py-2 text-sm"
                value={affiliateTag}
                onChange={(e) => setAffiliateTag(e.target.value)}
              />
            </div>
          </div>
          <label className="flex items-center gap-2 text-sm text-slate-300 cursor-pointer">
            <input
              type="checkbox"
              checked={generateFunnel}
              onChange={(e) => setGenerateFunnel(e.target.checked)}
              className="rounded border-slate-600"
            />
            generate_funnel (worker builds funnel after video when configured)
          </label>
          <button
            type="submit"
            disabled={submitting}
            className="px-4 py-2 rounded-lg bg-klipaura-600 hover:bg-klipaura-500 text-white text-sm font-medium disabled:opacity-50"
          >
            {submitting ? 'Enqueue…' : 'Enqueue job'}
          </button>
        </form>

        <div>
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-lg font-semibold text-slate-200">Recent jobs</h2>
            <button
              type="button"
              onClick={() => void loadRecent()}
              className="text-xs text-slate-500 hover:text-klipaura-400"
            >
              Refresh
            </button>
          </div>
          {loadingJobs ? (
            <p className="text-slate-500 text-sm">Loading…</p>
          ) : (
            <>
              {hitlDown ? (
                <div className="rounded-xl border border-amber-500/30 bg-amber-950/20 p-4 text-sm space-y-1 mb-4">
                  <p className="text-amber-300 font-medium">HITL proxy unavailable</p>
                  <p className="text-slate-400">
                    HITL service (port 8080) is not running. Showing local job data from Mission Control instead.
                    This is normal for local development.
                  </p>
                  <p className="text-slate-500 text-xs">
                    To use the dispatch service UI path, start:{' '}
                    <code className="text-xs bg-slate-800 px-1.5 py-0.5 rounded">
                      python -m uvicorn hitl_server:app --app-dir klip-dispatch --port 8080
                    </code>
                  </p>
                </div>
              ) : null}
            <div className="overflow-x-auto rounded-xl border border-slate-800 bg-slate-900/50">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-slate-800 text-left text-slate-500">
                    <th className="p-3 w-36">Preview</th>
                    <th className="p-3">Job</th>
                    <th className="p-3">Avatar</th>
                    <th className="p-3">Funnel</th>
                  </tr>
                </thead>
                <tbody>
                  {recent.length === 0 ? (
                    <tr>
                      <td colSpan={4} className="p-6 text-slate-500 text-center">
                        No jobs yet. Enqueue one above or run the worker stack.
                      </td>
                    </tr>
                  ) : (
                    recent.map((j) => (
                      <tr key={j.job_id} className="border-b border-slate-800/80 hover:bg-slate-900/80">
                        <td className="p-2 pl-3 align-top">
                          {j.video_url && j.video_url.startsWith('http') ? (
                            <video
                              src={j.video_url}
                              className="w-28 h-16 rounded-md object-cover bg-black border border-slate-700"
                              muted
                              playsInline
                              preload="metadata"
                            />
                          ) : (
                            <span className="text-xs text-slate-600">—</span>
                          )}
                        </td>
                        <td className="p-3 align-top font-mono text-xs">
                          <div className="text-klipaura-300">{j.job_id}</div>
                          <div className="text-slate-500 mt-1">{j.status || '—'}</div>
                          {j.product_url ? (
                            <a
                              href={j.product_url}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="inline-flex items-center gap-1 text-slate-400 hover:text-klipaura-400 mt-1"
                            >
                              Product <ExternalLink className="w-3 h-3" />
                            </a>
                          ) : null}
                        </td>
                        <td className="p-3 align-top text-slate-300">{j.avatar_id || '—'}</td>
                        <td className="p-3 align-top">
                          {j.funnel_url && j.funnel_url.startsWith('http') ? (
                            <a
                              href={j.funnel_url}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="text-emerald-400 hover:underline break-all"
                            >
                              Open funnel
                            </a>
                          ) : (
                            <span className="text-slate-600">—</span>
                          )}
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
