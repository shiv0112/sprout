"use client"

import { useCallback, useEffect, useLayoutEffect, useState } from "react"
import { useAuth } from "@clerk/nextjs"
import { Check, Code, Copy, Loader2, Play } from "lucide-react"

import type { IntegrationSnippet, ToolParam } from "@/lib/registry"
import { fetchToolIntegrations } from "@/lib/registry"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const TAB_VALUES = ["overview", "playground", "integration"] as const

type TabValue = (typeof TAB_VALUES)[number]

const useIsomorphicLayoutEffect =
  typeof window !== "undefined" ? useLayoutEffect : useEffect

const REGISTRY_URL =
  process.env.NEXT_PUBLIC_REGISTRY_URL || "http://localhost:8766"

function buildDefaultValues(params: ToolParam[]): Record<string, unknown> {
  const defaults: Record<string, unknown> = {}
  for (const p of params) {
    if (p.default !== undefined) {
      defaults[p.name] = p.default
    } else if (p.type === "bool") {
      defaults[p.name] = false
    } else {
      defaults[p.name] = ""
    }
  }
  return defaults
}

function coerceValue(raw: unknown, type: string): unknown {
  if (type === "int" || type === "integer") return Number(raw)
  if (type === "float" || type === "number") return Number(raw)
  if (type === "bool" || type === "boolean") return Boolean(raw)
  return raw
}

function getTabValueFromHash(hash: string): TabValue {
  const value = hash.replace(/^#/, "")
  return (TAB_VALUES as readonly string[]).includes(value)
    ? (value as TabValue)
    : "overview"
}

// ---------------------------------------------------------------------------
// CopyButton
// ---------------------------------------------------------------------------

function CopyButton({
  text,
  className,
}: {
  text: string
  className?: string
}) {
  const [copied, setCopied] = useState(false)

  const handleCopy = useCallback(() => {
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    })
  }, [text])

  return (
    <Button
      variant="ghost"
      size="icon-xs"
      className={className}
      onClick={handleCopy}
    >
      {copied ? (
        <Check className="size-3.5 text-emerald-400" />
      ) : (
        <Copy className="size-3.5" />
      )}
    </Button>
  )
}

// ---------------------------------------------------------------------------
// CodeBlock with syntax highlighting via Tailwind
// ---------------------------------------------------------------------------

function highlightCode(code: string, language: string): React.ReactNode[] {
  const lines = code.split("\n")
  return lines.map((line, i) => {
    let highlighted: React.ReactNode

    if (language === "python") {
      highlighted = highlightPython(line)
    } else if (language === "bash") {
      highlighted = highlightBash(line)
    } else if (language === "json") {
      highlighted = highlightJson(line)
    } else {
      highlighted = line
    }

    return (
      <div key={i} className="leading-relaxed">
        {highlighted}
      </div>
    )
  })
}

