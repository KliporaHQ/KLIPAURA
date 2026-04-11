/**
 * One-off: open Mission Control, submit AGENTS.md default credentials, print outcome.
 * Run: node scripts/browser-login-smoke.mjs [baseUrl]
 */
import { chromium } from 'playwright'

const base =
  process.argv[2] || 'https://klip-api-production.up.railway.app'
const url = base.replace(/\/$/, '') + '/'

const USER = 'klipaura2026'
const PASS = 'Klipaura123'

const browser = await chromium.launch({ headless: true })
const page = await browser.newPage()

const requests = []
page.on('request', (req) => {
  if (req.url().includes('auth/login')) {
    requests.push({ method: req.method(), url: req.url() })
  }
})

await page.goto(url, { waitUntil: 'networkidle', timeout: 90_000 })
await page.waitForSelector('input[placeholder="Username"]', { state: 'visible' })

await page.getByPlaceholder('Username').fill(USER)
await page.getByPlaceholder('Password').fill(PASS)

const loginRespPromise = page.waitForResponse(
  (r) =>
    r.url().includes('/api/v1/auth/login') &&
    r.request().method() === 'POST',
  { timeout: 90_000 },
)

await page.locator('form').evaluate((f) => f.requestSubmit())

let loginResp
try {
  loginResp = await loginRespPromise
} catch (e) {
  const body = await page.locator('body').innerText().catch(() => '')
  console.log(
    JSON.stringify(
      {
        target: url,
        error: String(e.message || e),
        capturedAuthRequests: requests,
        bodyPreview: body.slice(0, 600).replace(/\s+/g, ' ').trim(),
      },
      null,
      2,
    ),
  )
  await browser.close()
  process.exit(1)
}

let loginJson = null
try {
  loginJson = await loginResp.json()
} catch {
  loginJson = { raw: await loginResp.text().catch(() => '') }
}

await page.waitForTimeout(2_000)

const body = await page.locator('body').innerText()
const stillLogin = body.includes('operator sign-in')
const errLine =
  body
    .split('\n')
    .find(
      (l) =>
        l.includes('Credentials') === false &&
        /invalid|unreachable|503|401|502|misconfigured|network/i.test(l),
    ) || ''

console.log(
  JSON.stringify(
    {
      target: url,
      user: USER,
      loginApiStatus: loginResp.status(),
      loginApiBody: loginJson,
      stillOnLoginForm: stillLogin,
      errorHint: errLine.slice(0, 200),
      bodyPreview: body.slice(0, 400).replace(/\s+/g, ' ').trim(),
    },
    null,
    2,
  ),
)

await browser.close()
process.exit(stillLogin ? 1 : 0)
