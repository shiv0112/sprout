// E2E: navigate to the registry_ui homepage, assert it loads with the
// right title and no JS console errors.
//
// Run:   node packages/registry_ui/tests/e2e/homepage.mjs
// Or:    npm run test:e2e   (from packages/registry_ui)

import { preflightHealthChecks, runTest } from './_lib.mjs'

await preflightHealthChecks(['registry_ui'])

const ok = await runTest('homepage', async ({ page, consoleErrors }) => {
  const response = await page.goto('http://localhost:3001', {
    waitUntil: 'networkidle2',
    timeout: 30_000,
  })

  if (!response || !response.ok()) {
    throw new Error(`http://localhost:3001 returned ${response ? response.status() : 'no response'}`)
  }

  const title = await page.title()
  if (!/kiln/i.test(title)) {
    throw new Error(`Expected page title to contain "Kiln", got "${title}"`)
  }

  // Page-level rendering check: there should be a visible body element
  // with non-empty text content. Catches the case where the page loads
  // but JS crashes before React hydrates.
  const bodyText = await page.evaluate(() => document.body.innerText.trim())
  if (bodyText.length === 0) {
    throw new Error('Page body is empty after networkidle2 — React likely failed to render')
  }

  // Console errors collected by runTest get failed on as well, but only
  // for non-noisy ones — Clerk warnings about test mode are expected.
  const realErrors = consoleErrors.filter(
    (e) => !/clerk/i.test(e) && !/favicon/i.test(e) && !/Failed to load resource/i.test(e),
  )
  if (realErrors.length > 0) {
    throw new Error(`Page console errors:\n  ${realErrors.join('\n  ')}`)
  }
})

process.exit(ok ? 0 : 1)
