'use client'

export type ProviderAggregate = {
  amount_usd: number
  events: number
}

export function CreditsProviderTable({
  providers,
}: {
  providers: Record<string, ProviderAggregate>
}) {
  const rows = Object.entries(providers || {}).sort((a, b) => b[1].amount_usd - a[1].amount_usd)

  return (
    <div className="overflow-x-auto rounded-xl border border-slate-800 bg-slate-900/40">
      <table className="w-full min-w-[420px] text-sm">
        <thead>
          <tr className="border-b border-slate-800 text-left text-[10px] uppercase tracking-wider text-slate-500">
            <th className="px-4 py-3 font-semibold">Provider</th>
            <th className="px-4 py-3 font-semibold">Events</th>
            <th className="px-4 py-3 font-semibold text-right">Spend (USD)</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-800/80">
          {rows.map(([name, agg]) => (
            <tr key={name} className="hover:bg-slate-800/25">
              <td className="px-4 py-2.5 font-mono text-slate-200">{name}</td>
              <td className="px-4 py-2.5 text-slate-400 tabular-nums">{agg.events}</td>
              <td className="px-4 py-2.5 text-right font-mono text-klipaura-200/95 tabular-nums">
                {agg.amount_usd.toFixed(4)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length === 0 && (
        <div className="px-4 py-10 text-center text-slate-500 text-sm">No provider usage recorded yet</div>
      )}
    </div>
  )
}
