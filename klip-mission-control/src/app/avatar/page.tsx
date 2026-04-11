'use client'

import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { useState, useEffect, useCallback, useMemo, type FormEvent } from 'react'
import {
  User,
  LayoutGrid,
  Sparkles,
  Clapperboard,
  Activity,
  ArrowLeft,
  LogOut,
  ExternalLink,
  Radio,
  Cpu,
  Film,
  Loader2,
  CheckCircle2,
  AlertCircle,
  Clock,
  ChevronRight,
  Mic,
  Image as ImageIcon,
} from 'lucide-react'
import {
  createApiFetch,
  getApiBase,
  getHitlPublicUrl,
  getMcToken,
  mcSkipLogin,
  setMcToken,
} from '../../lib/mc-client'

const AVATAR = 'klip-avatar'

interface Job {
  id: string
  module: string
  job_type: string
  status: string
  progress: number
  created_at: string
  hitl_requested: boolean
  warning?: string | null
  redis_enqueued?: boolean | null
}

interface KlipEvent {
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
  redis_connected: boolean
  uptime_seconds: number
}

interface QueueOverview {
  jobs_pending: number
  hitl_pending: number
  dlq: number
  global_paused: boolean
}

interface WorkerStatus {
  worker: string
  online: boolean
  state?: string | null
  job_id?: string | null
  last_seen?: string | null
  seconds_since_last_seen?: number | null
  queue_depth: number
  note?: string | null
}

interface AvatarSummary {
  avatar_id: string
  display_name: string
  niche?: string | null
  has_persona: boolean
  has_social_config: boolean
  has_portrait: boolean
  updated_at?: string | null
}

interface AvatarDetail extends AvatarSummary {
  persona: Record<string, unknown>
  social_config: Record<string, unknown>
}

interface VideoTemplate {
  id: string
  name: string
  description?: string
  preview_hint?: string
  scene_types?: string[]
  knobs?: Record<string, unknown>
}

interface PipelineHealth {
  redis_connected: boolean
  redis_url_redacted: string
  env: Record<string, boolean>
  avatars_total: number
  avatars_with_portrait: number
  worker: WorkerStatus
}

type Tab = 'overview' | 'create' | 'avatars' | 'pipeline' | 'activity'

/** When the pasted URL already contains ``/dp/ASIN``, use it without waiting on server-side fetch. */
function clientAmazonAsinHintFromUrl(url: string): string | null {
  const s = url.trim()
  const patterns = [
    /amazon\.[a-z.]+\/dp\/([A-Z0-9]{10})\b/i,
    /amazon\.[a-z.]+\/gp\/product\/([A-Z0-9]{10})\b/i,
    /amazon\.[a-z.]+\/gp\/aw\/d\/([A-Z0-9]{10})\b/i,
    /amazon\.[a-z.]+\/d\/([A-Z0-9]{10})\b/i,
  ]
  for (const p of patterns) {
    const m = s.match(p)
    if (m) {
      const asin = m[1].toUpperCase()
      return `Amazon listing ASIN ${asin}. Describe this exact product from the page or link; do not invent skincare, steamers, or unrelated categories.`
    }
  }
  return null
}

/** FastAPI: `detail` string or validation error array */
function formatFastApiDetail(body: unknown): string | null {
  if (!body || typeof body !== 'object') return null
  const d = (body as { detail?: unknown }).detail
  if (typeof d === 'string') return d
  if (Array.isArray(d)) {
    return d
      .map((x) => {
        if (x && typeof x === 'object' && 'msg' in x) return String((x as { msg: unknown }).msg)
        return typeof x === 'string' ? x : JSON.stringify(x)
      })
      .filter(Boolean)
      .join('; ')
  }
  return null
}

function StatusBadge({ status }: { status: string }) {
  const styles: Record<string, string> = {
    running: 'bg-emerald-500/12 text-emerald-300 border-emerald-500/30',
    completed: 'bg-klipaura-500/12 text-klipaura-200 border-klipaura-500/30',
    failed: 'bg-red-500/12 text-red-300 border-red-500/30',
    pending: 'bg-amber-500/12 text-amber-200 border-amber-500/35',
    awaiting_hitl: 'bg-klipaura-500/12 text-klipaura-200 border-klipaura-500/35',
    cancelled: 'bg-slate-500/15 text-slate-400 border-slate-600',
  }
  return (
    <span
      className={`inline-flex items-center px-2.5 py-0.5 rounded-md text-[11px] font-semibold uppercase tracking-wide border ${
        styles[status] || styles.pending
      }`}
    >
      {(status || 'unknown').replace(/_/g, ' ')}
    </span>
  )
}

function SeverityDot({ severity }: { severity: string }) {
  const s = (severity || 'info').toLowerCase()
  const map: Record<string, string> = {
    debug: 'bg-slate-500',
    info: 'bg-klipaura-400',
    success: 'bg-emerald-400',
    warning: 'bg-amber-400',
    error: 'bg-red-400',
    critical: 'bg-red-500',
  }
  return <span className={`inline-block w-1.5 h-1.5 rounded-full ${map[s] || map.info}`} />
}

