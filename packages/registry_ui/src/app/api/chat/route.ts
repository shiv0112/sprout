/**
 * /api/chat — Bridge between AI SDK v6 chat and Sprout's backend.
 *
 * AI SDK sends: { messages: [{ parts: [...], role: "user" }] }
 * We extract the last user message, call /sprout/start, connect to the SSE stream,
 * and convert Sprout events to AI SDK UI Message Stream Protocol (text-start/text-delta/text-end).
 */

const CHAT_BACKEND = process.env.CHAT_BACKEND_INTERNAL || process.env.NEXT_PUBLIC_CHAT_BACKEND || "http://localhost:8765"

const SSE_HEADERS = {
  "Content-Type": "text/event-stream",
  "Cache-Control": "no-cache",
  "x-vercel-ai-ui-message-stream": "v1",
}

export async function POST(req: Request) {
  const { messages } = await req.json()

  // Extract last user message (handles both AI SDK v6 `parts` and legacy `content` format)
  const lastUser = [...messages].reverse().find((m: { role: string }) => m.role === "user")
  let query = ""
  if (typeof lastUser?.content === "string") {
    query = lastUser.content
  } else if (Array.isArray(lastUser?.content)) {
    query = lastUser.content.find((p: { type: string; text?: string }) => p.type === "text")?.text || ""
  } else if (Array.isArray(lastUser?.parts)) {
    query = lastUser.parts.find((p: { type: string; text?: string }) => p.type === "text")?.text || ""
  }

  if (!query.trim()) {
    return streamSimpleText("Please provide a query.")
  }

  // Build conversation history from last 10 messages for context
  const history = buildHistory(messages)

  // Forward auth header
  const authHeader = req.headers.get("authorization")
  const headers: Record<string, string> = { "Content-Type": "application/json" }
  if (authHeader) headers["Authorization"] = authHeader

  try {
    // Call Sprout backend with current query + conversation history
    const res = await fetch(`${CHAT_BACKEND}/sprout/start`, {
      method: "POST",
      headers,
      body: JSON.stringify({ request: query, history }),
    })

    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: `Error ${res.status}` }))
      const msg = typeof err.detail === "string" ? err.detail : JSON.stringify(err.detail)
      return streamSimpleText(`Error: ${msg}`)
    }

    const data = await res.json()

    if (data.status === "needs_config") {
      const payload = JSON.stringify({
        type: "needs_config",
        run_id: data.run_id,
        missing_envs: data.missing_envs,
      })
      return streamSimpleText(`__SPROUT_CONFIG__${payload}`)
    }

    // Stream execution events
    return streamSproutExecution(data.run_id)
  } catch (err) {
    return streamSimpleText(`Error: ${err}`)
  }
}

/** Stream a simple text response using AI SDK UI Message Stream Protocol */
function streamSimpleText(text: string): Response {
  const encoder = new TextEncoder()
  const msgId = `msg_${Date.now()}`
  const stream = new ReadableStream({
    start(controller) {
      controller.enqueue(encoder.encode(`data: ${JSON.stringify({ type: "text-start", id: msgId })}\n\n`))
      controller.enqueue(encoder.encode(`data: ${JSON.stringify({ type: "text-delta", id: msgId, delta: text })}\n\n`))
      controller.enqueue(encoder.encode(`data: ${JSON.stringify({ type: "text-end", id: msgId })}\n\n`))
      controller.enqueue(encoder.encode(`data: ${JSON.stringify({ type: "finish" })}\n\n`))
      controller.enqueue(encoder.encode("data: [DONE]\n\n"))
      controller.close()
    },
  })
  return new Response(stream, { headers: SSE_HEADERS })
}

/** Connect to Sprout's SSE stream and convert to AI SDK UI Message Stream Protocol.
 *
 * Key UX decision: we do NOT stream the noisy intermediate events
 * (`Plan: ...`, `node_x starting`, `tool_call`, etc) as markdown text into
 * the assistant message. Doing so makes the chat look like a debug log.
 *
 * Instead we buffer all intermediate events into a structured "trace" array
 * and emit them as a single hidden ``__SPROUT_TRACE__{...}__END__`` prefix on
 * the final delta. The chat page picks that prefix off and renders it as a
 * collapsible "View execution trace" disclosure under the answer. Default
 * view is just the answer — clean and conversational.
 */
type SproutTraceEvent =
  | { kind: "plan"; nodes: { id: string; role: string; tools: string[] }[] }
  | { kind: "synthesis_wait"; tool_ids: string[] }
  | { kind: "tool_ready"; tool_id: string }
  | { kind: "node_start"; node_id: string; role?: string }
  | { kind: "tool_call"; tool: string; node_id?: string }
  | { kind: "tool_result"; node_id?: string }
  | { kind: "node_complete"; node_id: string }
  | { kind: "synthesis_timeout"; missing: string[] }

