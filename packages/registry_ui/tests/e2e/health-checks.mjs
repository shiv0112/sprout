// E2E: verify the local Sprout services expose healthy HTTP probes.
//
// This keeps the suite honest before we spend time in browser tests, and it
// mirrors the same service set shown in the registry UI system-status panel.

const CHECKS = [
  { name: 'registry-api', url: 'http://localhost:8766/readyz', required: true },
  { name: 'chat-backend', url: 'http://localhost:8765/readyz', required: true },
  { name: 'mcp-server', url: 'http://localhost:8768/readyz', required: true },
  { name: 'tool-executor', url: 'http://localhost:8767/health', required: true },
  { name: 'synthesis-service', url: 'http://localhost:8002/readyz', required: false },
  { name: 'registry-ui', url: 'http://localhost:3001', required: true },
]

let failed = false

for (const check of CHECKS) {
  try {
    const res = await fetch(check.url, { signal: AbortSignal.timeout(3_000) })
    if (!res.ok) {
      if (check.required) {
        console.error(`FAIL  ${check.name}  (${check.url} returned HTTP ${res.status})`)
        failed = true
      } else {
        console.log(`WARN  ${check.name}  (${check.url} returned HTTP ${res.status}; optional)`)
      }
      continue
    }

    console.log(`PASS  ${check.name}`)
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err)
    if (check.required) {
      console.error(`FAIL  ${check.name}  (${message})`)
      failed = true
    } else {
      console.log(`WARN  ${check.name}  (${message}; optional)`)
    }
  }
}

process.exit(failed ? 1 : 0)
