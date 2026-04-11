import { NextResponse } from 'next/server'

export async function GET() {
  return NextResponse.json({
    status: 'ok',
    service: 'klipaura-frontend',
    sha: process.env.NEXT_PUBLIC_GIT_SHA || 'unknown',
    timestamp: new Date().toISOString(),
  })
}