function highlightPython(line: string): React.ReactNode {
  const parts: React.ReactNode[] = []
  // Simple regex-based highlighting
  const regex =
    /(#.*$)|("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')|\b(from|import|def|class|return|if|else|elif|for|while|with|as|try|except|raise|print|True|False|None)\b/g
  let lastIndex = 0
  let match: RegExpExecArray | null

  while ((match = regex.exec(line)) !== null) {
    if (match.index > lastIndex) {
      parts.push(line.slice(lastIndex, match.index))
    }

    if (match[1]) {
      // Comment
      parts.push(
        <span key={match.index} className="text-muted-foreground/60 italic">
          {match[1]}
        </span>
      )
    } else if (match[2]) {
      // String
      parts.push(
        <span key={match.index} className="text-emerald-400">
          {match[2]}
        </span>
      )
    } else if (match[3]) {
      // Keyword
      parts.push(
        <span key={match.index} className="text-violet-400 font-medium">
          {match[3]}
        </span>
      )
    }

    lastIndex = match.index + match[0].length
  }

  if (lastIndex < line.length) {
    parts.push(line.slice(lastIndex))
  }

  return <>{parts}</>
}

function highlightBash(line: string): React.ReactNode {
  const parts: React.ReactNode[] = []
  const regex =
    /(#.*$)|("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')|\b(curl|POST|GET|PUT|DELETE)\b|(-[A-Za-z]+|--[a-z-]+)|(\\$)/g
  let lastIndex = 0
  let match: RegExpExecArray | null

  while ((match = regex.exec(line)) !== null) {
    if (match.index > lastIndex) {
      parts.push(line.slice(lastIndex, match.index))
    }

    if (match[1]) {
      parts.push(
        <span key={match.index} className="text-muted-foreground/60 italic">
          {match[1]}
        </span>
      )
    } else if (match[2]) {
      parts.push(
        <span key={match.index} className="text-emerald-400">
          {match[2]}
        </span>
      )
    } else if (match[3]) {
      parts.push(
        <span key={match.index} className="text-blue-400 font-medium">
          {match[3]}
        </span>
      )
    } else if (match[4]) {
      parts.push(
        <span key={match.index} className="text-amber-400">
          {match[4]}
        </span>
      )
    } else if (match[5]) {
      parts.push(
        <span key={match.index} className="text-muted-foreground/50">
          {match[5]}
        </span>
      )
    }

    lastIndex = match.index + match[0].length
  }

  if (lastIndex < line.length) {
    parts.push(line.slice(lastIndex))
  }

  return <>{parts}</>
}

function highlightJson(line: string): React.ReactNode {
  const parts: React.ReactNode[] = []
  const regex =
    /("(?:[^"\\]|\\.)*")\s*(:?)|\b(true|false|null)\b|(-?\d+\.?\d*)/g
  let lastIndex = 0
  let match: RegExpExecArray | null

  while ((match = regex.exec(line)) !== null) {
    if (match.index > lastIndex) {
      parts.push(line.slice(lastIndex, match.index))
    }

    if (match[1] && match[2]) {
      // Key
      parts.push(
        <span key={match.index} className="text-blue-400">
          {match[1]}
        </span>
      )
      parts.push(match[2])
    } else if (match[1]) {
      // String value
      parts.push(
        <span key={match.index} className="text-emerald-400">
          {match[1]}
        </span>
      )
    } else if (match[3]) {
      // Boolean/null
      parts.push(
        <span key={match.index} className="text-amber-400">
          {match[3]}
        </span>
      )
    } else if (match[4]) {
      // Number
      parts.push(
        <span key={match.index} className="text-orange-400">
          {match[4]}
        </span>
      )
    }

    lastIndex = match.index + match[0].length
  }

  if (lastIndex < line.length) {
    parts.push(line.slice(lastIndex))
  }

  return <>{parts}</>
}

function CodeBlock({ code, language }: { code: string; language: string }) {
  return (
    <div className="group/code relative overflow-hidden rounded-xl border border-border/70 bg-card/70 ring-1 ring-border/70">
      <div className="flex items-center justify-between border-b border-border/70 bg-muted/35 px-4 py-2">
        <span className="text-xs font-medium uppercase tracking-wider text-muted-foreground/60">
          {language}
        </span>
        <CopyButton
          text={code}
          className="opacity-0 transition-opacity duration-200 group-hover/code:opacity-100"
        />
      </div>
      <pre className="overflow-x-auto p-4 text-sm font-mono">
        <code className="text-foreground/90">{highlightCode(code, language)}</code>
      </pre>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Overview Tab
// ---------------------------------------------------------------------------

function OverviewTab({ params }: { params: ToolParam[] }) {
  if (params.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center gap-3 py-16 text-center">
        <div className="flex size-12 items-center justify-center rounded-xl bg-muted/50 ring-1 ring-border/70">
          <Code className="size-5 text-muted-foreground/50" />
        </div>
        <p className="text-sm text-muted-foreground">
          This tool does not define any input parameters.
        </p>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <div>
        <h3 className="mb-4 text-base font-semibold">Input Parameters</h3>
        <div className="overflow-x-auto rounded-xl border border-border/70 ring-1 ring-border/70">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border/70 bg-muted/35">
                <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-muted-foreground/70">
                  Name
                </th>
                <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-muted-foreground/70">
                  Type
                </th>
                <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-muted-foreground/70">
                  Required
                </th>
                <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-muted-foreground/70">
                  Default
                </th>
                <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-muted-foreground/70">
                  Description
                </th>
              </tr>
            </thead>
            <tbody>
              {params.map((p, index) => (
                <tr
                  key={p.name}
                  className={`border-b border-border/50 transition-colors duration-200 last:border-0 hover:bg-muted/30 ${
                    index % 2 === 1 ? "bg-muted/15" : ""
                  }`}
                >
                  <td className="px-4 py-3">
                    <code className="rounded bg-muted/60 px-1.5 py-0.5 font-mono text-xs font-medium text-primary">
                      {p.name}
                    </code>
                  </td>
                  <td className="px-4 py-3">
                    <Badge variant="secondary" className="bg-violet-500/10 text-violet-400 text-[11px]">
                      {p.type}
                    </Badge>
                  </td>
                  <td className="px-4 py-3">
                    {p.required ? (
                      <Badge className="bg-red-500/10 text-red-400 text-[11px] border-0">
                        required
                      </Badge>
                    ) : (
                      <span className="text-xs text-muted-foreground/60">optional</span>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    {p.default !== undefined ? (
                      <code className="font-mono text-xs text-muted-foreground">
                        {String(p.default)}
                      </code>
                    ) : (
                      <span className="text-xs text-muted-foreground/40">{"\u2014"}</span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-muted-foreground">
                    {p.description || (
                      <span className="text-muted-foreground/40">{"\u2014"}</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Playground Tab
// ---------------------------------------------------------------------------

function PlaygroundTab({
  toolId,
  params,
}: {
  toolId: string
  params: ToolParam[]
}) {
  const { isSignedIn, getToken } = useAuth()

  const [formValues, setFormValues] = useState<Record<string, unknown>>(() =>
    buildDefaultValues(params)
  )
  const [isExecuting, setIsExecuting] = useState(false)
  const [result, setResult] = useState<unknown>(null)
  const [error, setError] = useState<string | null>(null)

  const setValue = useCallback((name: string, value: unknown) => {
    setFormValues((prev) => ({ ...prev, [name]: value }))
  }, [])

  const handleExecute = useCallback(async () => {
    setIsExecuting(true)
    setResult(null)
    setError(null)

    try {
      const args: Record<string, unknown> = {}
      for (const p of params) {
        const raw = formValues[p.name]
        if (raw === "" && !p.required) continue
        args[p.name] = coerceValue(raw, p.type)
      }

      const token = await getToken()
      const headers: Record<string, string> = {
        "Content-Type": "application/json",
      }
      if (token) headers["Authorization"] = `Bearer ${token}`

      const res = await fetch(`${REGISTRY_URL}/tools/${toolId}/execute`, {
        method: "POST",
        headers,
        body: JSON.stringify({ args }),
      })

      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: "Execution failed" }))
        throw new Error(err.detail || "Execution failed")
      }

      const data = await res.json()
      setResult(data)
    } catch (e) {
      setError(e instanceof Error ? e.message : "Execution failed")
    } finally {
      setIsExecuting(false)
    }
  }, [formValues, getToken, params, toolId])

  if (!isSignedIn) {
    return (
      <Card className="section-surface border-0 py-0">
        <CardHeader>
          <CardTitle>Authentication Required</CardTitle>
          <CardDescription>
            Sign in to use the playground and execute tools.
          </CardDescription>
        </CardHeader>
      </Card>
    )
  }

  return (
    <div className="space-y-6">
      <Card className="section-surface border-0 py-0">
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Play className="size-4 text-muted-foreground" />
            Parameters
          </CardTitle>
          <CardDescription>
            Fill in the parameters below and click Execute to run this tool.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {params.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              This tool has no parameters.
            </p>
          ) : (
            <div className="grid gap-5">
              {params.map((p) => (
                <div key={p.name} className="grid gap-1.5">
                  <label
                    htmlFor={`param-${p.name}`}
                    className="flex items-center gap-2 text-sm font-medium"
                  >
                    <code className="rounded bg-muted/60 px-1.5 py-0.5 font-mono text-xs text-primary">
                      {p.name}
                    </code>
                    <Badge variant="outline" className="border-border/70 bg-card/70 text-[10px]">
                      {p.type}
                    </Badge>
                    {p.required && (
                      <span className="text-xs text-red-400">*</span>
                    )}
                  </label>
                  {p.description && (
                    <p className="text-xs text-muted-foreground/70">
                      {p.description}
                    </p>
                  )}
                  <FieldInput
                    param={p}
                    value={formValues[p.name]}
                    onChange={setValue}
                  />
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      <Button
        size="lg"
        onClick={handleExecute}
        disabled={isExecuting}
        className="w-full gap-2 bg-gradient-to-r from-primary to-primary/80 shadow-sm transition-all duration-300 hover:shadow-md hover:shadow-primary/20 sm:w-auto"
      >
        {isExecuting ? (
          <Loader2 className="size-4 animate-spin" />
        ) : (
          <Play className="size-4" />
        )}
        {isExecuting ? "Executing..." : "Execute"}
      </Button>

      {error && (
        <Card className="section-surface border-red-500/35 bg-red-500/10 py-0">
          <CardHeader>
            <CardTitle className="text-red-300">Error</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-sm text-red-200/90">{error}</p>
          </CardContent>
        </Card>
      )}

      {result !== null && (
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-semibold">Result</h3>
            <CopyButton text={JSON.stringify(result, null, 2)} />
          </div>
          <CodeBlock
            code={JSON.stringify(result, null, 2)}
            language="json"
          />
        </div>
      )}
    </div>
  )
}

function FieldInput({
  param,
  value,
  onChange,
}: {
  param: ToolParam
  value: unknown
  onChange: (name: string, value: unknown) => void
}) {
  const id = `param-${param.name}`

  // Enum -> select
  if (param.enum && param.enum.length > 0) {
    return (
      <select
        id={id}
        value={String(value ?? "")}
        onChange={(e) => onChange(param.name, e.target.value)}
        className="h-9 w-full rounded-xl border border-border/70 bg-card/70 px-3 text-sm outline-none ring-1 ring-border/70 transition-all duration-300 focus-visible:ring-2 focus-visible:ring-primary/30"
      >
        <option value="">Select...</option>
        {param.enum.map((opt) => (
          <option key={opt} value={opt}>
            {opt}
          </option>
        ))}
      </select>
    )
  }

  // Bool -> checkbox
  if (param.type === "bool" || param.type === "boolean") {
    return (
      <label className="flex items-center gap-2 text-sm">
        <input
          id={id}
          type="checkbox"
          checked={Boolean(value)}
          onChange={(e) => onChange(param.name, e.target.checked)}
          className="size-4 rounded border-input accent-primary"
        />
        <span className="text-muted-foreground">
          {value ? "true" : "false"}
        </span>
      </label>
    )
  }

  // Number types
  if (
    param.type === "int" ||
    param.type === "integer" ||
    param.type === "float" ||
    param.type === "number"
  ) {
    return (
      <Input
        id={id}
        type="number"
        step={param.type === "int" || param.type === "integer" ? 1 : "any"}
        value={String(value ?? "")}
        onChange={(e) => onChange(param.name, e.target.value)}
        placeholder={param.default !== undefined ? String(param.default) : ""}
        className="rounded-xl border-border/70 bg-card/70 ring-1 ring-border/70 transition-all duration-300 focus-visible:ring-2 focus-visible:ring-primary/30"
      />
    )
  }

  // Default: text
  return (
    <Input
      id={id}
      type="text"
      value={String(value ?? "")}
      onChange={(e) => onChange(param.name, e.target.value)}
      placeholder={param.default !== undefined ? String(param.default) : ""}
      className="rounded-xl border-border/70 bg-card/70 ring-1 ring-border/70 transition-all duration-300 focus-visible:ring-2 focus-visible:ring-primary/30"
    />
  )
}

// ---------------------------------------------------------------------------
// Integration Tab
// ---------------------------------------------------------------------------

function IntegrationTab({
  toolId,
  toolDef,
}: {
  toolId: string
  toolDef: Record<string, unknown>
}) {
  const [integrations, setIntegrations] = useState<IntegrationSnippet[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [active, setActive] = useState<string>("openai")

  useEffect(() => {
    let cancelled = false
    fetchToolIntegrations(toolId)
      .then((res) => {
        if (cancelled) return
        setIntegrations(res.integrations)
        setError(null)
        // Default to OpenAI tools — most familiar to demo audiences.
        const openai = res.integrations.find((i) => i.target === "openai")
        if (openai) setActive(openai.target)
        else if (res.integrations[0]) setActive(res.integrations[0].target)
      })
      .catch((e: Error) => {
        if (cancelled) return
        setError(e.message || "Failed to load integrations")
        setIntegrations(null)
      })
    return () => {
      cancelled = true
    }
  }, [toolId])

  if (error) {
    return (
      <div className="rounded-xl border border-destructive/30 bg-destructive/5 p-4 text-sm text-destructive">
        Could not load integration snippets: {error}
      </div>
    )
  }

  if (!integrations) {
    return (
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        <Loader2 className="size-3.5 animate-spin" />
        Loading snippets…
      </div>
    )
  }

  const current = integrations.find((i) => i.target === active) ?? integrations[0]

  return (
    <div className="space-y-6">
      <div>
        <h3 className="mb-2 text-[13px] font-semibold tracking-tight text-foreground">
          Use this tool from any framework
        </h3>
        <p className="max-w-2xl text-[12.5px] leading-relaxed text-muted-foreground/85">
          Pick a stack — copy the snippet — replace <code className="rounded bg-muted/50 px-1 font-mono text-[11px] text-primary/90">YOUR_SPROUT_API_KEY</code>.
          The <code className="rounded bg-muted/50 px-1 font-mono text-[11px] text-primary/90">SproutRuntime</code> path
          gives you native objects for AG2 / LangChain / Pydantic-AI; the others
          go straight over HTTP.
        </p>
      </div>

      <div className="flex flex-wrap gap-1.5">
        {integrations.map((i) => (
          <button
            key={i.target}
            type="button"
            onClick={() => setActive(i.target)}
            className={
              "rounded-full border px-3 py-1 text-[11.5px] font-medium transition-colors " +
              (i.target === active
                ? "border-primary/40 bg-primary/12 text-primary"
                : "border-white/10 bg-white/[0.02] text-muted-foreground hover:border-white/20 hover:text-foreground")
            }
          >
            {i.label}
          </button>
        ))}
      </div>

      {current && (
        <>
          {current.install && (
            <div>
              <div className="mb-1.5 text-[10px] font-medium uppercase tracking-[0.18em] text-muted-foreground/55">
                Install
              </div>
              <CodeBlock code={current.install} language="bash" />
            </div>
          )}
          <div>
            <div className="mb-1.5 text-[10px] font-medium uppercase tracking-[0.18em] text-muted-foreground/55">
              {current.label} snippet
            </div>
            <CodeBlock code={current.snippet} language={current.language} />
          </div>
        </>
      )}

      <div>
        <div className="mb-1.5 flex items-center gap-2 text-[10px] font-medium uppercase tracking-[0.18em] text-muted-foreground/55">
          <Code className="size-3" />
          Tool definition (JSON Schema)
        </div>
        <CodeBlock code={JSON.stringify(toolDef, null, 2)} language="json" />
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main exported component
// ---------------------------------------------------------------------------

export function ToolDetailTabs({
  toolId,
  params,
  toolDef,
}: {
  toolId: string
  params: ToolParam[]
  toolDef: Record<string, unknown>
}) {
  const [activeTab, setActiveTab] = useState<TabValue>("overview")

  useIsomorphicLayoutEffect(() => {
    const syncFromHash = () => {
      setActiveTab(getTabValueFromHash(window.location.hash))
    }

    syncFromHash()
    window.addEventListener("hashchange", syncFromHash)

    return () => {
      window.removeEventListener("hashchange", syncFromHash)
    }
  }, [])

  const handleTabChange = useCallback((value: string) => {
    const nextTab = getTabValueFromHash(value)
    setActiveTab(nextTab)

    const nextHash = `#${nextTab}`
    if (window.location.hash !== nextHash) {
      window.history.replaceState(
        window.history.state,
        "",
        `${window.location.pathname}${window.location.search}${nextHash}`
      )
    }
  }, [])

  return (
    <Tabs value={activeTab} onValueChange={handleTabChange}>
      <TabsList variant="line">
        <TabsTrigger value="overview">Overview</TabsTrigger>
        <TabsTrigger value="playground">
          <Play className="size-3.5" />
          Playground
        </TabsTrigger>
        <TabsTrigger value="integration">
          <Code className="size-3.5" />
          Integration
        </TabsTrigger>
      </TabsList>

      <div className="mt-6">
        <TabsContent value="overview">
          <OverviewTab params={params} />
        </TabsContent>

        <TabsContent value="playground">
          <PlaygroundTab toolId={toolId} params={params} />
        </TabsContent>

        <TabsContent value="integration">
          {/* key={toolId} forces a fresh mount when the user navigates to
              a different tool, so the tab never shows stale snippets or a
              stale error state during the refetch. */}
          <IntegrationTab key={toolId} toolId={toolId} toolDef={toolDef} />
        </TabsContent>
      </div>
    </Tabs>
  )
}
