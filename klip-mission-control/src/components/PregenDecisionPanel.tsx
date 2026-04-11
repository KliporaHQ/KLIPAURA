'use client'

import { useCallback, useEffect, useState } from 'react'

type ApiFetch = (path: string, init?: RequestInit) => Promise<Response>

interface DecisionConfig {
  auto_approve_threshold: number
  manual_review_threshold: number
  pregen_hitl_required: boolean
  remaining_budget: number
}

interface DecisionRecord {
  id: string
  created_at: string
  status: string
  route: string
  avatar_id: string
  product_url: string
  title: string
  category: string
  final_score: number
  component_scores: Record<string, number>
  hard_gates: Record<string, { blocked: boolean; reason: string }>
  explainability: Record<string, unknown>
}

export function PregenDecisionPanel({
  apiFetch,
  onSuccess,
  onError,
}: {
  apiFetch: ApiFetch
  onSuccess: (message: string) => void
  onError: (message: string) => void
}) {
  const [globalConfig, setGlobalConfig] = useState<DecisionConfig | null>(null)
  const [queue, setQueue] = useState<DecisionRecord[]>([])
  const [busyId, setBusyId] = useState<string | null>(null)

  const load = useCallback(async () => {
    const [cfgRes, queueRes] = await Promise.all([
      apiFetch('/api/v1/decisions/config/global'),
      apiFetch('/api/v1/decisions/queue'),
    ])
    if (cfgRes.ok) setGlobalConfig(await cfgRes.json())
    if (queueRes.ok) setQueue(await queueRes.json())
  }, [apiFetch])

  useEffect(() => {
    load().catch(() => void 0)
  }, [load])

  const saveGlobal = async () => {
    if (!globalConfig) return
    const r = await apiFetch('/api/v1/decisions/config/global', {
      method: 'PUT',
      body: JSON.stringify(globalConfig),
    })
    if (!r.ok) {
      onError(await r.text())
      return
    }
    onSuccess('Decision global config updated.')
  }

  const handleAction = async (id: string, action: 'approve' | 'reject') => {
    setBusyId(id)
    try {
      const note = window.prompt(`${action === 'approve' ? 'Approve' : 'Reject'} note (optional):`) ?? ''
      const r = await apiFetch(`/api/v1/decisions/${encodeURIComponent(id)}/${action}`, {
        method: 'POST',
        body: JSON.stringify({ note }),
      })
      if (!r.ok) {
        onError(await r.text())
        return
      }
      onSuccess(`Decision ${action}d.`)
      await load()
    } finally {
      setBusyId(null)
    }
  }

  return (
    <div className="space-y-4">
      <div className="mc-panel p-4">
        <h3 className="text-slate-100 font-semibold mb-3">Pregen Decision Policy (Global)</h3>
        {globalConfig ? (
          <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
            <label className="text-xs text-slate-400">
              Auto approve threshold
              <input
                type="number"
                min={0}
                max={1}
                step={0.01}
                value={globalConfig.auto_approve_threshold}
                onChange={(e) =>
                  setGlobalConfig((p) => (p ? { ...p, auto_approve_threshold: Number(e.target.value) } : p))
                }
                className="mt-1 w-full px-2 py-1.5 rounded bg-slate-950 border border-slate-700 text-slate-100"
              />
            </label>
            <label className="text-xs text-slate-400">
              Manual review threshold
              <input
                type="number"
                min={0}
                max={1}
                step={0.01}
                value={globalConfig.manual_review_threshold}
                onChange={(e) =>
                  setGlobalConfig((p) => (p ? { ...p, manual_review_threshold: Number(e.target.value) } : p))
                }
                className="mt-1 w-full px-2 py-1.5 rounded bg-slate-950 border border-slate-700 text-slate-100"
              />
            </label>
            <label className="text-xs text-slate-400">
              Remaining budget
              <input
                type="number"
                min={0}
                step={0.01}
                value={globalConfig.remaining_budget}
                onChange={(e) =>
                  setGlobalConfig((p) => (p ? { ...p, remaining_budget: Number(e.target.value) } : p))
                }
                className="mt-1 w-full px-2 py-1.5 rounded bg-slate-950 border border-slate-700 text-slate-100"
              />
            </label>
            <label className="text-xs text-slate-400 flex items-center gap-2 mt-5">
              <input
                type="checkbox"
                checked={globalConfig.pregen_hitl_required}
                onChange={(e) =>
                  setGlobalConfig((p) => (p ? { ...p, pregen_hitl_required: e.target.checked } : p))
                }
              />
              PREGEN_HITL_REQUIRED
            </label>
          </div>
        ) : (
          <p className="text-slate-500 text-sm">Loading config…</p>
        )}
        <button
          type="button"
          onClick={() => saveGlobal().catch((e) => onError(e instanceof Error ? e.message : 'Save failed'))}
          className="mt-3 px-3 py-1.5 rounded bg-klipaura-600 text-white text-sm hover:bg-klipaura-500"
        >
          Save global config
        </button>
      </div>

      <div className="mc-panel p-4">
        <h3 className="text-slate-100 font-semibold mb-3">Decision Queue</h3>
        {queue.length === 0 ? (
          <p className="text-slate-500 text-sm">No pending decisions.</p>
        ) : (
          <div className="space-y-3">
            {queue.map((d) => (
              <div key={d.id} className="border border-slate-800 rounded-lg p-3 bg-slate-900/40">
                <div className="flex items-center justify-between gap-2">
                  <p className="text-slate-200 text-sm font-medium truncate">
                    {d.title || 'Untitled candidate'} · {d.route} · {d.final_score.toFixed(2)}
                  </p>
                  <div className="flex items-center gap-2">
                    <button
                      type="button"
                      disabled={busyId === d.id}
                      onClick={() => handleAction(d.id, 'approve').catch(() => void 0)}
                      className="px-2 py-1 rounded bg-emerald-700 text-white text-xs disabled:opacity-50"
                    >
                      Approve
                    </button>
                    <button
                      type="button"
                      disabled={busyId === d.id}
                      onClick={() => handleAction(d.id, 'reject').catch(() => void 0)}
                      className="px-2 py-1 rounded bg-red-700 text-white text-xs disabled:opacity-50"
                    >
                      Reject
                    </button>
                  </div>
                </div>
                <p className="text-xs text-slate-500 mt-1 truncate">{d.product_url}</p>
                <div className="mt-2 text-xs text-slate-400 grid grid-cols-1 md:grid-cols-2 gap-1">
                  <p>Avatar: {d.avatar_id}</p>
                  <p>Category: {d.category || 'unknown'}</p>
                  <p>
                    Scores: c={d.component_scores.commission_score ?? 0}, t={d.component_scores.trend_score ?? 0},
                    b={d.component_scores.budget_score ?? 0}
                  </p>
                  <p>
                    Gates blocked:{' '}
                    {Object.values(d.hard_gates || {})
                      .filter((g) => g.blocked)
                      .length || 0}
                  </p>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
