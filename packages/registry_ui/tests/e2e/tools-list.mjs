// E2E: navigate to the catalog page (homepage), wait for tool cards
// to render, assert at least one is visible. Validates that the SSR
// fetch from registry_api → CatalogClient hydration round-trip works.
//
// Run:   node packages/registry_ui/tests/e2e/tools-list.mjs

import { preflightHealthChecks, runTest } from './_lib.mjs'

await preflightHealthChecks(['registry_ui', 'registry_api'])

const ok = await runTest('tools-list', async ({ page }) => {
  await page.goto('http://localhost:3001', {
    waitUntil: 'networkidle2',
    timeout: 30_000,
  })

  // Tool cards on the catalog page are `<Link href="/tools/<id>">`.
  // Wait for at least one to appear in the DOM.
  await page.waitForSelector('a[href^="/tools/"]', { timeout: 10_000 })

  // Read the href attributes of all tool links via the page context.
  const toolLinks = await page.evaluate(() => {
    const links = Array.from(document.querySelectorAll('a[href^="/tools/"]'))
    return links
      .map((el) => el.getAttribute('href'))
      .filter((href) => href !== null)
  })

  if (toolLinks.length === 0) {
    throw new Error('Catalog page rendered 0 tool cards — registry_api returned an empty list?')
  }

  // The registry has 40+ tools loaded; if we only see 1-2, something is
  // wrong (filter stuck, pagination broken, etc.). Sanity-check the count.
  if (toolLinks.length < 5) {
    throw new Error(
      `Catalog page rendered only ${toolLinks.length} tool cards (expected 5+ from registry_api). ` +
      `Got: ${toolLinks.join(', ')}`
    )
  }

  // Verify at least one of the canonical tools is present. current_date
  // is the same fixture the Python test_loader test uses.
  const hasCurrentDate = toolLinks.some((href) => href.includes('current_date'))
  if (!hasCurrentDate) {
    console.warn(
      `  ⚠ current_date tool not in catalog (got ${toolLinks.length} tools). ` +
      `That's unusual but not fatal — the catalog renders whatever the registry returns.`
    )
  }
})

process.exit(ok ? 0 : 1)
