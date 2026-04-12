'use client'

import Link from 'next/link'
import { useRouter } from 'next/navigation'
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import {
  AnimatePresence,
  motion,
  useReducedMotion,
} from 'framer-motion'
import {
  ArrowLeft,
  Check,
  Loader2,
  Mic,
  Pencil,
  RefreshCw,
  Save,
  Sparkles,
  Video,
  Volume2,
  UserCircle2,
  Clapperboard,
  X,
} from 'lucide-react'
import { createApiFetch, getApiBase, getMcToken, mcSkipLogin } from '@/lib/mc-client'

type StudioPersona = {
  persona_id: string
  avatar_id: string
  name: string
  image_url: string
  voice_id: string
  created_at: string
  prompt?: string
}

type GenerateResponse = {
  persona_id: string
  avatar_id: string
  image_url: string
  voice_id: string
  created_at: string
}

function friendlyError(r: Response, body: unknown): string {
  if (body && typeof body === 'object' && 'detail' in body) {
    const d = (body as { detail: unknown }).detail
    if (typeof d === 'string') return d
  }
  if (r.status === 401) return 'Session expired — sign in again from the dashboard.'
  if (r.status === 503) return 'Service is temporarily unavailable. Try again in a moment.'
  if (r.status >= 500) return 'Something went wrong on our side. Please try again.'
  return 'Request could not be completed.'
}

const PRESETS = {
  age: ['18-24', '25-35', '35-45', '45+'],
  look: ['Middle Eastern', 'South Asian', 'East Asian', 'European', 'African', 'Latin American', 'Mixed'],
  outfit: ['Casual tee', 'Red Polo', 'Black blazer', 'Hoodie', 'Business casual', 'Streetwear'],
  personality: ['Friendly Reviewer', 'Energetic host', 'Calm expert', 'Bold opinion', 'Warm storyteller'],
  voice_tone: ['Warm Male', 'Warm Female', 'Energetic', 'Deep Confident', 'Professional', 'Friendly'],
}

const springBtn = { type: 'spring' as const, stiffness: 420, damping: 24, mass: 0.85 }

function SpringButton({
  children,
  className,
  disabled,
  onClick,
  type = 'button',
}: {
  children: ReactNode
  className?: string
  disabled?: boolean
  onClick?: () => void
  type?: 'button' | 'submit'
}) {
  const reduce = useReducedMotion()
  return (
    <motion.button
      type={type}
      onClick={onClick}
      disabled={disabled}
      whileHover={reduce || disabled ? undefined : { scale: 1.035, y: -1 }}
      whileTap={reduce || disabled ? undefined : { scale: 0.94 }}
      transition={springBtn}
      className={`touch-manipulation ${className ?? ''}`}
    >
      {children}
    </motion.button>
  )
}

