// E2E: navigate directly to a tool detail page and verify the SSR
// fetch (registry_api GET /tools/{id}) renders into the expected
// hero — name in h1, version badge, description paragraph.
//
// Uses com.kiln.tools.current_date because that's the same fixture
// the Python test_loader and test_main suites pin.
//
// Run:   node packages/registry_ui/tests/e2e/tool-detail.mjs

import { preflightHealthChecks, runTest } from './_lib.mjs'

await preflightHealthChecks(['registry_ui', 'registry_api'])

const TOOL_ID = 'com.kiln.tools.current_date'
const URL = `http://localhost:3001/tools/${TOOL_ID}`

const ok = await runTest('tool-detail', async ({ page }) => {
  const response = await page.goto(URL, {
    waitUntil: 'networkidle2',
    timeout: 30_000,
  })

  if (!response || !response.ok()) {
    throw new Error(
      `${URL} returned ${response ? response.status() : 'no response'}. ` +
      `If 404, registry_api does not have ${TOOL_ID} loaded — check /tools.`
    )
  }

  // Wait for the h1 to render. The page is a server component so this
  // should be there on first paint, but networkidle2 covers the case
  // where any client component bumps in afterwards.
  await page.waitForSelector('h1', { timeout: 10_000 })

  // Read the rendered hero contents in a single browser-side pass.
  const hero = await page.evaluate(() => {
    const h1 = document.querySelector('h1')
    return {
      h1Text: h1 ? h1.textContent?.trim() : '',
      bodyText: document.body.innerText,
    }
  })

  // The h1 must show the tool's short name (current_date).
  if (!hero.h1Text || !hero.h1Text.includes('current_date')) {
    throw new Error(
      `Expected h1 to contain "current_date", got "${hero.h1Text}"`
    )
  }

  // The version badge should render "v1.0.0" — pin the registry's
  // current version of this fixture so a regression in versioning
  // is loud.
  if (!hero.bodyText.includes('v1.0.0')) {
    throw new Error('Expected page body to contain "v1.0.0" version badge')
  }

  // The spec.yaml description must round-trip into the rendered page.
  // The current_date spec says "Fetch the current date and time in a
  // specified format." — assert at least the distinctive phrase.
  if (!/current date and time/i.test(hero.bodyText)) {
    throw new Error(
      'Expected page body to contain the spec.yaml description ' +
      '("current date and time"). The SSR fetch may not be hitting registry_api.'
    )
  }
})

process.exit(ok ? 0 : 1)
