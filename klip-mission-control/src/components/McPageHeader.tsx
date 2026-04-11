import Link from 'next/link'
import { ChevronLeft } from 'lucide-react'

export function McPageHeader({ title, subtitle }: { title: string; subtitle?: string }) {
  return (
    <div className="mb-8">
      <Link
        href="/"
        className="text-sm text-slate-400 hover:text-klipaura-300 flex items-center gap-1 mb-3"
      >
        <ChevronLeft className="w-4 h-4" /> Mission Control
      </Link>
      <h1 className="text-2xl font-bold text-slate-100">{title}</h1>
      {subtitle ? <p className="text-slate-500 text-sm mt-1">{subtitle}</p> : null}
    </div>
  )
}