export default function AvatarMissionControlPage() {
  const router = useRouter()
  const baseUrl = getApiBase()
  const apiFetch = useMemo(() => createApiFetch(baseUrl), [baseUrl])
  const hitlUrl = getHitlPublicUrl()

  /** Single sign-on: session comes from Mission Control (`/`); this page does not show its own login form. */
  const [loggedIn, setLoggedIn] = useState(
    () => mcSkipLogin() || (typeof window !== 'undefined' ? !!getMcToken() : false),
  )
  const [loading, setLoading] = useState(() =>
    mcSkipLogin() ? true : typeof window !== 'undefined' ? !!getMcToken() : true,
  )
  const [tab, setTab] = useState<Tab>('overview')

  const [jobs, setJobs] = useState<Job[]>([])
  const [events, setEvents] = useState<KlipEvent[]>([])
  const [metrics, setMetrics] = useState<Metrics | null>(null)
  const [queues, setQueues] = useState<QueueOverview | null>(null)
  const [workerStatus, setWorkerStatus] = useState<WorkerStatus | null>(null)
  const [avatars, setAvatars] = useState<AvatarSummary[]>([])
  const [avatarDetail, setAvatarDetail] = useState<AvatarDetail | null>(null)
  const [avatarDetailId, setAvatarDetailId] = useState<string>('')
  const [avatarBusyId, setAvatarBusyId] = useState<string | null>(null)

  const [studioPersonas, setStudioPersonas] = useState<
    { persona_id: string; avatar_id: string; name: string; image_url?: string }[]
  >([])
  const [selectedStudioPersonaId, setSelectedStudioPersonaId] = useState('')
  const [splitTopPct, setSplitTopPct] = useState('58')
  const [generateFunnel, setGenerateFunnel] = useState(false)
  const [affiliateDisclosure, setAffiliateDisclosure] = useState('')

  const [productUrl, setProductUrl] = useState('')
  /** Optional: exact product name — required for reliable preview when using short amzn.to links if the server is blocked. */
  const [productTitleHint, setProductTitleHint] = useState('')
  const [avatarId, setAvatarId] = useState<string>('theanikaglow')
  const [jobType, setJobType] = useState('ugc_video')
  const [submitting, setSubmitting] = useState(false)
  const [creatingAvatar, setCreatingAvatar] = useState(false)
  const [newAvatarId, setNewAvatarId] = useState('')
  const [newAvatarName, setNewAvatarName] = useState('')
  const [newAvatarNiche, setNewAvatarNiche] = useState('')
  const [newAvatarVoice, setNewAvatarVoice] = useState('')
  const [newAvatarCta, setNewAvatarCta] = useState('')
  const [newAvatarTone, setNewAvatarTone] = useState('friendly')
  const [newAvatarStyle, setNewAvatarStyle] = useState('ugc')
  const [newAvatarLanguage, setNewAvatarLanguage] = useState('en')
  const [newGetlateKey, setNewGetlateKey] = useState('')
  const [newZerioKey, setNewZerioKey] = useState('')
  const [newTiktokProfile, setNewTiktokProfile] = useState('')
  const [newInstagramProfile, setNewInstagramProfile] = useState('')
  const [newYoutubeProfile, setNewYoutubeProfile] = useState('')
  const [newAmazonTag, setNewAmazonTag] = useState('')
  const [newTiktokShopId, setNewTiktokShopId] = useState('')
  const [imageSource, setImageSource] = useState<'none' | 'upload' | 'prompt'>('none')
  const [avatarImageFile, setAvatarImageFile] = useState<File | null>(null)
  const [avatarImagePrompt, setAvatarImagePrompt] = useState('')
  const [toast, setToast] = useState<{ type: 'ok' | 'err' | 'warn'; text: string } | null>(null)
  const [videoTemplates, setVideoTemplates] = useState<VideoTemplate[]>([])
  const [templateId, setTemplateId] = useState('split_screen_review')
  const [pipelineHealth, setPipelineHealth] = useState<PipelineHealth | null>(null)
  const [scriptPreview, setScriptPreview] = useState('')
  /** `undefined` = no run yet; `null` = ran but no title scraped; string = Groq grounded on this. */
  const [previewProductHint, setPreviewProductHint] = useState<string | null | undefined>(undefined)
  const [previewBusy, setPreviewBusy] = useState<'script' | 'tts' | null>(null)
  const [ttsAudioUrl, setTtsAudioUrl] = useState<string | null>(null)
  const [thumbUrls, setThumbUrls] = useState<Record<string, string>>({})
  const [editDisplay, setEditDisplay] = useState('')
  const [editNiche, setEditNiche] = useState('')
  const [editCta, setEditCta] = useState('')
  const [editVoiceId, setEditVoiceId] = useState('')
  const [savingAvatar, setSavingAvatar] = useState(false)
  const [dragOver, setDragOver] = useState(false)

  const avatarJobs = useMemo(() => jobs.filter((j) => j.module === AVATAR), [jobs])
  const avatarEvents = useMemo(() => events.filter((e) => (e.module || '').includes('avatar')), [events])
  const avatarIdsKey = useMemo(() => avatars.map((a) => a.avatar_id).sort().join(','), [avatars])

  const showToast = useCallback((type: 'ok' | 'err' | 'warn', text: string) => {
    setToast({ type, text })
    window.setTimeout(() => setToast(null), 8000)
  }, [])

  useEffect(() => {
    if (videoTemplates.length && !videoTemplates.some((t) => t.id === templateId)) {
      setTemplateId(videoTemplates[0].id)
    }
  }, [videoTemplates, templateId])

  useEffect(() => {
    setPreviewProductHint(undefined)
  }, [productUrl])

  /** Deep-link from Avatar Studio &quot;Use in Video&quot; (``/avatar?avatar_id=…&persona_id=…``). */
  useEffect(() => {
    if (typeof window === 'undefined') return
    const q = new URLSearchParams(window.location.search).get('avatar_id')
    if (q?.trim()) setAvatarId(q.trim())
  }, [])

  /** Optional ``?persona_id=`` from Avatar Studio. */
  useEffect(() => {
    if (typeof window === 'undefined') return
    const pid = new URLSearchParams(window.location.search).get('persona_id')
    if (pid?.trim()) setSelectedStudioPersonaId(pid.trim())
  }, [])

  /** ``?generate_funnel=1`` — Video + landing page (Mission Control CTA). */
  useEffect(() => {
    if (typeof window === 'undefined') return
    const v = new URLSearchParams(window.location.search).get('generate_funnel')
    if (v === '1' || v === 'true') setGenerateFunnel(true)
  }, [])

  useEffect(() => {
    if (!selectedStudioPersonaId) return
    const p = studioPersonas.find((x) => x.persona_id === selectedStudioPersonaId)
    if (p?.avatar_id) setAvatarId(p.avatar_id)
  }, [selectedStudioPersonaId, studioPersonas])

  useEffect(() => {
    if (!loggedIn || avatars.length === 0) return
    let alive = true
    const loaded: Record<string, string> = {}
    ;(async () => {
      for (const a of avatars) {
        try {
          const r = await apiFetch(`/api/v1/avatars/${encodeURIComponent(a.avatar_id)}/portrait`)
          if (r.ok && alive) {
            loaded[a.avatar_id] = URL.createObjectURL(await r.blob())
          }
        } catch {
          /* ignore */
        }
      }
      if (alive) {
        setThumbUrls((prev) => {
          Object.values(prev).forEach((u) => URL.revokeObjectURL(u))
          return loaded
        })
      }
    })()
    return () => {
      alive = false
    }
  }, [loggedIn, avatarIdsKey, apiFetch])

  useEffect(() => {
    if (!avatarDetail) return
    const p = avatarDetail.persona || {}
    const s = avatarDetail.social_config || {}
    setEditDisplay(String(p.display_name || p.name || avatarDetail.display_name || ''))
    setEditNiche(String(p.niche || avatarDetail.niche || ''))
    setEditCta(String(p.cta_line || ''))
    setEditVoiceId(String(s.elevenlabs_voice_id || ''))
  }, [avatarDetail])

  useEffect(() => {
    let cancelled = false
    const boot = async () => {
      if (mcSkipLogin()) {
        if (!cancelled) setLoggedIn(true)
        return
      }
      if (typeof window !== 'undefined' && getMcToken()) {
        if (!cancelled) setLoggedIn(true)
        return
      }
      try {
        const r = await fetch(`${baseUrl}/api/v1/modules`)
        if (r.ok) {
          if (!cancelled) setLoggedIn(true)
          return
        }
      } catch {
        /* fall through */
      }
      if (!cancelled) router.replace('/')
    }
    void boot()
    return () => {
      cancelled = true
    }
  }, [baseUrl, router])

  const loadData = useCallback(async () => {
    try {
      const [jobsRes, eventsRes, metricsRes, qRes, avatarsRes, workerRes, healthRes, tmplRes, studioRes] =
        await Promise.all([
          apiFetch('/api/v1/jobs?limit=100').catch(() => ({ ok: false, json: async () => [] })),
          apiFetch('/api/v1/events?limit=80').catch(() => ({ ok: false, json: async () => [] })),
          apiFetch('/api/v1/metrics').catch(() => ({ ok: false, json: async () => null })),
          apiFetch('/api/v1/queues/overview').catch(() => ({ ok: false, json: async () => null })),
          apiFetch('/api/v1/avatars').catch(() => ({ ok: false, json: async () => [] })),
          apiFetch('/api/v1/workers/avatar').catch(() => ({ ok: false, json: async () => null })),
          apiFetch('/api/v1/health/pipeline').catch(() => ({ ok: false, json: async () => null })),
          apiFetch('/api/v1/templates').catch(() => ({ ok: false, json: async () => [] })),
          apiFetch('/api/v1/avatar-studio/personas').catch(() => ({ ok: false, json: async () => ({}) })),
        ])
      if (jobsRes.ok) setJobs(await jobsRes.json())
      if (eventsRes.ok) setEvents(await eventsRes.json())
      if (metricsRes.ok) setMetrics(await metricsRes.json())
      if (qRes.ok) setQueues(await qRes.json())
      if (workerRes.ok) setWorkerStatus(await workerRes.json())
      if (healthRes.ok) setPipelineHealth((await healthRes.json()) as PipelineHealth)
      if (tmplRes.ok) {
        const t = (await tmplRes.json()) as VideoTemplate[]
        setVideoTemplates(Array.isArray(t) ? t : [])
      }
      if (studioRes.ok) {
        const pack = (await studioRes.json()) as { personas?: typeof studioPersonas }
        setStudioPersonas(Array.isArray(pack.personas) ? pack.personas : [])
      }
      if (avatarsRes.ok) {
        const data = (await avatarsRes.json()) as AvatarSummary[]
        setAvatars(data)
        if (!avatarDetailId && data.length > 0) setAvatarDetailId(data[0].avatar_id)
        if (!data.some((a) => a.avatar_id === avatarId)) {
          setAvatarId(data[0]?.avatar_id || 'theanikaglow')
        }
      }
    } catch {
      /* ignore */
    } finally {
      setLoading(false)
    }
  }, [apiFetch, avatarDetailId, avatarId])

  useEffect(() => {
    if (!loggedIn) {
      setLoading(false)
      return
    }
    loadData()
    const id = window.setInterval(loadData, 8000)
    return () => window.clearInterval(id)
  }, [loggedIn, loadData])

  /** Never block the shell forever if fetches hang (e.g. IDE browser ↔ localhost quirks). */
  useEffect(() => {
    if (!loggedIn) return
    const t = window.setTimeout(() => setLoading(false), 15000)
    return () => window.clearTimeout(t)
  }, [loggedIn])

  useEffect(() => {
    if (!loggedIn) return
    const es = new EventSource(`${baseUrl}/api/events/stream`)
    es.onmessage = (e) => {
      try {
        const ev = JSON.parse(e.data) as KlipEvent
        if ((ev.module || '').includes('avatar')) {
          setEvents((prev) =>
            [{ ...ev, id: ev.id || crypto.randomUUID?.() || String(Date.now()) }, ...prev].slice(0, 80),
          )
        }
      } catch {
        /* ignore */
      }
    }
    es.onerror = () => es.close()
    return () => es.close()
  }, [loggedIn, baseUrl])

  const handleEnqueue = async (e: FormEvent) => {
    e.preventDefault()
    const pu = productUrl.trim()
    if (!pu) {
      showToast('err', 'Product URL is required.')
      return
    }
    setSubmitting(true)
    try {
      const ratio = Math.max(0.25, Math.min(0.75, Number.parseFloat(splitTopPct) / 100))
      const r = await apiFetch('/api/v1/jobs', {
        method: 'POST',
        body: JSON.stringify({
          module: AVATAR,
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
            ...(selectedStudioPersonaId.trim()
              ? { persona_id: selectedStudioPersonaId.trim() }
              : {}),
          },
        }),
      })
      if (!r.ok) {
        const err = await r.json().catch(() => ({}))
        showToast('err', (err as { detail?: string }).detail || r.statusText)
        return
      }
      const job = (await r.json()) as Job & { id?: string }
      if (job.warning) {
        showToast('warn', job.warning)
      } else {
        showToast(
          'ok',
          `${generateFunnel ? 'Video + funnel job' : 'Job'} queued${job.id ? ` · ${job.id.slice(0, 8)}…` : ''}. Ensure the klip-avatar worker is running.${
            generateFunnel ? ' Check Affiliate Pipeline for the landing page link when complete.' : ''
          }`,
        )
      }
      setProductUrl('')
      loadData()
    } catch (err) {
      showToast('err', err instanceof Error ? err.message : 'Request failed')
    } finally {
      setSubmitting(false)
    }
  }

  const loadAvatarDetail = useCallback(
    async (id: string) => {
      const aid = (id || '').trim()
      if (!aid) {
        setAvatarDetail(null)
        return
      }
      try {
        const r = await apiFetch(`/api/v1/avatars/${encodeURIComponent(aid)}`)
        if (!r.ok) return
        const data = (await r.json()) as AvatarDetail
        setAvatarDetail(data)
      } catch {
        /* ignore */
      }
    },
    [apiFetch],
  )

  const handleCreateAvatar = async (e: FormEvent) => {
    e.preventDefault()
    const aid = newAvatarId.trim().toLowerCase()
    if (!aid) {
      showToast('err', 'Avatar ID is required.')
      return
    }
    setCreatingAvatar(true)
    try {
      const r = await apiFetch('/api/v1/avatars', {
        method: 'POST',
        body: JSON.stringify({
          avatar_id: aid,
          display_name: newAvatarName.trim() || aid,
          niche: newAvatarNiche.trim() || undefined,
          voice_id: newAvatarVoice.trim() || undefined,
          cta_line: newAvatarCta.trim() || undefined,
          content_tone: newAvatarTone || undefined,
          content_style: newAvatarStyle || undefined,
          language: newAvatarLanguage || undefined,
          getlate_api_key: newGetlateKey.trim() || undefined,
          zerio_api_key: newZerioKey.trim() || undefined,
          affiliate_amazon_tag: newAmazonTag.trim() || undefined,
          affiliate_tiktok_shop_id: newTiktokShopId.trim() || undefined,
          platform_profiles: {
            ...(newTiktokProfile.trim() ? { tiktok: newTiktokProfile.trim() } : {}),
            ...(newInstagramProfile.trim() ? { instagram: newInstagramProfile.trim() } : {}),
            ...(newYoutubeProfile.trim() ? { youtube: newYoutubeProfile.trim() } : {}),
          },
        }),
      })
      if (!r.ok) {
        const err = await r.json().catch(() => ({}))
        const detail = formatFastApiDetail(err)
        showToast(
          'err',
          detail ||
            (r.status === 409
              ? 'This avatar ID already exists. Use another ID, or open Avatars and edit the existing profile.'
              : `Create avatar failed (${r.status})`),
        )
        return
      }
      showToast('ok', `Avatar created: ${aid}`)
      if (imageSource === 'upload' && avatarImageFile) {
        const fd = new FormData()
        fd.append('image', avatarImageFile)
        const up = await fetch(`${baseUrl}/api/v1/avatars/${encodeURIComponent(aid)}/image-upload`, {
          method: 'POST',
          headers: getMcToken() ? { Authorization: `Bearer ${getMcToken()}` } : {},
          body: fd,
        })
        if (!up.ok) {
          const err = await up.json().catch(() => ({}))
          showToast('err', formatFastApiDetail(err) || `Avatar created but image upload failed (${up.status})`)
        }
      } else if (imageSource === 'prompt' && avatarImagePrompt.trim()) {
        const gp = await apiFetch(`/api/v1/avatars/${encodeURIComponent(aid)}/image-generate`, {
          method: 'POST',
          body: JSON.stringify({ prompt: avatarImagePrompt.trim(), provider: 'wavespeed' }),
        })
        if (!gp.ok) {
          const err = await gp.json().catch(() => ({}))
          showToast('err', formatFastApiDetail(err) || `Avatar created but image generation failed (${gp.status})`)
        }
      }
      setNewAvatarId('')
      setNewAvatarName('')
      setNewAvatarNiche('')
      setNewAvatarVoice('')
      setNewAvatarCta('')
      setNewAvatarTone('friendly')
      setNewAvatarStyle('ugc')
      setNewAvatarLanguage('en')
      setNewGetlateKey('')
      setNewZerioKey('')
      setNewTiktokProfile('')
      setNewInstagramProfile('')
      setNewYoutubeProfile('')
      setNewAmazonTag('')
      setNewTiktokShopId('')
      setImageSource('none')
      setAvatarImageFile(null)
      setAvatarImagePrompt('')
      await loadData()
      setAvatarDetailId(aid)
      await loadAvatarDetail(aid)
      setAvatarId(aid)
      setTab('avatars')
    } catch (err) {
      showToast('err', err instanceof Error ? err.message : 'Create avatar failed')
    } finally {
      setCreatingAvatar(false)
    }
  }

  const handleDeleteAvatar = async (aid: string) => {
    if (!window.confirm(`Delete avatar "${aid}"? This removes local persona files.`)) return
    setAvatarBusyId(aid)
    try {
      const r = await apiFetch(`/api/v1/avatars/${encodeURIComponent(aid)}`, { method: 'DELETE' })
      if (!r.ok) {
        const err = await r.json().catch(() => ({}))
        showToast('err', (err as { detail?: string }).detail || 'Delete avatar failed')
        return
      }
      showToast('ok', `Avatar deleted: ${aid}`)
      await loadData()
      if (avatarDetailId === aid) {
        const next = avatars.find((a) => a.avatar_id !== aid)?.avatar_id || ''
        setAvatarDetailId(next)
        if (next) await loadAvatarDetail(next)
        else setAvatarDetail(null)
      }
      if (avatarId === aid) {
        const nextForJob = avatars.find((a) => a.avatar_id !== aid)?.avatar_id || 'theanikaglow'
        setAvatarId(nextForJob)
      }
    } catch (err) {
      showToast('err', err instanceof Error ? err.message : 'Delete avatar failed')
    } finally {
      setAvatarBusyId(null)
    }
  }

  const runScriptPreview = async () => {
    const pu = productUrl.trim()
    if (!pu) {
      showToast('err', 'Enter a product URL first.')
      return
    }
    setPreviewBusy('script')
    try {
      const manual = productTitleHint.trim()
      const fromUrl = manual ? null : clientAmazonAsinHintFromUrl(pu)
      const product_title = manual || fromUrl || undefined
      const r = await apiFetch('/api/v1/preview/script', {
        method: 'POST',
        body: JSON.stringify({
          product_url: pu,
          avatar_id: avatarId,
          ...(product_title ? { product_title } : {}),
        }),
      })
      const d = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(formatFastApiDetail(d) || r.statusText)
      const row = d as { script?: string; product_hint?: string | null }
      if ('product_hint' in row) {
        setPreviewProductHint(row.product_hint ?? null)
      } else {
        setPreviewProductHint(undefined)
      }
      setScriptPreview(String(row.script || ''))
      showToast('ok', 'Script preview generated')
    } catch (e) {
      showToast('err', e instanceof Error ? e.message : 'Script preview failed')
    } finally {
      setPreviewBusy(null)
    }
  }

  const runTtsPreview = async () => {
    const text = scriptPreview.trim() || 'Hello — this is a quick voice test for KlipAura.'
    setPreviewBusy('tts')
    try {
      const r = await apiFetch('/api/v1/preview/tts', {
        method: 'POST',
        body: JSON.stringify({ text: text.slice(0, 400), avatar_id: avatarId }),
      })
      const d = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(formatFastApiDetail(d) || r.statusText)
      const b64 = (d as { audio_base64?: string }).audio_base64
      if (b64) setTtsAudioUrl(`data:audio/mpeg;base64,${b64}`)
      showToast('ok', 'Voice preview ready')
    } catch (e) {
      showToast('err', e instanceof Error ? e.message : 'TTS preview failed')
    } finally {
      setPreviewBusy(null)
    }
  }

  const saveAvatarEdits = async () => {
    if (!avatarDetail) return
    setSavingAvatar(true)
    try {
      const r1 = await apiFetch(`/api/v1/avatars/${encodeURIComponent(avatarDetail.avatar_id)}/persona`, {
        method: 'PUT',
        body: JSON.stringify({
          display_name: editDisplay,
          niche: editNiche,
          cta_line: editCta,
        }),
      })
      if (!r1.ok) {
        const err = await r1.json().catch(() => ({}))
        showToast('err', formatFastApiDetail(err) || 'Persona save failed')
        return
      }
      const r2 = await apiFetch(`/api/v1/avatars/${encodeURIComponent(avatarDetail.avatar_id)}/social-config`, {
        method: 'PUT',
        body: JSON.stringify({ elevenlabs_voice_id: editVoiceId }),
      })
      if (!r2.ok) {
        const err = await r2.json().catch(() => ({}))
        showToast('err', formatFastApiDetail(err) || 'Social save failed')
        return
      }
      showToast('ok', 'Avatar saved')
      await loadData()
      await loadAvatarDetail(avatarDetail.avatar_id)
    } finally {
      setSavingAvatar(false)
    }
  }

  useEffect(() => {
    if (tab !== 'avatars') return
    if (!avatarDetailId && avatars.length > 0) {
      setAvatarDetailId(avatars[0].avatar_id)
      return
    }
    if (avatarDetailId) void loadAvatarDetail(avatarDetailId)
  }, [tab, avatarDetailId, avatars, loadAvatarDetail])

  if (!loggedIn && !mcSkipLogin()) {
    return (
      <div className="min-h-[100dvh] min-h-screen flex items-center justify-center px-4 py-8 bg-slate-950 w-full max-w-[100vw] overflow-x-hidden">
        <div className="text-center max-w-sm">
          <Loader2 className="w-10 h-10 text-klipaura-400 animate-spin mx-auto mb-4" />
          <p className="text-slate-300 text-sm mb-2">Opening Mission Control to sign in…</p>
          <p className="text-slate-500 text-xs mb-6">Avatar Studio uses the same session as the main dashboard.</p>
          <Link
            href="/"
            className="inline-flex items-center justify-center gap-2 rounded-lg bg-klipaura-600 hover:bg-klipaura-500 text-white text-sm font-medium px-5 py-2.5"
          >
            <ArrowLeft className="w-4 h-4" />
            Go to Mission Control
          </Link>
        </div>
      </div>
    )
  }

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-slate-950">
        <div className="text-center">
          <Loader2 className="w-10 h-10 text-klipaura-400 animate-spin mx-auto mb-4" />
          <p className="text-slate-400 text-sm">Loading Avatar Studio…</p>
        </div>
      </div>
    )
  }

  const pendingAvatar = avatarJobs.filter((j) => j.status === 'pending').length
  const runningAvatar = avatarJobs.filter((j) => j.status === 'running').length
  const hitlAvatar = avatarJobs.filter((j) => j.status === 'awaiting_hitl').length

  const tabBtn = (id: Tab, label: string, icon: React.ReactNode, compact?: boolean) => {
    const active = tab === id
    const base =
      'rounded-xl font-medium transition-all focus:outline-none focus-visible:ring-2 focus-visible:ring-klipaura-500/50'
    const compactCls = compact
      ? `min-h-[3.5rem] flex flex-col items-center justify-center gap-1 px-1 py-2 text-[10px] leading-tight sm:text-xs ${
          active
            ? 'bg-klipaura-500/25 text-klipaura-50 ring-1 ring-inset ring-klipaura-500/40'
            : 'text-slate-400 bg-slate-900/60 active:bg-slate-800'
        }`
      : `flex items-center gap-2.5 px-4 py-2.5 text-sm text-left ${
          active
            ? 'bg-klipaura-500/15 text-klipaura-100 border border-klipaura-500/35 shadow-sm shadow-klipaura-900/20'
            : 'text-slate-400 border border-transparent hover:bg-slate-800/80 hover:text-slate-200'
        }`
    return (
      <button type="button" onClick={() => setTab(id)} className={`${base} ${compactCls}`}>
        <span className="shrink-0 opacity-90">{icon}</span>
        <span className={compact ? 'text-center leading-snug px-0.5' : ''}>{label}</span>
      </button>
    )
  }

  return (
    <div className="min-h-[100dvh] min-h-screen flex flex-col bg-slate-950 text-slate-100 w-full max-w-[100vw] overflow-x-hidden">
      {toast && (
        <div
          className={`fixed z-[100] top-3 left-3 right-3 sm:left-auto sm:right-4 sm:max-w-md px-4 py-3 rounded-xl shadow-xl border text-sm flex items-start gap-3 ${
            toast.type === 'ok'
              ? 'bg-emerald-950/95 border-emerald-800/80 text-emerald-100'
              : toast.type === 'warn'
                ? 'bg-amber-950/95 border-amber-800/80 text-amber-100'
                : 'bg-red-950/95 border-red-900/80 text-red-100'
          }`}
          role="status"
        >
          {toast.type === 'ok' ? (
            <CheckCircle2 className="w-5 h-5 shrink-0 mt-0.5" />
          ) : (
            <AlertCircle className="w-5 h-5 shrink-0 mt-0.5" />
          )}
          <span className="break-words">{toast.text}</span>
        </div>
      )}

      <header className="sticky top-0 z-50 border-b border-slate-800/80 bg-slate-950/95 backdrop-blur-md">
        <div className="mx-auto w-full max-w-[1600px] px-3 sm:px-4 lg:px-8">
          <div className="flex flex-col gap-3 py-3 lg:flex-row lg:items-center lg:justify-between lg:gap-6 lg:py-3 lg:min-h-[3.5rem]">
            <div className="flex items-center gap-2 sm:gap-4 min-w-0">
              <Link
                href="/"
                className="flex items-center justify-center shrink-0 h-10 w-10 rounded-lg text-slate-400 hover:text-white hover:bg-slate-800/80 transition-colors border border-transparent hover:border-slate-700"
                aria-label="Back to Mission Control"
              >
                <ArrowLeft className="w-5 h-5" />
              </Link>
              <div className="h-8 w-px bg-slate-700/80 hidden sm:block" />
              <div className="flex items-center gap-3 min-w-0 flex-1">
                <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-gradient-to-br from-klipaura-500/25 to-klipaura-600/15 border border-klipaura-500/25">
                  <User className="w-5 h-5 text-klipaura-300" />
                </div>
                <div className="min-w-0">
                  <h1 className="text-base sm:text-lg font-semibold text-white tracking-tight truncate">Avatar Studio</h1>
                  <p className="text-[10px] sm:text-[11px] text-slate-500 uppercase tracking-wider truncate">
                    UGC pipeline
                  </p>
                </div>
              </div>
            </div>

            <div className="flex flex-wrap items-center justify-between gap-2 sm:justify-end sm:gap-3 pl-0 sm:pl-2">
              <div
                className="hidden sm:flex items-center gap-2 text-xs text-slate-500"
                title={
                  metrics?.redis_connected
                    ? 'Redis connected — job queues and metrics OK.'
                    : 'Redis unreachable — queues and workers are affected. Creating avatar profiles uses disk only; fix REDIS_URL / Upstash for pipeline jobs.'
                }
              >
                <span
                  className={`inline-flex h-2 w-2 rounded-full shrink-0 ${metrics?.redis_connected ? 'bg-emerald-400 shadow-[0_0_8px_rgba(52,211,153,0.6)]' : 'bg-red-400'}`}
                />
                Redis {metrics?.redis_connected ? 'live' : 'down'}
              </div>
              <div className="flex items-center gap-1.5 text-xs text-slate-500 tabular-nums">
                <Clock className="w-3.5 h-3.5 shrink-0" />
                {metrics ? `${Math.floor(metrics.uptime_seconds / 3600)}h` : '—'}
              </div>
              {hitlUrl ? (
                <a
                  href={`${hitlUrl}/`}
                  target="_blank"
                  rel="noopener noreferrer"
                  title={`Open HITL: ${hitlUrl}${hitlUrl.includes('localhost') || hitlUrl.includes('127.0.0.1') ? ' — localhost only works on the host running Docker; set NEXT_PUBLIC_HITL_URL at build for production.' : ''}`}
                  className="inline-flex items-center justify-center gap-1.5 min-h-10 rounded-lg border border-slate-600 bg-slate-900/50 px-3 text-xs font-medium text-slate-300 hover:border-klipaura-500/40 hover:text-white transition-colors"
                >
                  HITL
                  <ExternalLink className="w-3.5 h-3.5 opacity-70" />
                </a>
              ) : (
                <span
                  className="inline-flex items-center justify-center gap-1.5 min-h-10 rounded-lg border border-dashed border-slate-600 px-3 text-xs text-slate-500 cursor-help"
                  title="Set NEXT_PUBLIC_HITL_URL in Railway to your deployed HITL https URL, then redeploy."
                >
                  HITL
                  <ExternalLink className="w-3.5 h-3.5 opacity-40" />
                </span>
              )}
              {!mcSkipLogin() && (
                <button
                  type="button"
                  onClick={() => {
                    setMcToken(null)
                    window.location.href = '/'
                  }}
                  className="inline-flex items-center justify-center gap-1.5 min-h-10 min-w-10 rounded-lg text-slate-500 hover:text-slate-200 hover:bg-slate-800/80 sm:min-w-0 sm:px-2"
                  aria-label="Sign out"
                >
                  <LogOut className="w-4 h-4" />
                  <span className="hidden sm:inline text-sm">Exit</span>
                </button>
              )}
            </div>
          </div>
        </div>
      </header>

      <div className="flex flex-1 flex-col lg:flex-row w-full min-w-0 max-w-[1600px] mx-auto">
        <aside className="hidden lg:flex w-56 shrink-0 flex-col border-r border-slate-800/60 bg-slate-900/30 p-3 gap-1">
          {tabBtn('overview', 'Dashboard', <LayoutGrid className="w-4 h-4" />)}
          {tabBtn('create', 'Create content', <Sparkles className="w-4 h-4" />)}
          {tabBtn('avatars', 'Avatars', <User className="w-4 h-4" />)}
          {tabBtn('pipeline', 'Pipeline', <Clapperboard className="w-4 h-4" />)}
          {tabBtn('activity', 'Activity', <Activity className="w-4 h-4" />)}
        </aside>

        <nav
          className="lg:hidden grid grid-cols-5 gap-2 p-3 border-b border-slate-800/60 bg-slate-900/50"
          aria-label="Section"
        >
          {tabBtn('overview', 'Overview', <LayoutGrid className="w-4 h-4" />, true)}
          {tabBtn('create', 'Create', <Sparkles className="w-4 h-4" />, true)}
          {tabBtn('avatars', 'Avatars', <User className="w-4 h-4" />, true)}
          {tabBtn('pipeline', 'Pipeline', <Clapperboard className="w-4 h-4" />, true)}
          {tabBtn('activity', 'Activity', <Activity className="w-4 h-4" />, true)}
        </nav>

        <main className="flex-1 min-w-0 w-full px-3 sm:px-4 lg:px-8 py-4 sm:py-6 pb-8 sm:pb-10">
          {tab === 'overview' && (
            <div className="space-y-6 sm:space-y-8 w-full max-w-[1200px] mx-auto">
              <div className="px-0.5">
                <h2 className="text-lg font-semibold text-white tracking-tight">Operations overview</h2>
                <p className="text-sm text-slate-500 mt-1">Live snapshot of klip-avatar jobs and platform health</p>
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-3 sm:gap-4">
                {[
                  {
                    label: 'Queued (global)',
                    value: queues?.jobs_pending ?? '—',
                    sub: 'Redis pending list',
                    icon: <Radio className="w-5 h-5 text-amber-400/90" />,
                  },
                  {
                    label: 'Avatar jobs',
                    value: avatarJobs.length,
                    sub: `${pendingAvatar} pending · ${runningAvatar} running`,
                    icon: <Film className="w-5 h-5 text-klipaura-400" />,
                  },
                  {
                    label: 'Awaiting HITL',
                    value: hitlAvatar,
                    sub: 'Human review',
                    icon: <User className="w-5 h-5 text-klipaura-400/90" />,
                  },
                  {
                    label: 'Queue status',
                    value: queues?.global_paused ? 'Paused' : 'Active',
                    sub: queues?.global_paused ? 'Workers may idle' : 'Processing enabled',
                    icon: <Cpu className="w-5 h-5 text-emerald-400/90" />,
                  },
                ].map((c) => (
                  <div
                    key={c.label}
                    className="rounded-2xl border border-slate-700/60 bg-slate-900/40 p-5 hover:border-slate-600/80 transition-colors"
                  >
                    <div className="flex items-start justify-between gap-2 mb-3">
                      <span className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">{c.label}</span>
                      <div className="rounded-lg bg-slate-800/50 p-2">{c.icon}</div>
                    </div>
                    <p className="text-3xl font-semibold tabular-nums text-white tracking-tight">{c.value}</p>
                    <p className="text-xs text-slate-500 mt-2 leading-relaxed">{c.sub}</p>
                  </div>
                ))}
              </div>

              <div
                className={`rounded-2xl border p-4 sm:p-5 ${
                  workerStatus?.online
                    ? 'border-emerald-800/60 bg-emerald-950/15'
                    : 'border-amber-800/60 bg-amber-950/15'
                }`}
              >
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <h3 className="text-sm font-semibold text-slate-100">Avatar worker readiness</h3>
                  <span
                    className={`text-xs px-2 py-1 rounded-md border ${
                      workerStatus?.online
                        ? 'text-emerald-300 border-emerald-700/70 bg-emerald-900/30'
                        : 'text-amber-300 border-amber-700/70 bg-amber-900/30'
                    }`}
                  >
                    {workerStatus?.online ? 'ONLINE' : 'OFFLINE / STALE'}
                  </span>
                </div>
                <div className="grid sm:grid-cols-3 gap-3 mt-3 text-xs">
                  <div className="rounded-lg border border-slate-700/70 bg-slate-950/40 p-3">
                    <p className="text-slate-500">State</p>
                    <p className="text-slate-200 mt-1">{workerStatus?.state || 'unknown'}</p>
                  </div>
                  <div className="rounded-lg border border-slate-700/70 bg-slate-950/40 p-3">
                    <p className="text-slate-500">Queue depth</p>
                    <p className="text-slate-200 mt-1 tabular-nums">{workerStatus?.queue_depth ?? queues?.jobs_pending ?? 0}</p>
                  </div>
                  <div className="rounded-lg border border-slate-700/70 bg-slate-950/40 p-3">
                    <p className="text-slate-500">Last heartbeat</p>
                    <p className="text-slate-200 mt-1">
                      {workerStatus?.seconds_since_last_seen != null
                        ? `${workerStatus.seconds_since_last_seen}s ago`
                        : 'never'}
                    </p>
                  </div>
                </div>
                {workerStatus?.note && <p className="text-xs text-amber-300/90 mt-3">{workerStatus.note}</p>}
                {!workerStatus?.online && (workerStatus?.queue_depth ?? 0) > 0 && (
                  <p className="text-xs text-slate-300/90 mt-2">
                    Remediation: start/redeploy `klip-avatar` worker, confirm it can reach Redis, then verify
                    heartbeat updates under `/api/v1/workers/avatar`.
                  </p>
                )}
              </div>

              {pipelineHealth && (
                <div className="rounded-2xl border border-slate-700/60 bg-slate-900/40 p-4 sm:p-5 space-y-3">
                  <h3 className="text-sm font-semibold text-slate-100">Pipeline diagnostics</h3>
                  <p className="text-xs text-slate-500">
                    Redis URL (redacted): <span className="font-mono text-slate-400">{pipelineHealth.redis_url_redacted}</span>
                  </p>
                  <div className="flex flex-wrap gap-2">
                    {Object.entries(pipelineHealth.env).map(([k, v]) => (
                      <span
                        key={k}
                        className={`text-[10px] px-2 py-1 rounded-md border ${
                          v ? 'border-emerald-800/70 text-emerald-300 bg-emerald-950/40' : 'border-amber-800/70 text-amber-200 bg-amber-950/30'
                        }`}
                      >
                        {k.replace(/_api_key$/, '').replace(/_/g, ' ')}: {v ? 'set' : 'missing'}
                      </span>
                    ))}
                  </div>
                  <div className="grid sm:grid-cols-3 gap-2 text-xs">
                    <div className="rounded-lg border border-slate-700/80 bg-slate-950/40 p-2">
                      <p className="text-slate-500">Avatars w/ portrait</p>
                      <p className="text-slate-200 mt-0.5">
                        {pipelineHealth.avatars_with_portrait} / {pipelineHealth.avatars_total}
                      </p>
                    </div>
                    <div className="rounded-lg border border-slate-700/80 bg-slate-950/40 p-2">
                      <p className="text-slate-500">Redis (pipeline check)</p>
                      <p className={pipelineHealth.redis_connected ? 'text-emerald-300 mt-0.5' : 'text-red-300 mt-0.5'}>
                        {pipelineHealth.redis_connected ? 'reachable' : 'unreachable'}
                      </p>
                    </div>
                    <div className="rounded-lg border border-slate-700/80 bg-slate-950/40 p-2">
                      <p className="text-slate-500">Worker (health)</p>
                      <p className={pipelineHealth.worker?.online ? 'text-emerald-300 mt-0.5' : 'text-amber-300 mt-0.5'}>
                        {pipelineHealth.worker?.online ? 'online' : 'offline/stale'}
                      </p>
                    </div>
                  </div>
                </div>
              )}

              <div className="rounded-2xl border border-slate-700/50 bg-gradient-to-br from-slate-900/80 to-slate-950/80 p-4 sm:p-6">
                <h3 className="text-sm font-semibold text-slate-200 flex items-center gap-2">
                  <ChevronRight className="w-4 h-4 text-klipaura-400 shrink-0" />
                  Quick actions
                </h3>
                <div className="mt-4 flex flex-col sm:flex-row sm:flex-wrap gap-2 sm:gap-3">
                  <button
                    type="button"
                    onClick={() => setTab('create')}
                    className="inline-flex items-center justify-center gap-2 min-h-11 rounded-xl bg-klipaura-600 hover:bg-klipaura-500 text-white text-sm font-medium px-5 py-2.5 shadow-lg shadow-klipaura-900/25 transition-colors w-full sm:w-auto"
                  >
                    <Sparkles className="w-4 h-4" />
                    New video job
                  </button>
                  <button
                    type="button"
                    onClick={() => setTab('pipeline')}
                    className="inline-flex items-center justify-center gap-2 min-h-11 rounded-xl border border-slate-600 bg-slate-800/50 hover:bg-slate-800 text-slate-200 text-sm font-medium px-5 py-2.5 transition-colors w-full sm:w-auto"
                  >
                    <Clapperboard className="w-4 h-4" />
                    View pipeline
                  </button>
                  <Link
                    href="/"
                    className="inline-flex items-center justify-center gap-2 min-h-11 rounded-xl border border-slate-600 bg-transparent hover:bg-slate-800/50 text-slate-400 hover:text-slate-200 text-sm font-medium px-5 py-2.5 transition-colors w-full sm:w-auto text-center"
                  >
                    Full Mission Control
                  </Link>
                </div>
              </div>
            </div>
          )}

          {tab === 'create' && (
            <div className="w-full max-w-2xl mx-auto space-y-6">
              <div>
                <h2 className="text-lg font-semibold text-white tracking-tight">Create content</h2>
                <p className="text-sm text-slate-500 mt-1">
                  Enqueue a UGC video job for the avatar worker. Requires product URL and a running klip-avatar worker
                  consuming the shared queue.
                </p>
              </div>

              <form onSubmit={handleEnqueue} className="rounded-2xl border border-slate-700/60 bg-slate-900/35 p-6 sm:p-8 space-y-6 shadow-xl shadow-black/20">
                <div className="space-y-2">
                  <label htmlFor="as-product" className="text-sm font-medium text-slate-300">
                    Product URL
                  </label>
                  <input
                    id="as-product"
                    type="url"
                    required
                    value={productUrl}
                    onChange={(e) => setProductUrl(e.target.value)}
                    placeholder="https://…"
                    className="w-full min-h-12 rounded-xl border border-slate-600 bg-slate-950/60 px-4 py-3 text-base md:text-sm text-slate-100 placeholder:text-slate-600 focus:border-klipaura-500/50 focus:ring-2 focus:ring-klipaura-500/15 outline-none transition-shadow"
                  />
                  <p className="text-xs text-slate-500">Affiliate or product page used for script and visuals</p>
                </div>

                <div className="space-y-2">
                  <label htmlFor="as-product-title" className="text-sm font-medium text-slate-300">
                    Product name <span className="text-slate-500 font-normal">(optional)</span>
                  </label>
                  <input
                    id="as-product-title"
                    type="text"
                    value={productTitleHint}
                    onChange={(e) => setProductTitleHint(e.target.value)}
                    placeholder="e.g. Amazon Echo Show 11 — use for amzn.to links if preview is generic"
                    className="w-full min-h-11 rounded-xl border border-slate-600 bg-slate-950/60 px-4 py-2.5 text-base md:text-sm text-slate-100 placeholder:text-slate-600 focus:border-klipaura-500/50 focus:ring-2 focus:ring-klipaura-500/15 outline-none transition-shadow"
                  />
                  <p className="text-xs text-slate-500">
                    Paste the exact product title when using short links — the preview API often cannot scrape Amazon from
                    the server.
                  </p>
                </div>

                <div className="grid sm:grid-cols-2 gap-5">
                  <div className="space-y-2">
                    <label htmlFor="as-studio-persona" className="text-sm font-medium text-slate-300">
                      Saved AI persona <span className="text-slate-500 font-normal">(optional)</span>
                    </label>
                    <select
                      id="as-studio-persona"
                      value={selectedStudioPersonaId}
                      onChange={(e) => setSelectedStudioPersonaId(e.target.value)}
                      className="w-full min-h-12 rounded-xl border border-slate-600 bg-slate-950/60 px-4 py-3 text-base md:text-sm text-slate-100 focus:border-klipaura-500/50 focus:ring-2 focus:ring-klipaura-500/15 outline-none appearance-none bg-[length:1rem] bg-[right_0.75rem_center] bg-no-repeat pr-10"
                      style={{ backgroundImage: `url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' fill='none' viewBox='0 0 24 24' stroke='%2394a3b8'%3E%3Cpath stroke-linecap='round' stroke-linejoin='round' stroke-width='2' d='M19 9l-7 7-7-7'/%3E%3C/svg%3E")` }}
                    >
                      <option value="">— None —</option>
                      {studioPersonas.map((p) => (
                        <option key={p.persona_id} value={p.persona_id}>
                          {p.name || p.persona_id}
                          {p.avatar_id ? ` (${p.avatar_id})` : ''}
                        </option>
                      ))}
                    </select>
                    <p className="text-xs text-slate-500">
                      Uses Studio image + voice for lip-sync (bottom). Open{' '}
                      <Link href="/avatar-studio" className="text-klipaura-400 hover:underline">
                        Avatar Studio
                      </Link>{' '}
                      to create personas.
                    </p>
                  </div>
                  <div className="space-y-2">
                    <label htmlFor="as-split-top" className="text-sm font-medium text-slate-300">
                      Top band height (split-screen)
                    </label>
                    <select
                      id="as-split-top"
                      value={splitTopPct}
                      onChange={(e) => setSplitTopPct(e.target.value)}
                      className="w-full min-h-12 rounded-xl border border-slate-600 bg-slate-950/60 px-4 py-3 text-base md:text-sm text-slate-100 focus:border-klipaura-500/50 focus:ring-2 focus:ring-klipaura-500/15 outline-none appearance-none bg-[length:1rem] bg-[right_0.75rem_center] bg-no-repeat pr-10"
                      style={{ backgroundImage: `url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' fill='none' viewBox='0 0 24 24' stroke='%2394a3b8'%3E%3Cpath stroke-linecap='round' stroke-linejoin='round' stroke-width='2' d='M19 9l-7 7-7-7'/%3E%3C/svg%3E")` }}
                    >
                      <option value="55">55% product / 45% avatar</option>
                      <option value="58">58% / 42% (default)</option>
                      <option value="60">60% / 40%</option>
                    </select>
                    <p className="text-xs text-slate-500">Affiliate split: product visuals on top, avatar lip-sync below</p>
                  </div>
                </div>

                <div className="grid sm:grid-cols-2 gap-5">
                  <div className="space-y-2">
                    <label htmlFor="as-avatar" className="text-sm font-medium text-slate-300">
                      Avatar profile
                    </label>
                    <select
                      id="as-avatar"
                      value={avatarId}
                      onChange={(e) => setAvatarId(e.target.value)}
                      className="w-full min-h-12 rounded-xl border border-slate-600 bg-slate-950/60 px-4 py-3 text-base md:text-sm text-slate-100 focus:border-klipaura-500/50 focus:ring-2 focus:ring-klipaura-500/15 outline-none appearance-none bg-[length:1rem] bg-[right_0.75rem_center] bg-no-repeat pr-10"
                      style={{ backgroundImage: `url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' fill='none' viewBox='0 0 24 24' stroke='%2394a3b8'%3E%3Cpath stroke-linecap='round' stroke-linejoin='round' stroke-width='2' d='M19 9l-7 7-7-7'/%3E%3C/svg%3E")` }}
                    >
                      {avatars.length > 0 ? (
                        avatars.map((a) => (
                          <option key={a.avatar_id} value={a.avatar_id}>
                            {a.avatar_id}
                          </option>
                        ))
                      ) : (
                        <option value="theanikaglow">theanikaglow</option>
                      )}
                    </select>
                  </div>
                  <div className="space-y-2">
                    <label htmlFor="as-type" className="text-sm font-medium text-slate-300">
                      Job type
                    </label>
                    <input
                      id="as-type"
                      type="text"
                      value={jobType}
                      onChange={(e) => setJobType(e.target.value)}
                      className="w-full min-h-12 rounded-xl border border-slate-600 bg-slate-950/60 px-4 py-3 text-base md:text-sm text-slate-100 focus:border-klipaura-500/50 focus:ring-2 focus:ring-klipaura-500/15 outline-none"
                    />
                  </div>
                </div>

                <label className="flex items-start gap-3 rounded-xl border border-slate-700/60 bg-slate-950/25 p-4 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={generateFunnel}
                    onChange={(e) => setGenerateFunnel(e.target.checked)}
                    className="mt-1 rounded border-slate-600 bg-slate-950 text-klipaura-500 focus:ring-klipaura-500"
                  />
                  <span>
                    <span className="text-sm font-medium text-slate-200">Generate landing page (funnel)</span>
                    <span className="block text-xs text-slate-500 mt-1 leading-relaxed">
                      Publishes a mobile page with your video embed, product copy, affiliate CTA, and disclosure. URLs
                      appear in the job manifest and on the Affiliate Pipeline page.
                    </span>
                  </span>
                </label>

                <div className="space-y-2">
                  <label htmlFor="as-disclosure" className="text-sm font-medium text-slate-300">
                    Affiliate disclosure <span className="text-slate-500 font-normal">(optional)</span>
                  </label>
                  <textarea
                    id="as-disclosure"
                    rows={2}
                    value={affiliateDisclosure}
                    onChange={(e) => setAffiliateDisclosure(e.target.value)}
                    placeholder="Default disclosure is used if empty"
                    className="w-full rounded-xl border border-slate-600 bg-slate-950/60 px-4 py-3 text-sm text-slate-100 placeholder:text-slate-600 focus:border-klipaura-500/50 focus:ring-2 focus:ring-klipaura-500/15 outline-none resize-y min-h-[4rem]"
                  />
                </div>

                <div className="space-y-3">
                  <p className="text-sm font-medium text-slate-300">Video template</p>
                  <div className="grid sm:grid-cols-2 gap-2 max-h-[280px] overflow-y-auto pr-1">
                    {videoTemplates.length === 0 && (
                      <p className="text-xs text-slate-500 col-span-2">Loading templates…</p>
                    )}
                    {videoTemplates.map((t) => (
                      <button
                        key={t.id}
                        type="button"
                        onClick={() => setTemplateId(t.id)}
                        className={`text-left rounded-xl border p-3 transition-colors ${
                          templateId === t.id
                            ? 'border-klipaura-500/70 bg-klipaura-950/40 ring-1 ring-klipaura-500/30'
                            : 'border-slate-700 bg-slate-950/40 hover:border-slate-600'
                        }`}
                      >
                        <p className="text-sm font-medium text-slate-100">{t.name}</p>
                        {t.description && <p className="text-[11px] text-slate-500 mt-1 line-clamp-2">{t.description}</p>}
                        {t.preview_hint && (
                          <p className="text-[10px] text-klipaura-400/90 mt-1">{t.preview_hint}</p>
                        )}
                      </button>
                    ))}
                  </div>
                </div>

                <div className="rounded-xl border border-slate-700/80 bg-slate-950/30 p-4 space-y-3">
                  <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">Script & voice preview (optional)</p>
                  <div className="flex flex-wrap gap-2">
                    <button
                      type="button"
                      onClick={() => void runScriptPreview()}
                      disabled={previewBusy !== null}
                      className="inline-flex items-center gap-2 rounded-lg border border-slate-600 bg-slate-800/60 px-3 py-2 text-xs text-slate-200 hover:bg-slate-800 disabled:opacity-50"
                    >
                      {previewBusy === 'script' ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : null}
                      Generate script preview
                    </button>
                    <button
                      type="button"
                      onClick={() => void runTtsPreview()}
                      disabled={previewBusy !== null}
                      className="inline-flex items-center gap-2 rounded-lg border border-slate-600 bg-slate-800/60 px-3 py-2 text-xs text-slate-200 hover:bg-slate-800 disabled:opacity-50"
                    >
                      {previewBusy === 'tts' ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : null}
                      Preview voice (ElevenLabs)
                    </button>
                  </div>
                  {previewProductHint !== undefined && (
                    <p className="text-[11px] leading-snug text-slate-500">
                      {previewProductHint ? (
                        <>
                          Script grounded on product title:{' '}
                          <span className="text-slate-400">{previewProductHint.slice(0, 200)}</span>
                          {previewProductHint.length > 200 ? '…' : ''}
                        </>
                      ) : (
                        <span className="text-amber-400/90">
                          No product hint (Amazon blocked the server or the link did not expand). Paste a{' '}
                          <span className="text-slate-300">full amazon.…/dp/…</span> URL, or fill <strong>Product name</strong>{' '}
                          above, then run preview again — the full pipeline still scrapes the real product.
                        </span>
                      )}
                    </p>
                  )}
                  {scriptPreview && (
                    <textarea
                      readOnly
                      value={scriptPreview}
                      className="w-full min-h-[100px] rounded-lg border border-slate-700 bg-slate-950/60 px-3 py-2 text-xs text-slate-300"
                    />
                  )}
                  {ttsAudioUrl && <audio controls src={ttsAudioUrl} className="w-full max-w-md h-9" />}
                </div>

                <button
                  type="submit"
                  disabled={submitting}
                  className="w-full sm:w-auto inline-flex items-center justify-center gap-2 rounded-xl bg-gradient-to-r from-klipaura-600 to-klipaura-600 hover:from-klipaura-500 hover:to-klipaura-500 disabled:opacity-50 text-white font-semibold text-sm px-8 py-3.5 shadow-lg shadow-klipaura-900/30 transition-all"
                >
                  {submitting ? <Loader2 className="w-5 h-5 animate-spin" /> : <Sparkles className="w-5 h-5" />}
                  Enqueue video job
                </button>
              </form>
            </div>
          )}

          {tab === 'avatars' && (
            <div className="space-y-6 w-full max-w-[1200px] mx-auto">
              <div>
                <h2 className="text-lg font-semibold text-white tracking-tight">Avatar profiles</h2>
                <p className="text-sm text-slate-500 mt-1">
                  Create, inspect, and delete avatar profiles used by the video pipeline.
                </p>
              </div>

              <form
                id="avatar-create-form"
                onSubmit={handleCreateAvatar}
                className="rounded-2xl border border-slate-700/60 bg-slate-900/35 p-5 sm:p-6 space-y-4"
              >
                <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-3">
                  <input
                    value={newAvatarId}
                    onChange={(e) => setNewAvatarId(e.target.value)}
                    placeholder="avatar_id (required)"
                    className="min-h-11 rounded-xl border border-slate-600 bg-slate-950/60 px-3 text-sm text-slate-100 placeholder:text-slate-600 focus:border-klipaura-500/50 focus:ring-2 focus:ring-klipaura-500/15 outline-none"
                    required
                  />
                  <input
                    value={newAvatarName}
                    onChange={(e) => setNewAvatarName(e.target.value)}
                    placeholder="Display name"
                    className="min-h-11 rounded-xl border border-slate-600 bg-slate-950/60 px-3 text-sm text-slate-100 placeholder:text-slate-600 focus:border-klipaura-500/50 focus:ring-2 focus:ring-klipaura-500/15 outline-none"
                  />
                  <input
                    value={newAvatarNiche}
                    onChange={(e) => setNewAvatarNiche(e.target.value)}
                    placeholder="Niche (e.g. beauty-tech)"
                    className="min-h-11 rounded-xl border border-slate-600 bg-slate-950/60 px-3 text-sm text-slate-100 placeholder:text-slate-600 focus:border-klipaura-500/50 focus:ring-2 focus:ring-klipaura-500/15 outline-none"
                  />
                  <select
                    value={newAvatarTone}
                    onChange={(e) => setNewAvatarTone(e.target.value)}
                    className="min-h-11 rounded-xl border border-slate-600 bg-slate-950/60 px-3 text-sm text-slate-100 focus:border-klipaura-500/50 focus:ring-2 focus:ring-klipaura-500/15 outline-none"
                  >
                    <option value="friendly">Tone: Friendly</option>
                    <option value="premium">Tone: Premium</option>
                    <option value="playful">Tone: Playful</option>
                    <option value="minimal">Tone: Minimal</option>
                  </select>
                  <select
                    value={newAvatarStyle}
                    onChange={(e) => setNewAvatarStyle(e.target.value)}
                    className="min-h-11 rounded-xl border border-slate-600 bg-slate-950/60 px-3 text-sm text-slate-100 focus:border-klipaura-500/50 focus:ring-2 focus:ring-klipaura-500/15 outline-none"
                  >
                    <option value="ugc">Style: UGC</option>
                    <option value="cinematic">Style: Cinematic</option>
                    <option value="review">Style: Review</option>
                    <option value="story">Style: Story</option>
                  </select>
                  <select
                    value={newAvatarLanguage}
                    onChange={(e) => setNewAvatarLanguage(e.target.value)}
                    className="min-h-11 rounded-xl border border-slate-600 bg-slate-950/60 px-3 text-sm text-slate-100 focus:border-klipaura-500/50 focus:ring-2 focus:ring-klipaura-500/15 outline-none"
                  >
                    <option value="en">Language: English</option>
                    <option value="ar">Language: Arabic</option>
                    <option value="hi">Language: Hindi</option>
                  </select>
                  <input
                    value={newAvatarVoice}
                    onChange={(e) => setNewAvatarVoice(e.target.value)}
                    placeholder="ElevenLabs voice_id"
                    className="min-h-11 rounded-xl border border-slate-600 bg-slate-950/60 px-3 text-sm text-slate-100 placeholder:text-slate-600 focus:border-klipaura-500/50 focus:ring-2 focus:ring-klipaura-500/15 outline-none"
                  />
                  <input
                    value={newAvatarCta}
                    onChange={(e) => setNewAvatarCta(e.target.value)}
                    placeholder="CTA line"
                    className="min-h-11 rounded-xl border border-slate-600 bg-slate-950/60 px-3 text-sm text-slate-100 placeholder:text-slate-600 focus:border-klipaura-500/50 focus:ring-2 focus:ring-klipaura-500/15 outline-none"
                  />
                </div>

                <div className="rounded-xl border border-slate-700/80 p-3 space-y-3">
                  <p className="text-xs uppercase tracking-wide text-slate-500">Per-avatar social isolation</p>
                  <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-3">
                    <input value={newGetlateKey} onChange={(e) => setNewGetlateKey(e.target.value)} placeholder="GetLate API key" className="min-h-11 rounded-xl border border-slate-600 bg-slate-950/60 px-3 text-sm text-slate-100 placeholder:text-slate-600 focus:border-klipaura-500/50 focus:ring-2 focus:ring-klipaura-500/15 outline-none" />
                    <input value={newZerioKey} onChange={(e) => setNewZerioKey(e.target.value)} placeholder="Zerio API key" className="min-h-11 rounded-xl border border-slate-600 bg-slate-950/60 px-3 text-sm text-slate-100 placeholder:text-slate-600 focus:border-klipaura-500/50 focus:ring-2 focus:ring-klipaura-500/15 outline-none" />
                    <input value={newAmazonTag} onChange={(e) => setNewAmazonTag(e.target.value)} placeholder="Amazon affiliate tag" className="min-h-11 rounded-xl border border-slate-600 bg-slate-950/60 px-3 text-sm text-slate-100 placeholder:text-slate-600 focus:border-klipaura-500/50 focus:ring-2 focus:ring-klipaura-500/15 outline-none" />
                    <input value={newTiktokShopId} onChange={(e) => setNewTiktokShopId(e.target.value)} placeholder="TikTok shop id" className="min-h-11 rounded-xl border border-slate-600 bg-slate-950/60 px-3 text-sm text-slate-100 placeholder:text-slate-600 focus:border-klipaura-500/50 focus:ring-2 focus:ring-klipaura-500/15 outline-none" />
                    <input value={newTiktokProfile} onChange={(e) => setNewTiktokProfile(e.target.value)} placeholder="TikTok profile id" className="min-h-11 rounded-xl border border-slate-600 bg-slate-950/60 px-3 text-sm text-slate-100 placeholder:text-slate-600 focus:border-klipaura-500/50 focus:ring-2 focus:ring-klipaura-500/15 outline-none" />
                    <input value={newInstagramProfile} onChange={(e) => setNewInstagramProfile(e.target.value)} placeholder="Instagram profile id" className="min-h-11 rounded-xl border border-slate-600 bg-slate-950/60 px-3 text-sm text-slate-100 placeholder:text-slate-600 focus:border-klipaura-500/50 focus:ring-2 focus:ring-klipaura-500/15 outline-none" />
                    <input value={newYoutubeProfile} onChange={(e) => setNewYoutubeProfile(e.target.value)} placeholder="YouTube profile id" className="min-h-11 rounded-xl border border-slate-600 bg-slate-950/60 px-3 text-sm text-slate-100 placeholder:text-slate-600 focus:border-klipaura-500/50 focus:ring-2 focus:ring-klipaura-500/15 outline-none" />
                  </div>
                </div>

                <div className="rounded-xl border border-slate-700/80 p-3 space-y-3">
                  <p className="text-xs uppercase tracking-wide text-slate-500">Avatar image source</p>
                  <div className="flex flex-wrap gap-3 text-xs">
                    <label className="inline-flex items-center gap-2 text-slate-300">
                      <input type="radio" checked={imageSource === 'none'} onChange={() => setImageSource('none')} />
                      Set later
                    </label>
                    <label className="inline-flex items-center gap-2 text-slate-300">
                      <input type="radio" checked={imageSource === 'upload'} onChange={() => setImageSource('upload')} />
                      Upload image
                    </label>
                    <label className="inline-flex items-center gap-2 text-slate-300">
                      <input type="radio" checked={imageSource === 'prompt'} onChange={() => setImageSource('prompt')} />
                      Generate via WaveSpeed prompt
                    </label>
                  </div>
                  {imageSource === 'upload' && (
                    <input
                      type="file"
                      accept="image/*"
                      onChange={(e) => setAvatarImageFile(e.target.files?.[0] || null)}
                      className="block w-full text-sm text-slate-300 file:mr-3 file:rounded-lg file:border-0 file:bg-slate-800 file:px-3 file:py-2 file:text-slate-200"
                    />
                  )}
                  {imageSource === 'prompt' && (
                    <textarea
                      value={avatarImagePrompt}
                      onChange={(e) => setAvatarImagePrompt(e.target.value)}
                      placeholder="Describe the avatar image you want WaveSpeed to generate..."
                      className="w-full min-h-[90px] rounded-xl border border-slate-600 bg-slate-950/60 px-3 py-2 text-sm text-slate-100 placeholder:text-slate-600 focus:border-klipaura-500/50 focus:ring-2 focus:ring-klipaura-500/15 outline-none"
                    />
                  )}
                </div>

                <button
                  type="submit"
                  disabled={creatingAvatar}
                  className="min-h-11 rounded-xl bg-klipaura-600 hover:bg-klipaura-500 disabled:opacity-50 text-white text-sm font-semibold px-4"
                >
                  {creatingAvatar ? 'Creating…' : 'Create complete avatar'}
                </button>
              </form>

              <div className="grid lg:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)] gap-4">
                <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
                  {avatars.map((a) => (
                    <button
                      key={a.avatar_id}
                      type="button"
                      onClick={() => {
                        setAvatarDetailId(a.avatar_id)
                        void loadAvatarDetail(a.avatar_id)
                      }}
                      className={`rounded-2xl border text-left overflow-hidden transition-colors ${
                        avatarDetailId === a.avatar_id
                          ? 'border-klipaura-500/60 ring-1 ring-klipaura-500/25 bg-slate-800/40'
                          : 'border-slate-700/60 bg-slate-900/30 hover:border-slate-600'
                      }`}
                    >
                      <div className="aspect-[3/4] bg-slate-950/80 flex items-center justify-center overflow-hidden">
                        {thumbUrls[a.avatar_id] ? (
                          // eslint-disable-next-line @next/next/no-img-element
                          <img
                            src={thumbUrls[a.avatar_id]}
                            alt=""
                            className="w-full h-full object-cover"
                          />
                        ) : (
                          <ImageIcon className="w-10 h-10 text-slate-600" />
                        )}
                      </div>
                      <div className="p-3 space-y-1">
                        <p className="text-sm font-medium text-slate-100 truncate">{a.avatar_id}</p>
                        <p className="text-[11px] text-slate-500 truncate">{a.display_name}</p>
                        <div className="flex flex-wrap gap-1 pt-1">
                          {!a.has_portrait && (
                            <span className="text-[9px] px-1.5 py-0.5 rounded bg-red-950/60 text-red-300 border border-red-900/50">
                              No portrait
                            </span>
                          )}
                          {!a.has_social_config && (
                            <span className="text-[9px] px-1.5 py-0.5 rounded bg-amber-950/50 text-amber-200 border border-amber-900/40">
                              Social
                            </span>
                          )}
                          {a.has_portrait && (
                            <span className="text-[9px] px-1.5 py-0.5 rounded bg-emerald-950/40 text-emerald-300 border border-emerald-900/40">
                              Ready
                            </span>
                          )}
                        </div>
                      </div>
                    </button>
                  ))}
                  <button
                    type="button"
                    onClick={() => {
                      document.getElementById('avatar-create-form')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
                    }}
                    className="rounded-2xl border border-dashed border-slate-600 bg-slate-900/20 min-h-[200px] flex flex-col items-center justify-center gap-2 text-slate-500 hover:text-klipaura-300 hover:border-klipaura-600/40 transition-colors"
                  >
                    <span className="text-3xl font-light">+</span>
                    <span className="text-xs font-medium">New avatar</span>
                  </button>
                  {avatars.length === 0 && (
                    <p className="col-span-full text-sm text-slate-500">No avatars — create one or check AVATAR_DATA_DIR / Docker mount.</p>
                  )}
                </div>

                <div className="rounded-2xl border border-slate-700/60 bg-slate-900/30 p-4 sm:p-5 space-y-4">
                  {avatarDetail ? (
                    <>
                      <div className="flex flex-wrap items-start justify-between gap-3">
                        <div className="flex gap-4 min-w-0">
                          <div className="w-20 h-20 rounded-xl border border-slate-700 overflow-hidden bg-slate-950 shrink-0 flex items-center justify-center">
                            {thumbUrls[avatarDetail.avatar_id] ? (
                              // eslint-disable-next-line @next/next/no-img-element
                              <img src={thumbUrls[avatarDetail.avatar_id]} alt="" className="w-full h-full object-cover" />
                            ) : (
                              <ImageIcon className="w-8 h-8 text-slate-600" />
                            )}
                          </div>
                          <div className="min-w-0">
                            <h3 className="text-base font-semibold text-slate-100">{avatarDetail.avatar_id}</h3>
                            {!avatarDetail.has_portrait && (
                              <p className="text-xs text-red-400 mt-1">
                                Pipeline needs a portrait image — upload below or the worker cannot run UGC video.
                              </p>
                            )}
                          </div>
                        </div>
                        <button
                          type="button"
                          onClick={() => void handleDeleteAvatar(avatarDetail.avatar_id)}
                          disabled={avatarBusyId === avatarDetail.avatar_id}
                          className="rounded-lg border border-red-900/70 bg-red-950/30 px-3 py-1.5 text-xs text-red-300 hover:bg-red-950/50 disabled:opacity-50"
                        >
                          {avatarBusyId === avatarDetail.avatar_id ? 'Deleting…' : 'Delete'}
                        </button>
                      </div>

                      <div className="grid sm:grid-cols-2 gap-3 text-xs">
                        <label className="block space-y-1">
                          <span className="text-slate-500">Display name</span>
                          <input
                            value={editDisplay}
                            onChange={(e) => setEditDisplay(e.target.value)}
                            className="w-full rounded-lg border border-slate-600 bg-slate-950/60 px-2 py-2 text-slate-100"
                          />
                        </label>
                        <label className="block space-y-1">
                          <span className="text-slate-500">Niche</span>
                          <input
                            value={editNiche}
                            onChange={(e) => setEditNiche(e.target.value)}
                            className="w-full rounded-lg border border-slate-600 bg-slate-950/60 px-2 py-2 text-slate-100"
                          />
                        </label>
                        <label className="block space-y-1 sm:col-span-2">
                          <span className="text-slate-500">CTA line</span>
                          <input
                            value={editCta}
                            onChange={(e) => setEditCta(e.target.value)}
                            className="w-full rounded-lg border border-slate-600 bg-slate-950/60 px-2 py-2 text-slate-100"
                          />
                        </label>
                        <label className="block space-y-1 sm:col-span-2">
                          <span className="text-slate-500">ElevenLabs voice ID</span>
                          <input
                            value={editVoiceId}
                            onChange={(e) => setEditVoiceId(e.target.value)}
                            className="w-full rounded-lg border border-slate-600 bg-slate-950/60 px-2 py-2 text-slate-100"
                          />
                        </label>
                      </div>

                      <div className="flex flex-wrap gap-2">
                        <button
                          type="button"
                          onClick={() => void saveAvatarEdits()}
                          disabled={savingAvatar}
                          className="inline-flex items-center gap-2 rounded-lg bg-klipaura-600 hover:bg-klipaura-500 disabled:opacity-50 text-white text-xs font-semibold px-4 py-2"
                        >
                          {savingAvatar ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : null}
                          Save profile
                        </button>
                        <button
                          type="button"
                          onClick={async () => {
                            const t = editCta || 'This is a voice test for my KlipAura avatar.'
                            setPreviewBusy('tts')
                            try {
                              const r = await apiFetch('/api/v1/preview/tts', {
                                method: 'POST',
                                body: JSON.stringify({
                                  text: t.slice(0, 400),
                                  avatar_id: avatarDetail.avatar_id,
                                  voice_id: editVoiceId || undefined,
                                }),
                              })
                              const d = await r.json().catch(() => ({}))
                              if (!r.ok) throw new Error(formatFastApiDetail(d) || r.statusText)
                              const b64 = (d as { audio_base64?: string }).audio_base64
                              if (b64) setTtsAudioUrl(`data:audio/mpeg;base64,${b64}`)
                              showToast('ok', 'Voice test playing below')
                            } catch (e) {
                              showToast('err', e instanceof Error ? e.message : 'Voice test failed')
                            } finally {
                              setPreviewBusy(null)
                            }
                          }}
                          disabled={previewBusy !== null}
                          className="inline-flex items-center gap-2 rounded-lg border border-slate-600 bg-slate-800/50 px-4 py-2 text-xs text-slate-200"
                        >
                          <Mic className="w-3.5 h-3.5" />
                          Test voice
                        </button>
                      </div>
                      {ttsAudioUrl && (
                        <audio controls src={ttsAudioUrl} className="w-full max-w-md h-9" />
                      )}

                      <div
                        onDragOver={(e) => {
                          e.preventDefault()
                          setDragOver(true)
                        }}
                        onDragLeave={() => setDragOver(false)}
                        onDrop={async (e) => {
                          e.preventDefault()
                          setDragOver(false)
                          const f = e.dataTransfer.files?.[0]
                          if (!f || !f.type.startsWith('image/')) {
                            showToast('err', 'Drop an image file')
                            return
                          }
                          const fd = new FormData()
                          fd.append('image', f)
                          const up = await fetch(
                            `${baseUrl}/api/v1/avatars/${encodeURIComponent(avatarDetail.avatar_id)}/image-upload`,
                            {
                              method: 'POST',
                              headers: getMcToken() ? { Authorization: `Bearer ${getMcToken()}` } : {},
                              body: fd,
                            },
                          )
                          if (!up.ok) {
                            const err = await up.json().catch(() => ({}))
                            showToast('err', formatFastApiDetail(err) || 'Upload failed')
                            return
                          }
                          showToast('ok', 'Portrait updated')
                          await loadData()
                          await loadAvatarDetail(avatarDetail.avatar_id)
                        }}
                        className={`rounded-xl border-2 border-dashed p-6 text-center transition-colors ${
                          dragOver ? 'border-klipaura-500 bg-klipaura-950/20' : 'border-slate-600 bg-slate-950/30'
                        }`}
                      >
                        <p className="text-xs text-slate-500 mb-2">Drag & drop portrait here or choose file</p>
                        <input
                          type="file"
                          accept="image/*"
                          onChange={async (e) => {
                            const f = e.target.files?.[0]
                            if (!f || !avatarDetail) return
                            const fd = new FormData()
                            fd.append('image', f)
                            const up = await fetch(
                              `${baseUrl}/api/v1/avatars/${encodeURIComponent(avatarDetail.avatar_id)}/image-upload`,
                              {
                                method: 'POST',
                                headers: getMcToken() ? { Authorization: `Bearer ${getMcToken()}` } : {},
                                body: fd,
                              },
                            )
                            if (!up.ok) {
                              const err = await up.json().catch(() => ({}))
                              showToast('err', formatFastApiDetail(err) || 'Upload failed')
                              return
                            }
                            showToast('ok', 'Portrait updated')
                            await loadData()
                            await loadAvatarDetail(avatarDetail.avatar_id)
                          }}
                          className="text-xs text-slate-400 file:mr-2 file:rounded file:bg-slate-800 file:px-2 file:py-1"
                        />
                      </div>

                      <details className="rounded-lg border border-slate-700/80 bg-slate-950/40">
                        <summary className="px-3 py-2 text-xs text-slate-500 cursor-pointer">Raw JSON</summary>
                        <div className="p-3 pt-0 space-y-2 border-t border-slate-800/80">
                          <pre className="text-[10px] text-slate-400 overflow-auto max-h-36">{JSON.stringify(avatarDetail.persona || {}, null, 2)}</pre>
                          <pre className="text-[10px] text-slate-400 overflow-auto max-h-36">{JSON.stringify(avatarDetail.social_config || {}, null, 2)}</pre>
                        </div>
                      </details>
                    </>
                  ) : (
                    <p className="text-sm text-slate-500">Select an avatar card to edit.</p>
                  )}
                </div>
              </div>
            </div>
          )}

          {tab === 'pipeline' && (
            <div className="space-y-4 sm:space-y-6 w-full max-w-[1200px] mx-auto">
              <div className="px-0.5">
                <h2 className="text-lg font-semibold text-white tracking-tight">Pipeline</h2>
                <p className="text-sm text-slate-500 mt-1 max-w-2xl">
                  klip-avatar jobs only — progress updates when the worker reports state
                </p>
              </div>

              {/* Mobile: stacked cards */}
              <div className="flex flex-col gap-3 md:hidden">
                {avatarJobs.map((job) => (
                  <div
                    key={job.id}
                    className="rounded-2xl border border-slate-700/60 bg-slate-900/40 p-4 space-y-3 shadow-sm"
                  >
                    <div className="flex items-start justify-between gap-2">
                      <div className="min-w-0">
                        <p className="text-xs font-mono text-slate-500 break-all leading-relaxed">{job.id}</p>
                        <p className="text-sm font-medium text-slate-200 mt-1">{job.job_type}</p>
                      </div>
                      <StatusBadge status={job.status} />
                    </div>
                    <div>
                      <div className="flex justify-between text-[10px] uppercase tracking-wider text-slate-500 mb-1">
                        <span>Progress</span>
                        <span className="tabular-nums">{job.progress}%</span>
                      </div>
                      <div className="h-2 rounded-full bg-slate-800 overflow-hidden">
                        <div
                          className="h-full rounded-full bg-gradient-to-r from-klipaura-500 to-klipaura-500 transition-all"
                          style={{ width: `${Math.min(100, Math.max(0, job.progress))}%` }}
                        />
                      </div>
                    </div>
                    <p className="text-[11px] text-slate-500">{new Date(job.created_at).toLocaleString()}</p>
                  </div>
                ))}
              </div>

              {/* Desktop: table */}
              <div className="hidden md:block rounded-2xl border border-slate-700/50 overflow-hidden bg-slate-900/25">
                <div className="overflow-x-auto">
                  <table className="w-full min-w-[640px] text-sm">
                    <thead>
                      <tr className="border-b border-slate-700/80 bg-slate-900/60 text-left text-[11px] uppercase tracking-wider text-slate-500">
                        <th className="px-4 lg:px-5 py-3 font-semibold">Job</th>
                        <th className="px-4 lg:px-5 py-3 font-semibold">Type</th>
                        <th className="px-4 lg:px-5 py-3 font-semibold">Status</th>
                        <th className="px-4 lg:px-5 py-3 font-semibold">Progress</th>
                        <th className="px-4 lg:px-5 py-3 font-semibold">Created</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-800/80">
                      {avatarJobs.map((job) => (
                        <tr key={job.id} className="hover:bg-slate-800/25 transition-colors">
                          <td className="px-4 lg:px-5 py-4">
                            <span className="font-mono text-xs text-slate-500">{job.id.slice(0, 12)}…</span>
                          </td>
                          <td className="px-4 lg:px-5 py-4 text-slate-300">{job.job_type}</td>
                          <td className="px-4 lg:px-5 py-4">
                            <StatusBadge status={job.status} />
                          </td>
                          <td className="px-4 lg:px-5 py-4">
                            <div className="flex items-center gap-3 max-w-[160px]">
                              <div className="flex-1 h-2 rounded-full bg-slate-800 overflow-hidden">
                                <div
                                  className="h-full rounded-full bg-gradient-to-r from-klipaura-500 to-klipaura-500 transition-all"
                                  style={{ width: `${Math.min(100, Math.max(0, job.progress))}%` }}
                                />
                              </div>
                              <span className="text-xs text-slate-500 tabular-nums w-9">{job.progress}%</span>
                            </div>
                          </td>
                          <td className="px-4 lg:px-5 py-4 text-xs text-slate-500 whitespace-nowrap">
                            {new Date(job.created_at).toLocaleString()}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>

              {avatarJobs.length === 0 && (
                <div className="rounded-2xl border border-dashed border-slate-700 py-16 sm:py-20 text-center px-4">
                  <Clapperboard className="w-12 h-12 text-slate-700 mx-auto mb-3" />
                  <p className="text-slate-500 text-sm">No klip-avatar jobs in the store yet</p>
                  <button
                    type="button"
                    onClick={() => setTab('create')}
                    className="mt-4 text-klipaura-400 hover:text-klipaura-300 text-sm font-medium"
                  >
                    Create your first job →
                  </button>
                </div>
              )}
            </div>
          )}

          {tab === 'activity' && (
            <div className="space-y-4 sm:space-y-6 w-full max-w-[1200px] mx-auto">
              <div className="px-0.5">
                <h2 className="text-lg font-semibold text-white tracking-tight">Activity</h2>
                <p className="text-sm text-slate-500 mt-1">Stream of events from klip-avatar (and related ingest)</p>
              </div>

              <div className="rounded-2xl border border-slate-700/50 divide-y divide-slate-800/80 bg-slate-900/20 max-h-[min(70vh,32rem)] sm:max-h-[70vh] overflow-y-auto overscroll-contain">
                {avatarEvents.map((ev) => (
                  <div key={ev.id} className="px-3 sm:px-5 py-3 sm:py-4 hover:bg-slate-800/20 transition-colors flex gap-3 sm:gap-4">
                    <div className="pt-1.5">
                      <SeverityDot severity={ev.severity} />
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="flex flex-wrap items-center gap-2 mb-1">
                        <span className="text-[10px] font-mono uppercase text-slate-500">{ev.event_type || 'event'}</span>
                        <span className="text-[10px] text-slate-600">{new Date(ev.timestamp).toLocaleString()}</span>
                      </div>
                      <p className="text-sm text-slate-300 leading-relaxed">{ev.message}</p>
                    </div>
                  </div>
                ))}
                {avatarEvents.length === 0 && (
                  <div className="py-20 text-center text-slate-500 text-sm">No avatar events yet — run a job or check the full Events tab in Mission Control</div>
                )}
              </div>
            </div>
          )}
        </main>
      </div>
    </div>
  )
}
