'use client'

import { useState, type FormEvent, useEffect } from 'react'
import { Clapperboard, Radar, ListOrdered, Loader2 } from 'lucide-react'

type PipelineMode = 'avatar' | 'scanner' | 'selector'

type ApiFetch = (path: string, init?: RequestInit) => Promise<Response>

export function RunPipelinePanel({
  apiFetch,
  onSuccess,
  onError,
}: {
  apiFetch: ApiFetch
  onSuccess: (msg: string) => void
  onError: (msg: string) => void
}) {
  const [mode, setMode] = useState<PipelineMode>('avatar')
  const [submitting, setSubmitting] = useState(false)

  const [productUrl, setProductUrl] = useState('')
  const [avatarList, setAvatarList] = useState<{ avatar_id: string }[]>([])
  const [avatarId, setAvatarId] = useState('theanikaglow')
  const [jobType, setJobType] = useState('ugc_video')
  const [templates, setTemplates] = useState<{ id: string; name: string; description?: string }[]>([])
  const [templateId, setTemplateId] = useState('split_screen_review')
  const [studioPersonas, setStudioPersonas] = useState<
    { persona_id: string; avatar_id: string; name: string; image_url?: string }[]
  >([])
  const [selectedPersonaId, setSelectedPersonaId] = useState('')
  const [splitTopPct, setSplitTopPct] = useState('58')
  const [generateFunnel, setGenerateFunnel] = useState(true)
  const [affiliateDisclosure, setAffiliateDisclosure] = useState('')

  const [scanAmazon, setScanAmazon] = useState(true)
  const [scanClickbank, setScanClickbank] = useState(true)
  const [scanTemu, setScanTemu] = useState(true)
  const [scanLiveFeeds, setScanLiveFeeds] = useState(false)
  const [scanQueueLimit, setScanQueueLimit] = useState(5)

  const [selLimit, setSelLimit] = useState(5)
  const [selAvatarId, setSelAvatarId] = useState('theanikaglow')

  useEffect(() => {
    let alive = true
    ;(async () => {
      try {
        const [ar, tr, pr] = await Promise.all([
          apiFetch('/api/v1/avatars'),
          apiFetch('/api/v1/templates'),
          apiFetch('/api/v1/avatar-studio/personas'),
        ])
        if (alive && ar.ok) {
          const a = (await ar.json()) as { avatar_id: string }[]
          setAvatarList(Array.isArray(a) ? a : [])
        }
        if (alive && tr.ok) {
          const t = (await tr.json()) as { id: string; name: string; description?: string }[]
          setTemplates(Array.isArray(t) ? t : [])
        }
        if (alive && pr.ok) {
          const pack = (await pr.json()) as { personas?: { persona_id: string; avatar_id: string; name: string }[] }
          setStudioPersonas(Array.isArray(pack.personas) ? pack.personas : [])
        }
      } catch {
        /* ignore */
      }
    })()
    return () => {
      alive = false
    }
  }, [apiFetch])

  useEffect(() => {
    if (avatarList.length === 0) return
    if (!avatarList.some((x) => x.avatar_id === avatarId)) {
      setAvatarId(avatarList[0].avatar_id)
    }
    if (!avatarList.some((x) => x.avatar_id === selAvatarId)) {
      setSelAvatarId(avatarList[0].avatar_id)
    }
  }, [avatarList, avatarId, selAvatarId])

  useEffect(() => {
    if (templates.length && !templates.some((t) => t.id === templateId)) {
      setTemplateId(templates[0].id)
    }
  }, [templates, templateId])

  useEffect(() => {
    if (!selectedPersonaId) return
    const p = studioPersonas.find((x) => x.persona_id === selectedPersonaId)
    if (p?.avatar_id) setAvatarId(p.avatar_id)
  }, [selectedPersonaId, studioPersonas])

  const parseError = async (r: Response) => {
    try {
      const j = (await r.json()) as { detail?: string | unknown }
      if (typeof j.detail === 'string') return j.detail
      if (Array.isArray(j.detail)) return JSON.stringify(j.detail)
    } catch {
      /* ignore */
    }
    return r.statusText || `HTTP ${r.status}`
  }

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault()
    setSubmitting(true)
    try {
      if (mode === 'avatar') {
        const pu = productUrl.trim()
        if (!pu) {
          onError('Product URL is required for video pipeline jobs.')
          return
        }
        const ratio = Math.max(0.25, Math.min(0.75, Number.parseFloat(splitTopPct) / 100))
        const r = await apiFetch('/api/v1/jobs', {
          method: 'POST',
          body: JSON.stringify({
            module: 'klip-avatar',
            job_type: jobType.trim() || 'ugc_video',
            payload: {
              product_url: pu,
              avatar_id: avatarId,
              template_id: templateId,
              layout_mode: 'affiliate_split_55_45',
              affiliate_split_top_ratio: Number.isFinite(ratio) ? ratio : 0.58,
              generate_funnel: generateFunnel,
              ...(affiliateDisclosure.trim()
                ? { affiliate_disclosure: affiliateDisclosure.trim() }
                : {}),
              ...(selectedPersonaId.trim()
                ? { persona_id: selectedPersonaId.trim() }
                : {}),
            },
          }),
        })
        if (!r.ok) {
          onError(await parseError(r))
          return
        }
        const job = (await r.json()) as { id?: string; warning?: string | null }
        const extra = job.warning ? ` ${job.warning}` : ''
        onSuccess(
          `${generateFunnel ? 'Video + funnel job' : 'Video job'} queued${job.id ? ` (${job.id.slice(0, 8)}…)` : ''}.${extra}`,
        )
        setProductUrl('')
      } else if (mode === 'scanner') {
        const r = await apiFetch('/api/v1/actions/scanner-run', {
          method: 'POST',
          body: JSON.stringify({
            include_amazon: scanAmazon,
            include_clickbank: scanClickbank,
            include_temu: scanTemu,
            include_live_feeds: scanLiveFeeds,
            queue_limit: scanQueueLimit,
          }),
        })
        if (!r.ok) {
          onError(await parseError(r))
          return
        }
        const out = (await r.json()) as { queued?: number; ranked_count?: number }
        onSuccess(
          `Scanner finished — queued ${out.queued ?? 0} job(s), ranked ${out.ranked_count ?? 0}.`,
        )
      } else {
        const r = await apiFetch('/api/v1/actions/selector-run', {
          method: 'POST',
          body: JSON.stringify({
            limit: selLimit,
            avatar_id: selAvatarId,
          }),
        })
        if (!r.ok) {
          onError(await parseError(r))
          return
        }
        const out = (await r.json()) as { ok?: boolean; exit_code?: number }
        if (out.ok) onSuccess('Selector cycle completed — jobs pushed to pending queue.')
        else onError(`Selector exited with code ${out.exit_code ?? 'unknown'}`)
      }
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err))
    } finally {
      setSubmitting(false)
    }
  }

  const inputClass =
    'w-full px-3 py-2 rounded-lg bg-slate-950 border border-slate-600 text-slate-100 text-sm placeholder:text-slate-500 focus:outline-none focus:ring-2 focus:ring-klipaura-500/60 focus:border-klipaura-500'
  const labelClass = 'block text-xs font-medium text-klipaura-200/90 mb-1.5'

  return (
    <form onSubmit={onSubmit} className="space-y-6 max-w-2xl">
      <div>
        <h2 className="text-lg font-semibold text-slate-100 border-b border-slate-700 pb-2 mb-4">
          Run pipeline
        </h2>
        <p className="text-sm text-slate-400 mb-4">
          Video jobs feed the klip-avatar worker. Scanner and selector enqueue work onto the same Redis
          pending queue (monorepo). Trading uses a separate repository.
        </p>
      </div>

      <div>
        <span className={labelClass}>Pipeline</span>
        <select
          value={mode}
          onChange={(e) => setMode(e.target.value as PipelineMode)}
          className={inputClass}
          aria-label="Pipeline type"
        >
          <option value="avatar">Video — klip-avatar (product URL → UGC pipeline)</option>
          <option value="scanner">Scanner — scan feeds/CSV → enqueue top opportunities</option>
          <option value="selector">Selector — CSV products → score → enqueue for avatar</option>
        </select>
      </div>

      {mode === 'avatar' && (
        <div className="space-y-4 rounded-xl border border-slate-700/80 bg-slate-900/50 p-4">
          <div className="flex items-center gap-2 text-klipaura-300 text-sm font-medium">
            <Clapperboard className="w-4 h-4" />
            Video / affiliate
          </div>
          <div>
            <label className={labelClass} htmlFor="mc-product-url">
              Product URL
            </label>
            <input
              id="mc-product-url"
              type="url"
              required
              value={productUrl}
              onChange={(e) => setProductUrl(e.target.value)}
              placeholder="https://…"
              className={inputClass}
            />
          </div>
          <div>
            <label className={labelClass} htmlFor="mc-studio-persona">
              Saved AI persona (Avatar Studio)
            </label>
            <select
              id="mc-studio-persona"
              value={selectedPersonaId}
              onChange={(e) => setSelectedPersonaId(e.target.value)}
              className={inputClass}
            >
              <option value="">— Optional: pick a Studio persona —</option>
              {studioPersonas.map((p) => (
                <option key={p.persona_id} value={p.persona_id}>
                  {p.name || p.avatar_id} ({p.avatar_id})
                </option>
              ))}
            </select>
            <p className="text-[11px] text-slate-500 mt-1">
              Sets avatar + voice from Redis. Create personas in Avatar Studio first.
            </p>
          </div>
          <div>
            <label className={labelClass} htmlFor="mc-split-top">
              Top band (product) height
            </label>
            <select
              id="mc-split-top"
              value={splitTopPct}
              onChange={(e) => setSplitTopPct(e.target.value)}
              className={inputClass}
            >
              <option value="55">55% product / 45% avatar</option>
              <option value="58">58% / 42% (default)</option>
              <option value="60">60% / 40%</option>
            </select>
          </div>
          <div>
            <label className={labelClass} htmlFor="mc-avatar-id">
              Avatar (disk profile)
            </label>
            <select
              id="mc-avatar-id"
              value={avatarId}
              onChange={(e) => setAvatarId(e.target.value)}
              className={inputClass}
            >
              {avatarList.length > 0 ? (
                avatarList.map((x) => (
                  <option key={x.avatar_id} value={x.avatar_id}>
                    {x.avatar_id}
                  </option>
                ))
              ) : (
                <option value="theanikaglow">theanikaglow</option>
              )}
            </select>
          </div>
          <div>
            <label className={labelClass} htmlFor="mc-template-id">
              Video template
            </label>
            <select
              id="mc-template-id"
              value={templateId}
              onChange={(e) => setTemplateId(e.target.value)}
              className={inputClass}
            >
              {templates.length > 0 ? (
                templates.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.name}
                  </option>
                ))
              ) : (
                <option value="split_screen_review">Split-Screen Review (default)</option>
              )}
            </select>
            {templates.find((t) => t.id === templateId)?.description && (
              <p className="text-[11px] text-slate-500 mt-1">
                {templates.find((t) => t.id === templateId)?.description}
              </p>
            )}
          </div>
          <div>
            <label className={labelClass} htmlFor="mc-job-type">
              Job type
            </label>
            <input
              id="mc-job-type"
              type="text"
              value={jobType}
              onChange={(e) => setJobType(e.target.value)}
              className={inputClass}
            />
          </div>
          <label className="flex items-start gap-2 text-sm text-slate-300 cursor-pointer">
            <input
              type="checkbox"
              checked={generateFunnel}
              onChange={(e) => setGenerateFunnel(e.target.checked)}
              className="rounded border-slate-600 bg-slate-950 text-klipaura-500 focus:ring-klipaura-500 mt-0.5"
            />
            <span>
              <span className="font-medium text-slate-200">Generate landing page (funnel)</span>
              <span className="block text-[11px] text-slate-500 mt-0.5">
                After the video, publishes HTML with embed + affiliate CTA to R2 (or local jobs folder).
              </span>
            </span>
          </label>
          <div>
            <label className={labelClass} htmlFor="mc-disclosure">
              Affiliate disclosure (optional)
            </label>
            <textarea
              id="mc-disclosure"
              rows={2}
              value={affiliateDisclosure}
              onChange={(e) => setAffiliateDisclosure(e.target.value)}
              placeholder="Leave empty for default FTC-style line on the landing page"
              className={inputClass + ' min-h-[3rem]'}
            />
          </div>
        </div>
      )}

      {mode === 'scanner' && (
        <div className="space-y-4 rounded-xl border border-slate-700/80 bg-slate-900/50 p-4">
          <div className="flex items-center gap-2 text-klipaura-300 text-sm font-medium">
            <Radar className="w-4 h-4" />
            Scanner sources
          </div>
          {[
            ['Amazon (CSV)', scanAmazon, setScanAmazon],
            ['ClickBank', scanClickbank, setScanClickbank],
            ['Temu', scanTemu, setScanTemu],
            ['Live feeds (JSON)', scanLiveFeeds, setScanLiveFeeds],
          ].map(([label, val, setVal]) => (
            <label key={label as string} className="flex items-center gap-2 text-sm text-slate-300 cursor-pointer">
              <input
                type="checkbox"
                checked={val as boolean}
                onChange={(e) => (setVal as (v: boolean) => void)(e.target.checked)}
                className="rounded border-slate-600 bg-slate-950 text-klipaura-500 focus:ring-klipaura-500"
              />
              {label as string}
            </label>
          ))}
          <div>
            <label className={labelClass} htmlFor="mc-scan-limit">
              Max jobs to enqueue
            </label>
            <input
              id="mc-scan-limit"
              type="number"
              min={1}
              max={100}
              value={scanQueueLimit}
              onChange={(e) => setScanQueueLimit(Number(e.target.value) || 1)}
              className={inputClass}
            />
          </div>
        </div>
      )}

      {mode === 'selector' && (
        <div className="space-y-4 rounded-xl border border-slate-700/80 bg-slate-900/50 p-4">
          <div className="flex items-center gap-2 text-klipaura-300 text-sm font-medium">
            <ListOrdered className="w-4 h-4" />
            Selector cycle
          </div>
          <div>
            <label className={labelClass} htmlFor="mc-sel-limit">
              Max products to queue
            </label>
            <input
              id="mc-sel-limit"
              type="number"
              min={1}
              max={50}
              value={selLimit}
              onChange={(e) => setSelLimit(Number(e.target.value) || 1)}
              className={inputClass}
            />
          </div>
          <div>
            <label className={labelClass} htmlFor="mc-sel-avatar">
              Target avatar
            </label>
            <select
              id="mc-sel-avatar"
              value={selAvatarId}
              onChange={(e) => setSelAvatarId(e.target.value)}
              className={inputClass}
            >
              {avatarList.length > 0 ? (
                avatarList.map((x) => (
                  <option key={x.avatar_id} value={x.avatar_id}>
                    {x.avatar_id}
                  </option>
                ))
              ) : (
                <option value="theanikaglow">theanikaglow</option>
              )}
            </select>
          </div>
        </div>
      )}

      <button
        type="submit"
        disabled={submitting}
        className="w-full sm:w-auto inline-flex items-center justify-center gap-2 px-6 py-3 rounded-lg font-semibold text-white bg-gradient-to-r from-klipaura-600 to-klipaura-400 hover:from-klipaura-500 hover:to-klipaura-300 disabled:opacity-50 disabled:cursor-not-allowed shadow-lg shadow-klipaura-900/40"
      >
        {submitting ? <Loader2 className="w-5 h-5 animate-spin" /> : null}
        {mode === 'avatar' && 'Enqueue video job'}
        {mode === 'scanner' && 'Run scanner'}
        {mode === 'selector' && 'Run selector cycle'}
      </button>
    </form>
  )
}
