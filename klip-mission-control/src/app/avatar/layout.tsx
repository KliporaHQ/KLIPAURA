import type { Metadata } from 'next'

export const metadata: Metadata = {
  title: 'Avatar Studio · KLIPAURA',
  description: 'Klip-avatar production control — UGC pipeline, jobs, and activity',
}

export default function AvatarLayout({ children }: { children: React.ReactNode }) {
  return children
}
