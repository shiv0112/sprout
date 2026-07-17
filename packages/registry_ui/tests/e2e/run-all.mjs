// Sequential runner for the Kiln Puppeteer e2e suite.
//
// Spawns each test as a separate child process so a crash in one test
// doesn't take down the whole runner. Streams output live and prints
// a final summary. Exits non-zero on ANY failure.
//
// Run:   node packages/registry_ui/tests/e2e/run-all.mjs
// Or:    cd packages/registry_ui && npm run test:e2e
//
// Each child test handles its own pre-flight health check, so if the
// local stack isn't running every test will fail fast with the same
// "run ./dev.sh" message — that's fine, it's a clear signal.

import { spawn } from 'node:child_process'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = dirname(fileURLToPath(import.meta.url))

const TESTS = [
  'health-checks.mjs',
  'homepage.mjs',
  'tools-list.mjs',
  'tool-detail.mjs',
  'chat-flow.mjs',
]

function runOne(file) {
  return new Promise((resolve) => {
    const start = Date.now()
    const child = spawn('node', [join(__dirname, file)], {
      stdio: 'inherit',
      env: process.env,
    })
    child.on('exit', (code) => {
      resolve({ file, code: code ?? 1, ms: Date.now() - start })
    })
    child.on('error', (err) => {
      console.error(`spawn failed for ${file}: ${err.message}`)
      resolve({ file, code: 1, ms: Date.now() - start })
    })
  })
}

const results = []
for (const file of TESTS) {
  console.log(`\n━━━ ${file} ━━━`)
  const result = await runOne(file)
  results.push(result)
}

const passed = results.filter((r) => r.code === 0)
const failed = results.filter((r) => r.code !== 0)

console.log('\n━━━ Summary ━━━')
for (const r of results) {
  const mark = r.code === 0 ? 'PASS' : 'FAIL'
  console.log(`  ${mark}  ${r.file}  (${r.ms}ms)`)
}
console.log(`\n  ${passed.length} passed, ${failed.length} failed of ${results.length} total\n`)

process.exit(failed.length === 0 ? 0 : 1)
