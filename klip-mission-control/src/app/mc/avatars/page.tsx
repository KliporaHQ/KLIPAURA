'use client'

import { useCallback, useEffect, useState } from 'react'
import { McPageHeader } from '@/components/McPageHeader'
import { createApiFetch, getApiBase, getMcToken, mcSkipLogin } from '@/lib/mc-client'

/** Same as pipeline: next.config rewrites `/api/avatars` → HITL :8080; abort fast when down. */
const HITL_PROXY_TIMEOUT_MS = 1800

function missionControlDirectBase(): string {
  return (process.env.NEXT_PUBLIC_MC_DIRECT_URL || 'http://127.0.0.1:8000').replace(/\/$/, '')
}

type AvatarRow = {
  avatar_id: string
  name?: string | null
  registry_display_name?: string | null
  registry_voice_configured?: boolean
  niche?: string | null
  has_portrait?: boolean
  portrait_url?: string | null
  registry_only?: boolean
}

export default function AvatarManagementPage() {
  const base = getApiBase()
  const api = createApiFetch(base)
  const [authed, setAuthed] = useState(() => mcSkipLogin())
  const [loading, setLoading] = useState(true)
  const [hitlDown, setHitlDown] = useState(false)
  const [defaultId, setDefaultId] = useState<string>('')
  const [rows, setRows] = useState<AvatarRow[]>([])

  const load = useCallback(async () => {
    setLoading(true)
    setHitlDown(false)
    const ac = new AbortController()
    const timer = window.setTimeout(() => ac.abort(), HITL_PROXY_TIMEOUT_MS)

    const fetchAvatarsFromMissionControlDirect = async (): Promise<boolean> => {
      const headers = new Headers({ 'Content-Type': 'application/json' })
      const tok = getMcToken()
      if (tok) headers.set('Authorization', `Bearer ${tok}`)
      const r = await fetch(`${missionControlDirectBase()}/api/avatars`, { headers })
      if (!r.ok) return false
      const j = (await r.json()) as { default_avatar_id?: string; avatars?: AvatarRow[] }
      setDefaultId(j.default_avatar_id || '')
      setRows(Array.isArray(j.avatars) ? j.avatars : [])
      return true
    }

    try {
      const r = await api('/api/avatars', { signal: ac.signal })
      window.clearTimeout(timer)
      if (r.ok) {
        const j = (await r.json()) as { default_avatar_id?: string; avatars?: AvatarRow[] }
        setDefaultId(j.default_avatar_id || '')
        setRows(Array.isArray(j.avatars) ? j.avatars : [])
        return
      }
      setHitlDown(true)
      setLoading(false)
      try {
        await fetchAvatarsFromMissionControlDirect()
      } catch {
        setRows([])
        setDefaultId('')
      }
      return
    } catch {
      window.clearTimeout(timer)
      setHitlDown(true)
      setLoading(false)
      try {
        await fetchAvatarsFromMissionControlDirect()
      } catch {
        setRows([])
        setDefaultId('')
      }
    } finally {
      window.clearTimeout(timer)
      setLoading(false)
    }
  }, [api])

  useEffect(() => {
    if (!mcSkipLogin() && typeof window !== 'undefined' && !getMcToken()) {
      window.location.href = '/'
      return
    }
    setAuthed(true)
    void load()
  }, [load])

  const portraitSrc = (a: AvatarRow) => {
    if (!a.portrait_url) return null
    if (a.portrait_url.startsWith('http')) return a.portrait_url
    return `${base}${a.portrait_url}`
  }

  if (!authed) return null

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 p-6 md:p-10">
      <div className="max-w-5xl mx-auto">
        <McPageHeader
          title="Avatar Management"
          subtitle="GET /api/avatars — disk avatars merged with config/avatars.json (use avatar_id when enqueueing jobs)."
        />

        {hitlDown ? (
          <div className="rounded-xl border border-amber-500/30 bg-amber-950/20 p-4 text-sm space-y-1 mb-6">
            <p className="text-amber-300 font-medium">HITL proxy unavailable</p>
            <p className="text-slate-400">
              HITL service (port 8080) is not running. Showing avatar data from Mission Control instead. This is
              normal for local development.
            </p>
            <p className="text-slate-500 text-xs">
              Optional:{' '}
              <code className="text-xs bg-slate-800 px-1.5 py-0.5 rounded">
                python -m uvicorn hitl_server:app --app-dir klip-dispatch --port 8080
              </code>
            </p>
            <button type="button" className="text-cyan-400 underline text-xs mt-1" onClick={() => void load()}>
              Retry
            </button>
          </div>
        ) : null}

        {loading ? (
          <p className="text-slate-500 text-sm">Loading avatars…</p>
        ) : (
          <>
            <p className="text-slate-400 text-sm mb-4">
              Default avatar: <code className="text-klipaura-300">{defaultId || '—'}</code>
            </p>
            <div className="overflow-x-auto rounded-xl border border-slate-800 bg-slate-900/50">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-slate-800 text-left text-slate-500">
                    <th className="p-3 w-20"> </th>
                    <th className="p-3">avatar_id</th>
                    <th className="p-3">Name</th>
                    <th className="p-3">Voice</th>
                    <th className="p-3">Niche</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((a) => (
                    <tr key={a.avatar_id} className="border-b border-slate-800/80 hover:bg-slate-900/80">
                      <td className="p-2 pl-3">
                        {portraitSrc(a) ? (
                          // eslint-disable-next-line @next/next/no-img-element
                          <img
                            src={portraitSrc(a)!}
                            alt=""
                            className="w-12 h-12 rounded-lg object-cover border border-slate-700"
                            width={48}
                            height={48}
                          />
                        ) : (
                          <div className="w-12 h-12 rounded-lg bg-slate-800 border border-slate-700 flex items-center justify-center text-[10px] text-slate-500">
                            n/a
                          </div>
                        )}
                      </td>
                      <td className="p-3 font-mono text-klipaura-300">
                        {a.avatar_id}
                        {a.registry_only ? (
                          <span className="ml-2 text-[10px] uppercase text-amber-400/90">registry</span>
                        ) : null}
                      </td>
                      <td className="p-3 text-slate-300">
                        {a.registry_display_name || a.name || '—'}
                      </td>
                      <td className="p-3">{a.registry_voice_configured ? 'yes' : '—'}</td>
                      <td className="p-3 text-slate-400">{a.niche || '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
