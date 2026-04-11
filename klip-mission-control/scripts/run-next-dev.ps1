# Next.js dev with build/cache on E: (set NEXT_DIST_DIR). Pair with run-api-local.ps1 + dev:stack.
$ErrorActionPreference = "Stop"
$McDir = Split-Path $PSScriptRoot -Parent
Set-Location $McDir

function Get-KlipProjectData {
  $b = [Environment]::GetEnvironmentVariable("KLIP_PROJECT_DATA", "Process")
  if ([string]::IsNullOrWhiteSpace($b)) { $b = [Environment]::GetEnvironmentVariable("KLIP_PROJECT_DATA", "User") }
  if ([string]::IsNullOrWhiteSpace($b)) { $b = "E:\ProjectData\KLIPAURA" }
  return $b
}

$Base = Get-KlipProjectData
# Optional: keep a folder on E: for future artifacts (not used as NEXT_DIST_DIR — see below).
$dist = Join-Path $Base "next-mc-build"
New-Item -ItemType Directory -Force -Path $dist | Out-Null

# Do NOT set NEXT_DIST_DIR to an absolute path on Windows: Next 14.1 dev bundler can
# incorrectly join cwd + distDir and try to mkdir "...\project\E:\Data\..." (ENOENT).
# The repo already lives on E:, so default `.next` stays on E:.
Write-Host "[mc-next] Using default distDir .next (repo on E:). ProjectData stub: $dist" -ForegroundColor DarkGray
npx --yes next dev
