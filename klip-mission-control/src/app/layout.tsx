import type { Metadata, Viewport } from 'next'
import './globals.css'

export const metadata: Metadata = {
  title: 'KLIPAURA Mission Control',
  description: 'Autonomous Venture Operating System - Master Dashboard',
  icons: {
    icon: '/favicon.ico',
  },
  appleWebApp: {
    capable: true,
    statusBarStyle: 'black-translucent',
  },
}

/** Mobile + foldables: stable scale; allow pinch-zoom for accessibility; notch / hinge safe-areas via CSS */
export const viewport: Viewport = {
  width: 'device-width',
  initialScale: 1,
  maximumScale: 5,
  viewportFit: 'cover',
  themeColor: '#0f172a',
}

export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <html lang="en" className="h-full">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
        <link
          href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap"
          rel="stylesheet"
        />
      </head>
      <body
        className="antialiased text-slate-100 min-h-[100dvh] min-h-screen touch-manipulation"
        style={{ fontFamily: 'Inter, system-ui, sans-serif' }}
      >
        {children}
      </body>
    </html>
  )
}
