// Shared helpers for Puppeteer e2e tests.
//
// Tests are plain ESM Node scripts (run with `node tests/e2e/<name>.mjs`)
// rather than a heavyweight test runner. Keep this file dependency-free
// beyond the puppeteer + node:fs + node:path stdlib.

import { mkdir, writeFile } from 'node:fs/promises'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = dirname(fileURLToPath(import.meta.url))
export const SCREENSHOT_DIR = join(__dirname, 'screenshots')

export const SERVICES = {
  registry_api: { url: 'http://localhost:8766/health', name: 'kiln-registry-api' },
  chat_backend: { url: 'http://localhost:8765/health', name: 'kiln-chat-backend' },
  registry_ui: { url: 'http://localhost:3001', name: 'registry-ui' },
}

/**
 * Hit each service's /health (or root) and fail fast with a clear message
 * if any aren't running. Returns silently if everything is up.
 */
export async function preflightHealthChecks(required = ['registry_api', 'registry_ui']) {
  const failures = []
  for (const key of required) {
    const svc = SERVICES[key]
    try {
      const res = await fetch(svc.url, { signal: AbortSignal.timeout(2000) })
      if (!res.ok) {
        failures.push(`  - ${svc.name} (${svc.url}) returned HTTP ${res.status}`)
      }
    } catch (err) {
      failures.push(`  - ${svc.name} (${svc.url}) is not reachable: ${err.message}`)
    }
  }
  if (failures.length > 0) {
    console.error('\n❌ Pre-flight health checks failed:')
    for (const f of failures) console.error(f)
    console.error(
      '\nStart the local dev stack first:\n' +
      '  ./dev.sh   (from the repo root)\n'
    )
    process.exit(2)
  }
}

/**
 * Capture a screenshot to ``tests/e2e/screenshots/<name>-<timestamp>.png``
 * and return the absolute path. Used by failure handlers.
 */
export async function captureScreenshot(page, name) {
  await mkdir(SCREENSHOT_DIR, { recursive: true })
  const ts = new Date().toISOString().replace(/[:.]/g, '-')
  const path = join(SCREENSHOT_DIR, `${name}-${ts}.png`)
  await page.screenshot({ path, fullPage: true })
  return path
}

/**
 * Run a single test. Catches errors, captures a screenshot, prints
 * a one-line PASS/FAIL summary, exits non-zero on failure.
 *
 * Usage:
 *   import { runTest } from './_lib.mjs'
 *   runTest('homepage', async ({ browser, page }) => { ... })
 */
export async function runTest(name, fn) {
  const { default: puppeteer } = await import('puppeteer')
  const headless = process.env.HEADED ? false : 'new'
  const browser = await puppeteer.launch({ headless, args: ['--no-sandbox'] })
  const page = await browser.newPage()

  const consoleErrors = []
  page.on('console', (msg) => {
    if (msg.type() === 'error') consoleErrors.push(msg.text())
  })
  page.on('pageerror', (err) => consoleErrors.push(`pageerror: ${err.message}`))

  const start = Date.now()
  try {
    await fn({ browser, page, consoleErrors })
    const ms = Date.now() - start
    console.log(`PASS  ${name}  (${ms}ms)`)
    await browser.close()
    return true
  } catch (err) {
    const ms = Date.now() - start
    let shotPath = ''
    try {
      shotPath = await captureScreenshot(page, name)
    } catch (shotErr) {
      shotPath = `(screenshot failed: ${shotErr.message})`
    }
    console.error(`FAIL  ${name}  (${ms}ms)`)
    console.error(`  ${err.stack || err.message}`)
    console.error(`  screenshot: ${shotPath}`)
    if (consoleErrors.length > 0) {
      console.error(`  page console errors:`)
      for (const e of consoleErrors) console.error(`    ${e}`)
    }
    await browser.close()
    return false
  }
}