function streamSproutExecution(runId: string): Response {
  const encoder = new TextEncoder()
  const msgId = `msg_${runId}`

  const stream = new ReadableStream({
    async start(controller) {
      let closed = false
      let started = false
      const trace: SproutTraceEvent[] = []

      function ensureStarted() {
        if (!started) {
          started = true
          controller.enqueue(encoder.encode(`data: ${JSON.stringify({ type: "text-start", id: msgId })}\n\n`))
        }
      }

      function finishWithAnswer(answer: string) {
        if (closed) return
        closed = true
        ensureStarted()
        // Trace prefix is invisible to the default rendering — the chat
        // page strips it off and renders it as a separate disclosure.
        const tracePrefix =
          trace.length > 0
            ? `__SPROUT_TRACE__${JSON.stringify(trace)}__END__`
            : ""
        const payload = tracePrefix + answer
        controller.enqueue(encoder.encode(`data: ${JSON.stringify({ type: "text-delta", id: msgId, delta: payload })}\n\n`))
        controller.enqueue(encoder.encode(`data: ${JSON.stringify({ type: "text-end", id: msgId })}\n\n`))
        controller.enqueue(encoder.encode(`data: ${JSON.stringify({ type: "finish" })}\n\n`))
        controller.enqueue(encoder.encode("data: [DONE]\n\n"))
        controller.close()
      }

      function finishWithError(message: string) {
        finishWithAnswer(`I hit an error: ${message}`)
      }

      // Connect to Sprout SSE
      const sseRes = await fetch(`${CHAT_BACKEND}/sprout/stream/${runId}`)
      if (!sseRes.ok || !sseRes.body) {
        finishWithError("Could not connect to the execution stream.")
        return
      }

      const reader = sseRes.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ""

      // Keep-alive: send SSE comment every 30s to prevent proxy/connection timeout
      const keepAlive = setInterval(() => {
        if (!closed) {
          controller.enqueue(encoder.encode(": keepalive\n\n"))
        }
      }, 30_000)

      try {
        while (true) {
          const { done, value } = await reader.read()
          if (done) break

          buffer += decoder.decode(value, { stream: true })
          const lines = buffer.split("\n")
          buffer = lines.pop() || ""

          for (const line of lines) {
            if (!line.startsWith("data: ")) continue
            const raw = line.slice(6).trim()
            if (!raw || raw === "[DONE]") continue

            try {
              const ev = JSON.parse(raw)

              switch (ev.type) {
                case "plan_ready":
                case "plan_updated": {
                  trace.push({
                    kind: "plan",
                    nodes: (ev.nodes || []).map(
                      (n: { id: string; role: string; tools?: string[] }) => ({
                        id: n.id,
                        role: n.role,
                        tools: n.tools || [],
                      }),
                    ),
                  })
                  break
                }
                case "synthesis_wait":
                  trace.push({ kind: "synthesis_wait", tool_ids: ev.tool_ids || [] })
                  break
                case "tool_ready":
                  trace.push({ kind: "tool_ready", tool_id: ev.tool_id })
                  break
                case "node_start":
                  trace.push({ kind: "node_start", node_id: ev.node_id, role: ev.role })
                  break
                case "tool_call":
                  trace.push({ kind: "tool_call", tool: ev.tool, node_id: ev.node_id })
                  break
                case "tool_result":
                  trace.push({ kind: "tool_result", node_id: ev.node_id })
                  break
                case "node_complete":
                  trace.push({ kind: "node_complete", node_id: ev.node_id })
                  break
                case "synthesis_timeout":
                  trace.push({ kind: "synthesis_timeout", missing: ev.missing || [] })
                  break
                case "flow_complete":
                  finishWithAnswer(ev.final_answer || "(no answer returned)")
                  return
                case "error":
                  finishWithError(ev.message || "Unknown error")
                  return
              }
            } catch {
              // skip malformed events
            }
          }
        }

        if (!closed) finishWithAnswer("(no answer returned)")
      } catch (err) {
        finishWithError(String(err))
      } finally {
        clearInterval(keepAlive)
      }
    },
  })

  return new Response(stream, { headers: SSE_HEADERS })
}

/** Extract text from last 10 messages as conversation history */
function buildHistory(messages: Array<{ role: string; content?: string | Array<{ type: string; text?: string }>; parts?: Array<{ type: string; text?: string }> }>): string[] {
  // Take last 10 messages (excluding the very last user message which is the current query)
  const recent = messages.slice(-11, -1)
  return recent
    .map((m) => {
      let text = ""
      if (typeof m.content === "string") {
        text = m.content
      } else if (Array.isArray(m.content)) {
        text = m.content.find((p) => p.type === "text")?.text || ""
      } else if (Array.isArray(m.parts)) {
        text = m.parts.find((p) => p.type === "text")?.text || ""
      }
      // Skip config messages
      if (text.startsWith("__SPROUT_CONFIG__")) return null
      // Truncate long messages
      if (text.length > 500) text = text.slice(0, 500) + "..."
      return text ? `${m.role}: ${text}` : null
    })
    .filter((line): line is string => line !== null)
}
