"use client"

import { useState, useRef, useCallback } from "react"
import { useAuth } from "@clerk/nextjs"
import {
  Sprout,
  Send,
  Loader2,
  CheckCircle2,
  AlertCircle,
  KeyRound,
  Eye,
  EyeOff,
} from "lucide-react"

import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { ScrollArea } from "@/components/ui/scroll-area"
import { SproutDag, type DagPlan, type NodeStatus } from "@/components/sprout-dag"

const CHAT_BACKEND =
  typeof window !== "undefined"
    ? (process.env.NEXT_PUBLIC_CHAT_BACKEND || "http://localhost:8765")
    : "http://localhost:8765"

// ── Types ──────────────────────────────────────────────────────────────────

interface SproutEvent {
  type: string
  [key: string]: unknown
}

type Phase = "idle" | "planning" | "config" | "synthesizing" | "executing" | "complete" | "error"

interface MissingEnv {
  tool_id: string
  var_name: string
  description: string
}

// ── Component ──────────────────────────────────────────────────────────────

export function SproutExecute() {
  const { getToken } = useAuth()
  const [input, setInput] = useState("")
  const [phase, setPhase] = useState<Phase>("idle")
  const [plan, setPlan] = useState<DagPlan | null>(null)
  const [nodeStatuses, setNodeStatuses] = useState<Record<string, NodeStatus>>({})
  const [nodeResults, setNodeResults] = useState<Record<string, string>>({})
  const [events, setEvents] = useState<SproutEvent[]>([])
  const [finalAnswer, setFinalAnswer] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [missingEnvs, setMissingEnvs] = useState<MissingEnv[]>([])
  const [envValues, setEnvValues] = useState<Record<string, string>>({})
  const [showEnvValues, setShowEnvValues] = useState<Record<string, boolean>>({})
  const [pendingRunId, setPendingRunId] = useState<string | null>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const abortRef = useRef<AbortController | null>(null)
  const pendingHeadersRef = useRef<Record<string, string>>({})

  const scrollToBottom = useCallback(() => {
    requestAnimationFrame(() => {
      if (scrollRef.current) {
        scrollRef.current.scrollTop = scrollRef.current.scrollHeight
      }
    })
  }, [])

  const addEvent = useCallback(
    (evt: SproutEvent) => {
      setEvents((prev) => [...prev, evt])
      scrollToBottom()
    },
    [scrollToBottom],
  )

  // Process a single SSE event. Defined as a stable callback so handleSubmit
  // can include it in its dependency list (react-hooks/exhaustive-deps).
  const processEvent = useCallback(
    (evt: SproutEvent) => {
      addEvent(evt)

      switch (evt.type) {
        case "plan_ready":
        case "plan_updated": {
          const p = evt as unknown as DagPlan & { type: string }
          setPlan(p)
          const statuses: Record<string, NodeStatus> = {}
          for (const node of p.nodes || []) {
            statuses[node.id] = "pending"
          }
          setNodeStatuses(statuses)
          break
        }

        case "synthesis_wait":
          setPhase("synthesizing")
          break

        case "tool_ready":
          break

        case "node_start":
          setNodeStatuses((prev) => ({
            ...prev,
            [evt.node_id as string]: "running",
          }))
          break

        case "node_complete":
          setNodeStatuses((prev) => ({
            ...prev,
            [evt.node_id as string]: "complete",
          }))
          if (evt.result) {
            const preview =
              typeof evt.result === "string"
                ? evt.result.slice(0, 200)
                : JSON.stringify(evt.result).slice(0, 200)
            setNodeResults((prev) => ({
              ...prev,
              [evt.node_id as string]: preview,
            }))
          }
          break

        case "flow_complete":
          setFinalAnswer(evt.final_answer as string)
          setPhase("complete")
          break

        case "error":
          setError(evt.message as string)
          setPhase("error")
          break
      }

      scrollToBottom()
    },
    [addEvent, scrollToBottom],
  )

  const streamRun = useCallback(async (runId: string, headers: Record<string, string>) => {
    abortRef.current = new AbortController()
    const streamResp = await fetch(`${CHAT_BACKEND}/sprout/stream/${runId}`, {
      signal: abortRef.current.signal,
      headers,
    })
    if (!streamResp.ok || !streamResp.body) {
      throw new Error("Failed to connect to event stream")
    }
    const reader = streamResp.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ""
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split("\n")
      buffer = lines.pop() || ""
      for (const line of lines) {
        if (!line.startsWith("data: ")) continue
        try {
          const evt: SproutEvent = JSON.parse(line.slice(6))
          processEvent(evt)
        } catch { /* skip malformed */ }
      }
    }
  }, [processEvent])

  const handleSubmit = useCallback(async () => {
    const query = input.trim()
    if (!query || phase === "planning" || phase === "executing") return

    // Reset state
    setPlan(null)
    setNodeStatuses({})
    setNodeResults({})
    setEvents([])
    setFinalAnswer(null)
    setError(null)
    setPhase("planning")
    setInput("")

    try {
      const token = await getToken()
      const headers: Record<string, string> = { "Content-Type": "application/json" }
      if (token) headers["Authorization"] = `Bearer ${token}`

      // Step 1: POST /sprout/start
      const startResp = await fetch(`${CHAT_BACKEND}/sprout/start`, {
        method: "POST",
        headers,
        body: JSON.stringify({ request: query }),
      })

      if (!startResp.ok) {
        const err = await startResp.json().catch(() => ({ detail: "Request failed" }))
        throw new Error(err.detail || `HTTP ${startResp.status}`)
      }

      const startData = await startResp.json()
      const runId: string = startData.run_id

      if (startData.status === "needs_config") {
        setPlan(startData.plan)
        addEvent({ type: "plan_ready", ...startData.plan })
        const statuses: Record<string, NodeStatus> = {}
        for (const node of startData.plan.nodes || []) {
          statuses[node.id] = "pending"
        }
        setNodeStatuses(statuses)

        const envs: MissingEnv[] = startData.missing_envs || []
        if (envs.length > 0) {
          setMissingEnvs(envs)
          setPendingRunId(runId)
          pendingHeadersRef.current = headers
          setPhase("config")
          return
        }

        const execResp = await fetch(`${CHAT_BACKEND}/sprout/execute/${runId}`, {
          method: "POST",
          headers,
          body: JSON.stringify({ env_vars: {} }),
        })
        if (!execResp.ok) {
          const err = await execResp.json().catch(() => ({ detail: "Execution failed" }))
          throw new Error(err.detail || `HTTP ${execResp.status}`)
        }
        setPhase("executing")
      } else {
        setPhase("executing")
      }

      await streamRun(runId, headers)
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") return
      const msg = err instanceof Error ? err.message : "Unknown error"
      setError(msg)
      setPhase("error")
      addEvent({ type: "error", message: msg })
    }
  }, [input, phase, getToken, addEvent, streamRun])

  const handleConfigSubmit = useCallback(async () => {
    if (!pendingRunId) return
    const headers = pendingHeadersRef.current
    setPhase("executing")
    setMissingEnvs([])
    try {
      const execResp = await fetch(`${CHAT_BACKEND}/sprout/execute/${pendingRunId}`, {
        method: "POST",
        headers,
        body: JSON.stringify({ env_vars: envValues }),
      })
      if (!execResp.ok) {
        const err = await execResp.json().catch(() => ({ detail: "Execution failed" }))
        throw new Error(err.detail || `HTTP ${execResp.status}`)
      }
      await streamRun(pendingRunId, headers)
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") return
      const msg = err instanceof Error ? err.message : "Unknown error"
      setError(msg)
      setPhase("error")
      addEvent({ type: "error", message: msg })
    } finally {
      setPendingRunId(null)
      setEnvValues({})
    }
  }, [pendingRunId, envValues, addEvent, streamRun])

  const isActive = phase === "planning" || phase === "config" || phase === "synthesizing" || phase === "executing"

  return (
    <div className="flex h-[calc(100vh-12.5rem)] min-h-[38rem] flex-col overflow-hidden">
      {/* Event stream + DAG area */}
      <div className="flex-1 overflow-hidden">
        <ScrollArea className="h-full">
          <div ref={scrollRef} className="h-full overflow-y-auto">
            {phase === "idle" ? (
              <div className="flex flex-col items-center justify-center h-full min-h-[60vh] px-6 py-12">
                <div className="relative mb-8">
                  <span className="absolute -inset-4 animate-pulse rounded-3xl bg-gradient-to-br from-sprout/30 to-sprout/10 blur-2xl" />
                  <div className="relative flex size-20 items-center justify-center rounded-2xl bg-gradient-to-br from-sprout/20 to-sprout/5 ring-1 ring-sprout/30 shadow-lg shadow-sprout/15">
                    <Sprout className="size-10 text-sprout" />
                  </div>
                </div>
                <h2 className="text-3xl font-bold tracking-tight mb-3">Sprout Execute</h2>
                <p className="text-sm text-muted-foreground mb-6 max-w-md text-center leading-relaxed">
                  Describe a task in natural language. Sprout will plan a multi-agent workflow,
                  synthesize any missing tools, and execute everything in real time.
                </p>
                <div className="flex flex-wrap gap-2 justify-center max-w-lg">
                  {[
                    "Get the weather in Tokyo and Singapore, then compare them",
                    "Convert 100 USD to EUR and JPY",
                    "Generate a QR code for https://sprout.dev",
                  ].map((example) => (
                    <button
                      key={example}
                      onClick={() => {
                        setInput(example)
                      }}
                      className="rounded-lg border border-border/70 bg-card/70 px-3 py-2 text-xs text-muted-foreground ring-1 ring-border/70 transition-all hover:bg-card hover:text-foreground"
                    >
                      {example}
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              <div className="mx-auto max-w-3xl space-y-4 px-6 py-6">
                {/* Phase indicator */}
                <div className="flex items-center gap-2 text-sm">
                  {phase === "complete" ? (
                    <CheckCircle2 className="size-4 text-emerald-400" />
                  ) : phase === "error" ? (
                    <AlertCircle className="size-4 text-red-400" />
                  ) : (
                    <Loader2 className="size-4 animate-spin text-primary" />
                  )}
                  <span className="font-medium text-foreground">
                    {phase === "planning" && "Planning execution graph..."}
                    {phase === "config" && "API keys required"}
                    {phase === "synthesizing" && "Synthesizing missing tools..."}
                    {phase === "executing" && "Executing workflow..."}
                    {phase === "complete" && "Execution complete"}
                    {phase === "error" && "Execution failed"}
                  </span>
                </div>

                {/* DAG visualization */}
                {plan && (
                  <SproutDag
                    plan={plan}
                    nodeStatuses={nodeStatuses}
                    nodeResults={nodeResults}
                  />
                )}

                {/* Config card — collect missing API keys */}
                {phase === "config" && missingEnvs.length > 0 && (
                  <Card className="border-amber-500/30 bg-amber-500/5 p-4">
                    <div className="flex items-center gap-2 mb-3">
                      <KeyRound className="size-4 text-amber-400" />
                      <p className="text-xs font-medium text-amber-400">
                        API keys needed to run this workflow
                      </p>
                    </div>
                    <div className="space-y-3">
                      {missingEnvs.map((env) => (
                        <div key={env.var_name}>
                          <label className="block text-xs text-muted-foreground mb-1">
                            <span className="font-mono text-foreground">{env.var_name}</span>
                            {env.description && (
                              <span className="ml-1 text-muted-foreground/60">
                                — {env.description}
                              </span>
                            )}
                            {env.tool_id && (
                              <span className="ml-1 text-muted-foreground/40">
                                (used by {env.tool_id.split(".").pop()})
                              </span>
                            )}
                          </label>
                          <div className="relative">
                            <input
                              type={showEnvValues[env.var_name] ? "text" : "password"}
                              value={envValues[env.var_name] || ""}
                              onChange={(e) =>
                                setEnvValues((prev) => ({
                                  ...prev,
                                  [env.var_name]: e.target.value,
                                }))
                              }
                              placeholder={`Enter ${env.var_name}`}
                              className="w-full rounded-lg border border-border/70 bg-card/75 px-3 py-2 pr-9 text-sm font-mono text-foreground ring-1 ring-border/70 placeholder:text-muted-foreground/40 outline-none focus:ring-2 focus:ring-primary/30"
                            />
                            <button
                              type="button"
                              onClick={() =>
                                setShowEnvValues((prev) => ({
                                  ...prev,
                                  [env.var_name]: !prev[env.var_name],
                                }))
                              }
                              className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground/50 hover:text-foreground"
                            >
                              {showEnvValues[env.var_name] ? (
                                <EyeOff className="size-4" />
                              ) : (
                                <Eye className="size-4" />
                              )}
                            </button>
                          </div>
                        </div>
                      ))}
                    </div>
                    <div className="mt-4 flex gap-2">
                      <Button
                        size="sm"
                        onClick={handleConfigSubmit}
                        disabled={missingEnvs.some((e) => !envValues[e.var_name]?.trim())}
                        className="bg-amber-600 hover:bg-amber-500 text-white"
                      >
                        Run with keys
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => {
                          handleConfigSubmit()
                        }}
                        className="text-muted-foreground"
                      >
                        Skip (run without keys)
                      </Button>
                    </div>
                  </Card>
                )}

                {/* Event log */}
                <div className="space-y-1">
                  <p className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground/50">
                    Event Log
                  </p>
                  <div className="rounded-lg border bg-card/30 p-3 font-mono text-[11px] max-h-48 overflow-y-auto">
                    {events.map((evt, i) => {
                      const nodeId = typeof evt.node_id === "string" ? evt.node_id : ""
                      const tool = typeof evt.tool === "string" ? evt.tool : ""
                      const message = typeof evt.message === "string" ? evt.message : ""
                      return (
                        <div key={i} className="text-muted-foreground/60 leading-relaxed">
                          <span className="text-primary/60">{evt.type}</span>
                          {nodeId && (
                            <span className="text-muted-foreground/40">
                              {" "}node={nodeId}
                            </span>
                          )}
                          {tool && (
                            <span className="text-amber-400/50">
                              {" "}tool={tool}
                            </span>
                          )}
                          {message && (
                            <span className="text-muted-foreground/40">
                              {" "}{message.slice(0, 100)}
                            </span>
                          )}
                        </div>
                      )
                    })}
                  </div>
                </div>

                {/* Final answer */}
                {finalAnswer && (
                  <Card className="border-emerald-500/30 bg-emerald-500/5 p-4">
                    <p className="text-xs font-medium text-emerald-400 mb-2">Final Answer</p>
                    <p className="text-sm text-foreground whitespace-pre-wrap leading-relaxed">
                      {finalAnswer}
                    </p>
                  </Card>
                )}

                {/* Error */}
                {error && (
                  <Card className="border-red-500/30 bg-red-500/5 p-4">
                    <p className="text-xs font-medium text-red-400 mb-1">Error</p>
                    <p className="text-sm text-red-300/80">{error}</p>
                  </Card>
                )}
              </div>
            )}
          </div>
        </ScrollArea>
      </div>

      {/* Input bar */}
      <div className="shrink-0 border-t border-border/70 bg-background/85 px-6 py-4 backdrop-blur-xl">
        <form
          onSubmit={(e) => {
            e.preventDefault()
            handleSubmit()
          }}
          className="mx-auto flex max-w-3xl items-end gap-3"
        >
          <div className="relative flex-1">
            <textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault()
                  handleSubmit()
                }
              }}
              placeholder="Describe a task for Sprout to execute..."
              rows={1}
              disabled={isActive}
              className="field-sizing-content min-h-[44px] max-h-[200px] w-full resize-none rounded-xl border border-border/70 bg-card/75 px-4 py-3 pr-12 text-sm text-foreground shadow-inner shadow-black/15 ring-1 ring-border/70 placeholder:text-muted-foreground/60 outline-none transition-all duration-300 focus:border-border focus:bg-card focus:ring-2 focus:ring-primary/30 disabled:opacity-50"
            />
          </div>
          <Button
            type="submit"
            size="icon"
            disabled={!input.trim() || isActive}
            className="size-11 shrink-0 rounded-xl border border-primary/35 bg-gradient-to-br from-primary to-primary/80 shadow-sm transition-all duration-300 hover:shadow-md hover:shadow-primary/20 disabled:opacity-40"
          >
            {isActive ? (
              <Loader2 className="size-4 animate-spin" />
            ) : (
              <Send className="size-4" />
            )}
          </Button>
        </form>
      </div>
    </div>
  )
}