export default function AvatarStudioPage() {
  const router = useRouter()
  const baseUrl = getApiBase()
  const api = useMemo(() => createApiFetch(baseUrl), [baseUrl])
  const reduceMotion = useReducedMotion()

  const [loggedIn, setLoggedIn] = useState(
    () => mcSkipLogin() || (typeof window !== 'undefined' ? !!getMcToken() : false),
  )

  const [prompt, setPrompt] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [age, setAge] = useState(PRESETS.age[1]!)
  const [look, setLook] = useState(PRESETS.look[0]!)
  const [outfit, setOutfit] = useState(PRESETS.outfit[1]!)
  const [personality, setPersonality] = useState(PRESETS.personality[0]!)
  const [voiceTone, setVoiceTone] = useState(PRESETS.voice_tone[0]!)

  const [session, setSession] = useState<GenerateResponse | null>(null)
  const [saved, setSaved] = useState<StudioPersona[]>([])
  const [loadingList, setLoadingList] = useState(true)

  const [busyGen, setBusyGen] = useState(false)
  const [busyVoice, setBusyVoice] = useState(false)
  const [busyLip, setBusyLip] = useState(false)
  const [busySave, setBusySave] = useState(false)

  const [ttsUrl, setTtsUrl] = useState<string | null>(null)
  const [lipUrl, setLipUrl] = useState<string | null>(null)

  const [toast, setToast] = useState<{ type: 'ok' | 'err'; text: string } | null>(null)

  const showToast = useCallback((type: 'ok' | 'err', text: string) => {
    setToast({ type, text })
    window.setTimeout(() => setToast(null), 5000)
  }, [])

  useEffect(() => {
    if (mcSkipLogin()) {
      setLoggedIn(true)
      return
    }
    if (typeof window !== 'undefined' && getMcToken()) {
      setLoggedIn(true)
      return
    }
    // No Bearer token: `/api/v1/modules` would 401 anyway. Avoid a slow/hanging fetch to a dead
    // loopback when the UI service is split from FastAPI (Railway) — send user to sign-in.
    router.replace('/')
  }, [router])

  const loadSaved = useCallback(async () => {
    setLoadingList(true)
    try {
      const r = await api('/api/v1/avatar-studio/personas')
      const j = (await r.json().catch(() => ({}))) as { personas?: StudioPersona[] }
      if (!r.ok) {
        setSaved([])
        showToast('err', 'Could not load saved personas.')
        return
      }
      setSaved(Array.isArray(j.personas) ? j.personas : [])
    } catch {
      setSaved([])
    } finally {
      setLoadingList(false)
    }
  }, [api, showToast])

  useEffect(() => {
    if (!loggedIn) return
    void loadSaved()
  }, [loggedIn, loadSaved])

  const promptEmpty = !prompt.trim()

  const runGenerate = async () => {
    const p = prompt.trim()
    if (!p) {
      showToast('err', 'Please enter a description for your AI influencer.')
      return
    }
    setBusyGen(true)
    setTtsUrl(null)
    setLipUrl(null)
    try {
      const r = await api('/api/v1/avatar-studio/generate', {
        method: 'POST',
        body: JSON.stringify({
          prompt: p,
          name: displayName.trim() || undefined,
          age,
          look,
          outfit,
          personality,
          voice_tone: voiceTone,
        }),
      })
      const data = await r.json().catch(() => ({}))
      if (!r.ok) {
        showToast('err', friendlyError(r, data))
        return
      }
      const out = data as GenerateResponse
      setSession(out)
      showToast('ok', 'Your avatar image is ready.')
      await loadSaved()
    } catch {
      showToast('err', 'Network error — check your connection.')
    } finally {
      setBusyGen(false)
    }
  }

  const runTestVoice = async () => {
    const vid = session?.voice_id
    if (!vid) {
      showToast('err', 'Generate an avatar first to attach a voice.')
      return
    }
    const line =
      'Hey — quick honest take: this is how the voice will sound in your videos.'
    setBusyVoice(true)
    try {
      const r = await api('/api/v1/preview/tts', {
        method: 'POST',
        body: JSON.stringify({ text: line.slice(0, 400), voice_id: vid }),
      })
      const data = await r.json().catch(() => ({}))
      if (!r.ok) {
        showToast('err', friendlyError(r, data))
        return
      }
      const b64 = (data as { audio_base64?: string }).audio_base64
      if (b64) {
        setTtsUrl(`data:audio/mpeg;base64,${b64}`)
        showToast('ok', 'Voice sample ready — press play below.')
      }
    } catch {
      showToast('err', 'Could not play voice preview.')
    } finally {
      setBusyVoice(false)
    }
  }

  const runTestLipsync = async () => {
    const pid = session?.persona_id
    if (!pid) {
      showToast('err', 'Generate an avatar first.')
      return
    }
    setBusyLip(true)
    setLipUrl(null)
    try {
      const r = await api('/api/v1/avatar-studio/test-lipsync', {
        method: 'POST',
        body: JSON.stringify({
          persona_id: pid,
          text: 'Here is a short sample so you can see lip-sync with your new look.',
        }),
      })
      const data = await r.json().catch(() => ({}))
      if (!r.ok) {
        showToast('err', friendlyError(r, data))
        return
      }
      const url = (data as { clip_url?: string }).clip_url
      if (url) {
        setLipUrl(url)
        showToast('ok', 'Lip-sync clip is ready — watch below.')
      }
    } catch {
      showToast('err', 'Lip-sync preview failed.')
    } finally {
      setBusyLip(false)
    }
  }

  const runSavePersona = async () => {
    if (!session) {
      showToast('err', 'Generate your avatar first — then you can save it to the library.')
      return
    }
    setBusySave(true)
    try {
      const r = await api('/api/v1/avatar-studio/save-persona', {
        method: 'POST',
        body: JSON.stringify({ persona_id: session.persona_id }),
      })
      const data = await r.json().catch(() => ({}))
      if (!r.ok) {
        showToast('err', friendlyError(r, data))
        return
      }
      showToast('ok', 'Persona saved to your library.')
      await loadSaved()
    } catch {
      showToast('err', 'Could not save persona — check your connection.')
    } finally {
      setBusySave(false)
    }
  }

  if (!loggedIn && !mcSkipLogin()) {
    return (
      <div className="min-h-[100dvh] min-h-screen flex flex-col items-center justify-center bg-slate-950 px-4 safe-pt safe-pb">
        <Loader2 className="w-10 h-10 text-cyan-400 animate-spin mb-4" />
        <p className="text-slate-400 text-sm mb-6">Checking your session…</p>
        <Link
          href="/"
          className="inline-flex items-center justify-center gap-2 min-h-12 w-full max-w-sm rounded-xl bg-cyan-600 hover:bg-cyan-500 text-white text-sm font-medium px-5 py-3 touch-manipulation"
        >
          <ArrowLeft className="w-4 h-4" />
          Back to Dashboard
        </Link>
      </div>
    )
  }

  const previewSrc = session?.image_url || null
  const previewBusy = busyGen || busyLip

  return (
    <div className="min-h-[100dvh] min-h-screen bg-slate-950 text-slate-100">
      {toast && (
        <div
          className={`fixed z-[100] top-3 left-3 right-3 sm:top-4 sm:left-auto sm:right-4 sm:max-w-md px-4 py-3 rounded-xl border text-sm shadow-xl safe-pt ${
            toast.type === 'ok'
              ? 'bg-emerald-950/95 border-emerald-700 text-emerald-100'
              : 'bg-red-950/95 border-red-800 text-red-100'
          }`}
          role="status"
        >
          {toast.text}
        </div>
      )}

      <header className="border-b border-cyan-500/20 bg-slate-900/80 backdrop-blur-md sticky top-0 z-40 safe-pt">
        <div className="max-w-7xl mx-auto px-3 sm:px-4 xl:px-8 py-3 sm:py-4 flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2 sm:gap-3 min-w-0 flex-1">
            <div className="p-2 rounded-xl bg-gradient-to-br from-cyan-500/20 to-blue-600/10 border border-cyan-500/30 shrink-0">
              <UserCircle2 className="w-6 h-6 sm:w-7 sm:h-7 text-cyan-300" />
            </div>
            <div className="min-w-0">
              <h1 className="text-base sm:text-lg font-semibold text-white tracking-tight truncate">Avatar Studio</h1>
              <p className="text-[10px] sm:text-xs text-cyan-200/70 truncate">AI portraits · voice · lip-sync</p>
            </div>
          </div>
          <Link
            href="/"
            className="inline-flex items-center justify-center gap-2 min-h-12 sm:min-h-0 rounded-xl border border-slate-600 bg-slate-900/80 px-4 py-2.5 sm:py-2 text-sm text-slate-200 hover:border-cyan-500/40 hover:text-cyan-100 transition-colors w-full sm:w-auto touch-manipulation"
          >
            <ArrowLeft className="w-4 h-4 shrink-0" />
            Back to Dashboard
          </Link>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-3 sm:px-4 xl:px-10 2xl:px-12 py-5 sm:py-6 md:py-10 space-y-8 sm:space-y-10 pb-[calc(1.5rem+env(safe-area-inset-bottom))]">
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 sm:gap-8 lg:gap-10 lg:items-start min-h-0">
          {/* Left: prompt + presets */}
          <section className="space-y-4 sm:space-y-5 min-w-0">
            <div className="rounded-2xl border border-cyan-500/25 bg-slate-900/50 p-4 sm:p-5 md:p-6 shadow-lg shadow-cyan-950/20">
              <label className="block text-sm font-medium text-cyan-100/90 mb-2">Describe your avatar</label>
              <textarea
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                placeholder="Example: Confident tech reviewer, subtle smile, soft daylight from a window…"
                rows={6}
                className="w-full rounded-xl border border-slate-600/80 bg-slate-950/70 px-4 py-3 text-base sm:text-sm leading-relaxed text-slate-100 placeholder:text-slate-500 focus:border-cyan-500/50 focus:ring-2 focus:ring-cyan-500/20 outline-none resize-y min-h-[168px] sm:min-h-[140px]"
              />
              <AnimatePresence>
                {promptEmpty ? (
                  <motion.p
                    key="prompt-hint"
                    initial={{ opacity: 0, y: -4 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0, y: -4 }}
                    transition={{ duration: 0.2 }}
                    className="text-xs text-red-400/90 mt-2 leading-relaxed"
                  >
                    Please enter a description for your AI influencer.
                  </motion.p>
                ) : (
                  <motion.p
                    key="prompt-ok"
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    exit={{ opacity: 0 }}
                    className="text-xs text-slate-500 mt-2 leading-relaxed"
                  >
                    Plain language is enough — we combine this with the style options on the right.
                  </motion.p>
                )}
              </AnimatePresence>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 sm:gap-4">
              <Field label="Display name (optional)">
                <input
                  value={displayName}
                  onChange={(e) => setDisplayName(e.target.value)}
                  placeholder="e.g. Tech Reviewer"
                  className="w-full min-h-12 rounded-xl border border-slate-600 bg-slate-950/70 px-3 py-2.5 text-base sm:text-sm focus:border-cyan-500/50 outline-none touch-manipulation"
                />
              </Field>
              <PresetField label="Age" value={age} onChange={setAge} options={PRESETS.age} />
              <PresetField label="Look" value={look} onChange={setLook} options={PRESETS.look} />
              <PresetField label="Outfit" value={outfit} onChange={setOutfit} options={PRESETS.outfit} />
              <PresetField
                label="Personality"
                value={personality}
                onChange={setPersonality}
                options={PRESETS.personality}
              />
              <PresetField
                label="Voice tone"
                value={voiceTone}
                onChange={setVoiceTone}
                options={PRESETS.voice_tone}
              />
            </div>

            <SpringButton
              onClick={() => void runGenerate()}
              disabled={busyGen || promptEmpty}
              className="w-full sm:w-auto inline-flex items-center justify-center gap-2 min-h-12 rounded-xl bg-gradient-to-r from-cyan-600 to-blue-600 hover:from-cyan-500 hover:to-blue-500 disabled:opacity-50 text-white font-semibold text-sm px-8 py-3.5 shadow-lg shadow-cyan-900/30"
            >
              {busyGen ? <Loader2 className="w-5 h-5 animate-spin" /> : <Sparkles className="w-5 h-5" />}
              {busyGen ? 'Creating…' : 'Generate'}
            </SpringButton>
          </section>

          {/* Right: preview + actions — portrait on phones; square on sm+; min size protects foldable inner + cover */}
          <section className="space-y-4 sm:space-y-5 min-w-0 lg:min-w-[min(100%,28rem)]">
            <div className="rounded-2xl border border-cyan-500/25 bg-slate-900/40 overflow-hidden">
              <div className="relative w-full max-w-lg mx-auto aspect-[9/16] max-[430px]:max-h-[min(78dvh,720px)] sm:aspect-square sm:max-h-[min(420px,80vw)] min-h-[240px] bg-gradient-to-b from-slate-800/50 to-slate-950 flex items-center justify-center border-b border-cyan-500/10">
                <AnimatePresence mode="wait">
                  {previewSrc ? (
                    <motion.img
                      key={previewSrc}
                      src={previewSrc}
                      alt="Avatar preview"
                      className="w-full h-full object-cover absolute inset-0"
                      initial={
                        reduceMotion
                          ? { opacity: 0 }
                          : { opacity: 0, scale: 0.9 }
                      }
                      animate={{ opacity: 1, scale: 1 }}
                      exit={
                        reduceMotion
                          ? { opacity: 0 }
                          : { opacity: 0, scale: 0.96 }
                      }
                      transition={
                        reduceMotion
                          ? { duration: 0.2 }
                          : { type: 'spring', stiffness: 280, damping: 26 }
                      }
                    />
                  ) : (
                    <motion.div
                      key="placeholder"
                      className="text-center px-6 py-12"
                      initial={{ opacity: 0 }}
                      animate={{ opacity: 1 }}
                      exit={{ opacity: 0 }}
                      transition={{ duration: 0.2 }}
                    >
                      <ImageIconPlaceholder />
                      <p className="text-slate-500 text-sm mt-4">Preview appears after you generate</p>
                    </motion.div>
                  )}
                </AnimatePresence>

                <AnimatePresence>
                  {previewBusy && (
                    <motion.div
                      key="preview-loading"
                      className="pointer-events-none absolute inset-0 z-10 flex items-center justify-center bg-slate-950/50 backdrop-blur-[1px]"
                      initial={{ opacity: 0 }}
                      animate={{ opacity: 1 }}
                      exit={{ opacity: 0 }}
                      transition={{ duration: 0.25 }}
                    >
                      <div className="relative flex items-center justify-center">
                        {!reduceMotion && (
                          <motion.div
                            className="absolute h-28 w-28 rounded-full border border-cyan-500/25"
                            animate={{ scale: [1, 1.12, 1], opacity: [0.35, 0.6, 0.35] }}
                            transition={{ duration: 2, repeat: Infinity, ease: 'easeInOut' }}
                          />
                        )}
                        <motion.div
                          className="h-11 w-11 rounded-full border-2 border-cyan-400/35 border-t-cyan-300"
                          animate={reduceMotion ? {} : { rotate: 360 }}
                          transition={
                            reduceMotion
                              ? {}
                              : { repeat: Infinity, duration: 1.1, ease: 'linear' }
                          }
                        />
                        <motion.div
                          className="absolute inset-0 rounded-full bg-gradient-to-tr from-cyan-500/10 via-transparent to-blue-500/10"
                          animate={
                            reduceMotion
                              ? { opacity: [0.4, 0.7, 0.4] }
                              : { opacity: [0.3, 0.55, 0.3], rotate: [0, 180, 360] }
                          }
                          transition={{ duration: 2.4, repeat: Infinity, ease: 'easeInOut' }}
                        />
                      </div>
                      <p className="absolute bottom-6 left-0 right-0 text-center text-[11px] uppercase tracking-wider text-cyan-200/70">
                        {busyGen ? 'Generating…' : 'Lip-sync…'}
                      </p>
                    </motion.div>
                  )}
                </AnimatePresence>
              </div>
              <div className="p-3 sm:p-4 md:p-5 grid grid-cols-1 sm:grid-cols-2 gap-2 sm:gap-3">
                <SpringButton
                  onClick={() => void runGenerate()}
                  disabled={busyGen || promptEmpty}
                  className="inline-flex items-center justify-center gap-2 min-h-12 rounded-xl border border-cyan-500/35 bg-cyan-950/30 hover:bg-cyan-900/40 text-cyan-100 text-sm font-medium py-3 disabled:opacity-50 w-full sm:w-auto"
                >
                  <RefreshCw className={`w-4 h-4 ${busyGen ? 'animate-spin' : ''}`} />
                  Regenerate
                </SpringButton>
                <SpringButton
                  onClick={() => void runTestVoice()}
                  disabled={busyVoice || !session}
                  className="inline-flex items-center justify-center gap-2 min-h-12 rounded-xl border border-slate-600 bg-slate-800/50 hover:bg-slate-800 text-slate-100 text-sm font-medium py-3 disabled:opacity-40 w-full sm:w-auto"
                >
                  {busyVoice ? <Loader2 className="w-4 h-4 animate-spin" /> : <Volume2 className="w-4 h-4" />}
                  Test Voice
                </SpringButton>
                <SpringButton
                  onClick={() => void runTestLipsync()}
                  disabled={busyLip || !session}
                  className="inline-flex items-center justify-center gap-2 min-h-12 rounded-xl border border-slate-600 bg-slate-800/50 hover:bg-slate-800 text-slate-100 text-sm font-medium py-3 disabled:opacity-40 w-full sm:w-auto"
                >
                  {busyLip ? <Loader2 className="w-4 h-4 animate-spin" /> : <Mic className="w-4 h-4" />}
                  Test Lip-Sync
                </SpringButton>
                <SpringButton
                  onClick={() => void runSavePersona()}
                  disabled={busySave || !session}
                  className="inline-flex items-center justify-center gap-2 min-h-12 rounded-xl bg-gradient-to-r from-emerald-600 to-cyan-600 hover:from-emerald-500 hover:to-cyan-500 text-white text-sm font-semibold py-3 disabled:opacity-40 w-full sm:min-w-0 sm:w-auto sm:col-span-2"
                >
                  {busySave ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
                  Save as Persona
                </SpringButton>
              </div>
            </div>

            {ttsUrl && (
              <div className="rounded-xl border border-slate-700 bg-slate-900/50 p-4">
                <p className="text-xs text-slate-500 mb-2 flex items-center gap-2">
                  <Volume2 className="w-3.5 h-3.5" /> Voice sample
                </p>
                <audio controls src={ttsUrl} className="w-full h-10" />
              </div>
            )}

            {lipUrl && (
              <div className="rounded-xl border border-cyan-500/20 bg-slate-900/50 p-4">
                <p className="text-xs text-cyan-200/80 mb-2 flex items-center gap-2">
                  <Video className="w-3.5 h-3.5" /> Lip-sync preview
                </p>
                <video src={lipUrl} controls playsInline className="w-full rounded-lg border border-slate-700 max-h-80" />
              </div>
            )}
          </section>
        </div>

        {/* Saved personas strip */}
        <section>
          <div className="flex items-center justify-between gap-3 mb-4">
            <h2 className="text-base font-semibold text-white flex items-center gap-2">
              <Clapperboard className="w-5 h-5 text-cyan-400" />
              Saved personas
            </h2>
            <button
              type="button"
              onClick={() => void loadSaved()}
              className="text-xs text-cyan-400/90 hover:text-cyan-300"
            >
              Refresh
            </button>
          </div>
          {loadingList ? (
            <div className="flex flex-col items-center justify-center py-16 gap-3 text-slate-500 text-sm">
              <Loader2 className="w-8 h-8 text-cyan-400/80 animate-spin" />
              <span>Loading your library…</span>
            </div>
          ) : saved.length === 0 ? (
            <div className="rounded-xl border border-dashed border-slate-700 py-12 text-center text-slate-500 text-sm">
              No saved personas yet — generate one above.
            </div>
          ) : (
            <SavedPersonasRow personas={saved} reduceMotion={!!reduceMotion} onRename={async (pid, name) => {
                await api('/api/v1/avatar-studio/save-persona', { method: 'POST', body: JSON.stringify({ persona_id: pid, name }) })
                await loadSaved()
              }} />
          )}
        </section>
      </main>
    </div>
  )
}

const rowList = {
  hidden: {},
  show: {
    transition: { staggerChildren: 0.07, delayChildren: 0.04 },
  },
}

const rowListReduced = {
  hidden: {},
  show: {
    transition: { staggerChildren: 0, delayChildren: 0 },
  },
}

const rowItem = {
  hidden: { opacity: 0, x: 28, scale: 0.96 },
  show: {
    opacity: 1,
    x: 0,
    scale: 1,
    transition: { type: 'spring' as const, stiffness: 320, damping: 28 },
  },
}

const rowItemReduced = {
  hidden: { opacity: 1, x: 0, scale: 1 },
  show: { opacity: 1, x: 0, scale: 1 },
}

function SavedPersonasRow({
  personas,
  reduceMotion,
  onRename,
}: {
  personas: StudioPersona[]
  reduceMotion: boolean
  onRename: (personaId: string, newName: string) => Promise<void>
}) {
  const outerRef = useRef<HTMLDivElement>(null)
  const innerRef = useRef<HTMLDivElement>(null)
  const [dragCx, setDragCx] = useState({ left: 0, right: 0 })
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editingName, setEditingName] = useState('')
  const [renameBusy, setRenameBusy] = useState(false)

  const startEdit = (p: StudioPersona) => {
    setEditingId(p.persona_id)
    setEditingName(p.name || '')
  }
  const cancelEdit = () => { setEditingId(null); setEditingName('') }
  const commitEdit = async (personaId: string) => {
    if (!editingName.trim()) return cancelEdit()
    setRenameBusy(true)
    try {
      await onRename(personaId, editingName.trim())
      setEditingId(null)
    } finally {
      setRenameBusy(false)
    }
  }

  useLayoutEffect(() => {
    const outer = outerRef.current
    const inner = innerRef.current
    if (!outer || !inner) return
    const measure = () => {
      const max = inner.scrollWidth - outer.clientWidth
      setDragCx({ left: max > 0 ? -max : 0, right: 0 })
    }
    measure()
    const ro = new ResizeObserver(measure)
    ro.observe(outer)
    ro.observe(inner)
    return () => ro.disconnect()
  }, [personas])

  const cardClass =
    'w-[11.25rem] sm:w-44 shrink-0 snap-center overflow-hidden rounded-xl border border-slate-700 bg-slate-900/60 shadow-md shadow-black/20'

  return (
    <div className="relative w-full rounded-xl pb-1">
      {/* Touch / foldables: native horizontal scroll + snap; desktop: Framer drag */}
      <div
        className="md:hidden w-full overflow-x-auto overflow-y-hidden overscroll-x-contain snap-x snap-mandatory scrollbar-touch rounded-xl pb-2 pt-1 -mx-1 px-1"
        style={{ WebkitOverflowScrolling: 'touch' }}
      >
        <div className="flex w-max gap-3 sm:gap-4 pr-2">
          {personas.map((p) => (
            <div key={p.persona_id} className={cardClass}>
              <div className="relative aspect-square bg-slate-800">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={p.image_url} alt="" className="h-full w-full object-cover" />
              </div>
              <div className="space-y-2 p-2">
                {editingId === p.persona_id ? (
                  <div className="flex items-center gap-1">
                    <input
                      autoFocus
                      value={editingName}
                      onChange={(e) => setEditingName(e.target.value)}
                      onKeyDown={(e) => { if (e.key === 'Enter') void commitEdit(p.persona_id); if (e.key === 'Escape') cancelEdit() }}
                      className="min-w-0 flex-1 rounded border border-cyan-500 bg-slate-950 px-1.5 py-0.5 text-xs text-slate-100 outline-none"
                    />
                    <button onClick={() => void commitEdit(p.persona_id)} disabled={renameBusy} className="text-cyan-400 hover:text-cyan-300 disabled:opacity-50"><Check className="w-3.5 h-3.5" /></button>
                    <button onClick={cancelEdit} className="text-slate-500 hover:text-slate-300"><X className="w-3.5 h-3.5" /></button>
                  </div>
                ) : (
                  <div className="flex items-center gap-1 group">
                    <p className="truncate text-xs font-medium text-slate-200 flex-1" title={p.name}>{p.name || 'Persona'}</p>
                    <button onClick={() => startEdit(p)} className="opacity-0 group-hover:opacity-100 text-slate-500 hover:text-slate-300 transition-opacity shrink-0"><Pencil className="w-3 h-3" /></button>
                  </div>
                )}
                <div className="flex flex-col gap-1.5">
                  <Link
                    href={`/avatar?avatar_id=${encodeURIComponent(p.avatar_id)}&persona_id=${encodeURIComponent(p.persona_id)}`}
                    className="flex items-center justify-center min-h-12 w-full rounded-lg bg-cyan-600/90 px-2 text-center text-[11px] font-semibold text-white hover:bg-cyan-500 touch-manipulation"
                  >
                    Video only
                  </Link>
                  <Link
                    href={`/avatar?avatar_id=${encodeURIComponent(p.avatar_id)}&persona_id=${encodeURIComponent(p.persona_id)}&generate_funnel=1`}
                    className="flex items-center justify-center min-h-12 w-full rounded-lg border border-emerald-500/50 bg-emerald-950/40 px-2 text-center text-[11px] font-semibold text-emerald-100 hover:bg-emerald-900/50 touch-manipulation"
                  >
                    Video + Funnel
                  </Link>
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>

      <div ref={outerRef} className="relative hidden md:block w-full overflow-hidden rounded-xl pb-1">
        <motion.div
          ref={innerRef}
          className="flex w-max cursor-grab gap-4 pb-2 pt-1 active:cursor-grabbing"
          drag={reduceMotion ? false : 'x'}
          dragConstraints={dragCx}
          dragElastic={0.07}
          dragTransition={{ bounceStiffness: 320, bounceDamping: 28 }}
          variants={reduceMotion ? rowListReduced : rowList}
          initial="hidden"
          animate="show"
        >
          {personas.map((p) => (
            <motion.div key={p.persona_id} variants={reduceMotion ? rowItemReduced : rowItem} className={cardClass}>
              <div className="relative aspect-square bg-slate-800">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={p.image_url} alt="" className="h-full w-full object-cover" />
              </div>
              <div className="space-y-2 p-2">
                {editingId === p.persona_id ? (
                  <div className="flex items-center gap-1">
                    <input
                      autoFocus
                      value={editingName}
                      onChange={(e) => setEditingName(e.target.value)}
                      onKeyDown={(e) => { if (e.key === 'Enter') void commitEdit(p.persona_id); if (e.key === 'Escape') cancelEdit() }}
                      className="min-w-0 flex-1 rounded border border-cyan-500 bg-slate-950 px-1.5 py-0.5 text-xs text-slate-100 outline-none"
                    />
                    <button onClick={() => void commitEdit(p.persona_id)} disabled={renameBusy} className="text-cyan-400 hover:text-cyan-300 disabled:opacity-50"><Check className="w-3.5 h-3.5" /></button>
                    <button onClick={cancelEdit} className="text-slate-500 hover:text-slate-300"><X className="w-3.5 h-3.5" /></button>
                  </div>
                ) : (
                  <div className="flex items-center gap-1 group">
                    <p className="truncate text-xs font-medium text-slate-200 flex-1" title={p.name}>{p.name || 'Persona'}</p>
                    <button onClick={() => startEdit(p)} className="opacity-0 group-hover:opacity-100 text-slate-500 hover:text-slate-300 transition-opacity shrink-0"><Pencil className="w-3 h-3" /></button>
                  </div>
                )}
                <div className="flex flex-col gap-1.5">
                  <Link
                    href={`/avatar?avatar_id=${encodeURIComponent(p.avatar_id)}&persona_id=${encodeURIComponent(p.persona_id)}`}
                    className="flex items-center justify-center min-h-11 w-full rounded-lg bg-cyan-600/90 py-2 text-center text-[11px] font-semibold text-white hover:bg-cyan-500 touch-manipulation"
                  >
                    Video only
                  </Link>
                  <Link
                    href={`/avatar?avatar_id=${encodeURIComponent(p.avatar_id)}&persona_id=${encodeURIComponent(p.persona_id)}&generate_funnel=1`}
                    className="flex items-center justify-center min-h-11 w-full rounded-lg border border-emerald-500/50 bg-emerald-950/40 py-2 text-center text-[11px] font-semibold text-emerald-100 hover:bg-emerald-900/50 touch-manipulation"
                  >
                    Video + Funnel
                  </Link>
                </div>
              </div>
            </motion.div>
          ))}
        </motion.div>
      </div>
      <p className="mt-2 text-center text-[10px] text-slate-600 md:hidden">
        Swipe sideways — cards snap into place
      </p>
    </div>
  )
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="space-y-1.5">
      <label className="text-xs font-medium text-slate-400">{label}</label>
      {children}
    </div>
  )
}

function PresetField({
  label,
  value,
  onChange,
  options,
}: {
  label: string
  value: string
  onChange: (v: string) => void
  options: string[]
}) {
  return (
    <div className="space-y-2 sm:space-y-1.5 min-w-0">
      <span className="block text-xs font-medium text-slate-400">{label}</span>
      <div className="hidden md:block">
        <select
          value={value}
          onChange={(e) => onChange(e.target.value)}
          className="w-full min-h-12 rounded-xl border border-slate-600 bg-slate-950/70 px-3 py-2.5 text-sm text-slate-100 focus:border-cyan-500/50 outline-none touch-manipulation"
        >
          {options.map((o) => (
            <option key={o} value={o}>
              {o}
            </option>
          ))}
        </select>
      </div>
      <div
        className="md:hidden flex gap-2 overflow-x-auto overscroll-x-contain snap-x snap-mandatory scrollbar-touch pb-1 -mx-0.5 px-0.5"
        role="listbox"
        aria-label={label}
      >
        {options.map((o) => {
          const active = value === o
          return (
            <button
              key={o}
              type="button"
              role="option"
              aria-selected={active}
              onClick={() => onChange(o)}
              className={`shrink-0 snap-start min-h-12 max-w-[min(100%,18rem)] rounded-full border px-3.5 py-2 text-left text-xs font-medium leading-snug transition-colors touch-manipulation ${
                active
                  ? 'border-cyan-500/60 bg-cyan-950/55 text-cyan-50 shadow-sm shadow-cyan-950/40'
                  : 'border-slate-600 bg-slate-950/80 text-slate-300 hover:border-slate-500'
              }`}
            >
              {o}
            </button>
          )
        })}
      </div>
    </div>
  )
}

function ImageIconPlaceholder() {
  return (
    <svg className="mx-auto h-16 w-16 text-slate-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth={1}
        d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z"
      />
    </svg>
  )
}
