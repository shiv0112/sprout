"use client"

import { useEffect, useMemo, useRef, useState } from "react"
import Link from "next/link"
import {
  Search,
  Package,
  Tag,
  User,
  Layers,
  X,
  PackageSearch,
  Sparkles,
  ArrowRight,
  PlayCircle,
  CheckCircle2,
  Star,
  Activity,
  ShieldCheck,
  ShieldAlert,
  Loader2,
} from "lucide-react"
import type { Tool, ToolStats, RankedTool } from "@/lib/registry"
import { searchToolsSemantic } from "@/lib/registry"
import type { SystemStatus } from "@/lib/system-status"
import { Badge } from "@/components/ui/badge"
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function StatsRow({ stats }: { stats: ToolStats }) {
  const items: Array<{ label: string; value: number }> = [
    { label: "Tools", value: stats.total },
    { label: "Categories", value: Object.keys(stats.categories).length },
    { label: "Authors", value: stats.unique_authors },
    { label: "Tags", value: Object.keys(stats.tags).length },
  ]

  // Single bordered strip with cells separated by hairline dividers.
  // Numbers in monospace tabular-nums so the row doesn't reflow as values
  // change. No icons, no gradients — Linear-style restraint: information
  // first, decoration zero.
  return (
    <div className="mb-6 grid grid-cols-2 divide-x divide-border/60 overflow-hidden rounded-lg border border-border/60 bg-card/40 sm:grid-cols-4">
      {items.map((item, idx) => (
        <div
          key={item.label}
          className={cn(
            "flex flex-col gap-0.5 px-4 py-3",
            idx >= 2 && "border-t border-border/60 sm:border-t-0",
          )}
        >
          <span className="font-mono text-lg tabular-nums tracking-tight text-foreground">
            {item.value}
          </span>
          <span className="text-[11px] uppercase tracking-[0.12em] text-muted-foreground/70">
            {item.label}
          </span>
        </div>
      ))}
    </div>
  )
}

function SystemStatusPanel({ systemStatus }: { systemStatus: SystemStatus }) {
  const overallOk = systemStatus.status === "ok"

  return (
    <Card className="section-surface mb-8 overflow-hidden py-0">
      <CardHeader className="border-b border-border/60 bg-background/35 pb-4 pt-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="space-y-1">
            <CardTitle className="flex items-center gap-2 text-base">
              <Activity className="size-4 text-primary" />
              System status
            </CardTitle>
            <CardDescription>
              Live readiness across the operator-facing services.
            </CardDescription>
          </div>
          <Badge
            variant="outline"
            className={
              overallOk
                ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-300"
                : "border-amber-500/30 bg-amber-500/10 text-amber-300"
            }
          >
            {overallOk ? "All core services ready" : "Action needed"}
          </Badge>
        </div>
      </CardHeader>
      <CardContent className="grid gap-3 px-5 py-5 md:grid-cols-3">
        {systemStatus.services.map((service) => {
          const ok = service.status === "ok"
          return (
            <div
              key={service.key}
              className="rounded-2xl border border-border/70 bg-background/45 p-4"
            >
              <div className="flex items-start justify-between gap-3">
                <div>
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium">{service.label}</span>
                    {service.optional && (
                      <Badge
                        variant="secondary"
                        className="rounded-full bg-muted/65 text-[10px] font-normal"
                      >
                        optional
                      </Badge>
                    )}
                  </div>
                  <p className="mt-1 text-xs text-muted-foreground">
                    {service.httpStatus ? `HTTP ${service.httpStatus}` : "No response"}
                  </p>
                </div>
                {ok ? (
                  <ShieldCheck className="size-4 text-emerald-400" />
                ) : (
                  <ShieldAlert className="size-4 text-amber-300" />
                )}
              </div>
              <p className="mt-3 text-sm text-muted-foreground">
                {service.detail || (ok ? "Ready" : "Waiting for response")}
              </p>
            </div>
          )
        })}
      </CardContent>
    </Card>
  )
}

function formatCount(n: number): string {
  if (n >= 1000) return `${(n / 1000).toFixed(1).replace(/\.0$/, "")}k`
  return String(n)
}

function relativeTime(iso: string | null): string | null {
  if (!iso) return null
  const ts = new Date(iso).getTime()
  if (Number.isNaN(ts)) return null
  const diff = Date.now() - ts
  const m = Math.floor(diff / 60_000)
  if (m < 1) return "just now"
  if (m < 60) return `${m}m ago`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h ago`
  const d = Math.floor(h / 24)
  if (d < 30) return `${d}d ago`
  return new Date(ts).toLocaleDateString()
}

function ToolCard({ tool, confidence }: { tool: Tool; confidence?: number }) {
  const truncatedDescription =
    tool.description.length > 120
      ? tool.description.slice(0, 120).trimEnd() + "..."
      : tool.description

  // Stats default to zero/never when the registry doesn't yet supply them
  // (older deploys, fresh DB, etc). The UI should never break on missing data.
  const stats = tool.stats
  const executionCount = stats?.execution_count ?? 0
  const successRate = stats?.success_rate ?? null
  const lastStatus = stats?.last_status ?? "never"
  const favoriteCount = stats?.favorite_count ?? 0
  const lastExecuted = relativeTime(stats?.last_executed_at ?? null)

  // Color the success-rate badge so the eye can scan a long catalog quickly.
  let successBadgeClass = "bg-muted/50 text-muted-foreground"
  let successLabel = "—"
  if (successRate !== null) {
    const pct = Math.round(successRate * 100)
    successLabel = `${pct}%`
    if (pct >= 90) successBadgeClass = "bg-emerald-500/15 text-emerald-300 ring-emerald-500/30"
    else if (pct >= 70) successBadgeClass = "bg-amber-500/15 text-amber-300 ring-amber-500/30"
    else successBadgeClass = "bg-red-500/15 text-red-300 ring-red-500/30"
  }

  // Last-status dot mirrors GitHub Actions / CI pill style.
  const statusDotClass =
    lastStatus === "success"
      ? "bg-emerald-400 shadow-emerald-500/40"
      : lastStatus === "error"
        ? "bg-red-400 shadow-red-500/40"
        : "bg-muted-foreground/30"

  return (
    <Link href={`/tools/${tool.id}`} className="block">
      <Card className="section-surface group/tool relative h-full cursor-pointer overflow-hidden border-border/70 bg-card/75 py-0 transition-all duration-300 hover:border-primary/40 hover:shadow-[var(--shadow-panel-lg)]">
        <div className="pointer-events-none absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-primary/40 to-transparent opacity-70" />

        <CardHeader className="px-5 pb-3 pt-5">
          <div className="flex items-start justify-between gap-2">
            <CardTitle className="flex min-w-0 items-center gap-2.5">
              <div className="flex size-8 items-center justify-center rounded-lg bg-primary/10 ring-1 ring-primary/20 transition-all duration-300 group-hover/tool:bg-primary/15 group-hover/tool:ring-primary/40">
                <Package className="size-4 text-primary/70 transition-colors duration-300 group-hover/tool:text-primary" />
              </div>
              <span className="truncate transition-colors duration-300 group-hover/tool:text-primary/90">
                {tool.name}
              </span>
            </CardTitle>
            <div className="flex shrink-0 items-center gap-1.5">
              {confidence !== undefined && confidence >= 0.6 && (
                <span
                  title={`Semantic match confidence ${Math.round(confidence * 100)}%`}
                  className={cn(
                    "rounded-full px-2 py-0.5 font-mono text-[10px] tabular-nums ring-1",
                    confidence >= 0.85
                      ? "bg-emerald-500/10 text-emerald-300 ring-emerald-500/30"
                      : "bg-amber-500/10 text-amber-300 ring-amber-500/30",
                  )}
                >
                  {Math.round(confidence * 100)}%
                </span>
              )}
              <Badge
                variant="secondary"
                className="rounded-full border border-border/80 bg-background/60 px-2.5 py-0.5 font-mono text-[10px] text-muted-foreground"
              >
                v{tool.version}
              </Badge>
            </div>
          </div>
          <CardDescription className="line-clamp-2 pt-0.5 text-sm leading-6 text-muted-foreground/95">
            {truncatedDescription}
          </CardDescription>
        </CardHeader>

        <CardContent className="flex flex-col gap-3 px-5 pb-4 pt-0">
          {/* Category */}
          <div className="flex items-center gap-2">
            <Layers className="size-3.5 text-muted-foreground/70" />
            <Badge
              variant="outline"
              className="rounded-full border-border/70 bg-background/45 font-normal"
            >
              {tool.category}
            </Badge>
          </div>

          {/* Tags */}
          {tool.tags.length > 0 && (
            <div className="flex flex-wrap items-center gap-1.5">
              <Tag className="size-3.5 shrink-0 text-muted-foreground/70" />
              {tool.tags.slice(0, 4).map((tag) => (
                <Badge
                  key={tag}
                  variant="secondary"
                  className="rounded-full bg-muted/65 text-[10px] font-normal"
                >
                  {tag}
                </Badge>
              ))}
              {tool.tags.length > 4 && (
                <span className="text-[10px] text-muted-foreground">
                  +{tool.tags.length - 4}
                </span>
              )}
            </div>
          )}

          {/* Stats row — execution count, success rate, favorites, last status */}
          <div className="flex flex-wrap items-center gap-1.5 pt-1">
            <span
              title={`${executionCount} total runs`}
              className="inline-flex items-center gap-1 rounded-full bg-muted/50 px-2 py-0.5 text-[10px] text-muted-foreground"
            >
              <PlayCircle className="size-3 text-muted-foreground/70" />
              {formatCount(executionCount)}
            </span>
            <span
              title={
                successRate === null
                  ? "Never executed"
                  : `${stats?.success_count ?? 0} of ${executionCount} runs succeeded`
              }
              className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] ring-1 ${successBadgeClass}`}
            >
              <CheckCircle2 className="size-3" />
              {successLabel}
            </span>
            {favoriteCount > 0 && (
              <span
                title={`${favoriteCount} favorites`}
                className="inline-flex items-center gap-1 rounded-full bg-amber-500/10 px-2 py-0.5 text-[10px] text-amber-300 ring-1 ring-amber-500/25"
              >
                <Star className="size-3" />
                {formatCount(favoriteCount)}
              </span>
            )}
            <span
              title={
                lastStatus === "never"
                  ? "Never executed"
                  : `Last run: ${lastStatus}${lastExecuted ? ` (${lastExecuted})` : ""}`
              }
              className="inline-flex items-center gap-1 rounded-full bg-background/60 px-2 py-0.5 text-[10px] text-muted-foreground ring-1 ring-border/60"
            >
              <span
                className={`size-1.5 rounded-full shadow-sm ${statusDotClass}`}
              />
              {lastExecuted ?? "never"}
            </span>
          </div>
        </CardContent>

        <CardFooter className="mt-auto border-t border-border/60 bg-background/35 px-5 py-3">
          <div className="flex w-full items-center justify-between">
            <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
              <User className="size-3.5" />
              <span>{tool.author}</span>
            </div>
            <ArrowRight className="size-3.5 text-muted-foreground/0 transition-all duration-300 group-hover/tool:translate-x-0.5 group-hover/tool:text-muted-foreground/70" />
          </div>
        </CardFooter>
      </Card>
    </Link>
  )
}

function EmptyState({ query }: { query: string }) {
  return (
    <div className="section-surface flex flex-col items-center justify-center gap-6 px-4 py-24 text-center">
      <div className="relative">
        <span className="absolute -inset-6 rounded-full bg-gradient-to-br from-primary/10 to-primary/5 blur-2xl" />
        <div className="relative flex size-20 items-center justify-center rounded-2xl bg-muted/80 ring-1 ring-border/70">
          <PackageSearch className="size-10 text-muted-foreground/50" />
        </div>
      </div>
      <div className="max-w-sm space-y-2">
        <h3 className="text-lg font-semibold">No tools found</h3>
        <p className="text-sm leading-relaxed text-muted-foreground">
          No results match{" "}
          <span className="rounded-md bg-muted px-1.5 py-0.5 font-medium text-foreground">
            {query}
          </span>
          . Try a different search term or clear the filters.
        </p>
      </div>
      <Button
        variant="outline"
        size="sm"
        className="mt-2"
        onClick={() => window.location.reload()}
      >
        Clear all filters
      </Button>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main client component
// ---------------------------------------------------------------------------

export function CatalogClient({
  tools,
  stats,
  systemStatus,
  error,
}: {
  tools: Tool[]
  stats: ToolStats | null
  systemStatus: SystemStatus | null
  error: string | null
}) {
  const [search, setSearch] = useState("")
  const [activeCategory, setActiveCategory] = useState<string | null>(null)

  // Semantic ranking from the registry's BM25 index. `null` means no
  // semantic response yet (use lexical fallback); `[]` means the registry
  // explicitly returned no matches.
  const [ranked, setRanked] = useState<RankedTool[] | null>(null)
  const [searching, setSearching] = useState(false)
  const [semanticFailed, setSemanticFailed] = useState(false)
  const latestSearchRef = useRef(0)

  const trimmedQuery = search.trim()

  // Debounced semantic search. Each keystroke schedules a new fetch 180ms
  // later; only the most recent response is allowed to mutate state so
  // out-of-order responses can't overwrite the current ranking.
  //
  // Bumping `latestSearchRef` on EVERY effect run (including the empty-query
  // branch) is what makes this race-free: any in-flight request from a
  // previous query that hasn't resolved yet sees its `my` no longer match
  // the ref and bails out before mutating `ranked`. Without this, clearing
  // the input mid-flight would let the prior response repopulate `ranked`
  // after we'd already cleared it.
  useEffect(() => {
    const my = ++latestSearchRef.current
    if (!trimmedQuery) {
      setRanked(null)
      setSearching(false)
      setSemanticFailed(false)
      return
    }
    setSearching(true)
    const t = setTimeout(async () => {
      try {
        const hits = await searchToolsSemantic(trimmedQuery, 50)
        if (my !== latestSearchRef.current) return
        setRanked(hits)
        setSemanticFailed(false)
      } catch {
        if (my !== latestSearchRef.current) return
        // Registry unreachable — fall back to local lexical match below.
        setRanked(null)
        setSemanticFailed(true)
      } finally {
        if (my === latestSearchRef.current) setSearching(false)
      }
    }, 180)
    return () => clearTimeout(t)
  }, [trimmedQuery])

  // Derive unique categories
  const categories = useMemo(() => {
    const set = new Set(tools.map((t) => t.category))
    return Array.from(set).sort()
  }, [tools])

  // Confidence map keyed by tool id — drives the per-card score chip.
  const confidenceById = useMemo(() => {
    if (!ranked) return new Map<string, number>()
    return new Map(ranked.map((r) => [r.id, r.confidence]))
  }, [ranked])

  // Final filter+rank pipeline:
  //   1. Empty query → all tools, original order.
  //   2. Semantic ranked list available → reorder by rank, drop misses.
  //   3. Semantic failed/pending → local lexical filter (substring) so
  //      offline / 5xx doesn't block the user from finding anything.
  //   4. Category pill restricts the result set after ranking.
  const filtered = useMemo(() => {
    const q = trimmedQuery.toLowerCase()

    let base: Tool[]
    if (!q) {
      base = tools
    } else if (ranked && ranked.length > 0) {
      const byId = new Map(tools.map((t) => [t.id, t]))
      base = ranked
        .map((r) => byId.get(r.id))
        .filter((t): t is Tool => Boolean(t))
    } else if (ranked && ranked.length === 0) {
      base = []
    } else {
      // Pre-response or semantic-failed: lexical fallback
      base = tools.filter(
        (tool) =>
          tool.name.toLowerCase().includes(q) ||
          tool.description.toLowerCase().includes(q) ||
          tool.id.toLowerCase().includes(q) ||
          tool.tags.some((tag) => tag.toLowerCase().includes(q)),
      )
    }

    if (activeCategory) {
      return base.filter((t) => t.category === activeCategory)
    }
    return base
  }, [tools, trimmedQuery, ranked, activeCategory])

  return (
    <div className="w-full">
      {/* Header — single line, no gradient orbs, no oversized icon. The
          live count moves to a quiet status pill on the right. */}
      <div className="mb-5 flex items-end justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Tools</h1>
          <p className="mt-0.5 text-[13px] text-muted-foreground">
            Discover and run any tool in the Sprout registry.
          </p>
        </div>
        {tools.length > 0 && (
          <span className="inline-flex items-center gap-1.5 rounded-full border border-border/60 bg-card/60 px-2.5 py-1 text-[11px] tabular-nums text-muted-foreground">
            <span className="size-1.5 rounded-full bg-emerald-500" />
            {filtered.length === tools.length
              ? `${tools.length} available`
              : `${filtered.length} / ${tools.length}`}
          </span>
        )}
      </div>

      {/* Stats row */}
      {stats && <StatsRow stats={stats} />}
      {systemStatus && <SystemStatusPanel systemStatus={systemStatus} />}

      {/* Unified search: one bar that does both keyword AND natural-language
          discovery. As the user types, the registry's BM25 router ranks
          every tool; the grid below re-orders to match. The "ai-ranked"
          chip on the right makes the mode visible without forcing the user
          to choose. Empty state surfaces sample intents inline. */}
      <div className="mb-6 overflow-hidden rounded-lg border border-border/60 bg-card/40">
        <div className="flex items-center gap-2.5 px-3.5 py-2.5">
          {searching ? (
            <Loader2 className="size-3.5 shrink-0 animate-spin text-muted-foreground/70" />
          ) : (
            <Search className="size-3.5 shrink-0 text-muted-foreground/70" />
          )}
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search by name, tag, or describe what you need…"
            className="flex-1 bg-transparent text-sm text-foreground placeholder:text-muted-foreground/55 focus:outline-none"
            aria-label="Search tools"
          />
          {trimmedQuery && (
            <span
              className={cn(
                "hidden h-5 items-center rounded border px-1.5 font-mono text-[10px] sm:inline-flex",
                ranked && ranked.length > 0
                  ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-300"
                  : semanticFailed
                    ? "border-amber-500/30 bg-amber-500/10 text-amber-300"
                    : "border-border/70 bg-muted/60 text-muted-foreground",
              )}
              title={
                ranked && ranked.length > 0
                  ? "Results ranked by the semantic router"
                  : semanticFailed
                    ? "Semantic router unavailable — using local match"
                    : "Searching"
              }
            >
              {ranked && ranked.length > 0
                ? "ai-ranked"
                : semanticFailed
                  ? "local"
                  : "ranking…"}
            </span>
          )}
          {trimmedQuery && (
            <button
              type="button"
              onClick={() => setSearch("")}
              aria-label="Clear search"
              className="rounded-md p-0.5 text-muted-foreground/70 transition-colors hover:bg-muted/60 hover:text-foreground"
            >
              <X className="size-3.5" />
            </button>
          )}
        </div>

        {/* Sample intents — only when the input is empty. Kept inline so
            the search box isn't pushed off-screen on small viewports. */}
        {!trimmedQuery && (
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1.5 border-t border-border/50 px-3.5 py-2">
            <span className="text-[11px] text-muted-foreground/60">Try</span>
            {[
              "weather for Tokyo",
              "top stories on ycombinator",
              "latest bitcoin price",
              "transcript of a youtube video",
            ].map((s, i, arr) => (
              <button
                key={s}
                type="button"
                onClick={() => setSearch(s)}
                className="text-[11px] text-muted-foreground transition-colors hover:text-foreground"
              >
                {s}
                {i < arr.length - 1 && (
                  <span className="ml-2 text-muted-foreground/30">·</span>
                )}
              </button>
            ))}
          </div>
        )}
      </div>

      {/* Category pills */}
      {categories.length > 0 && (
        <div className="mb-8 flex flex-wrap gap-2">
          <button
            onClick={() => setActiveCategory(null)}
            className={`inline-flex h-7 items-center gap-1.5 rounded-full px-3 text-xs font-medium transition-all duration-300 ${
              activeCategory === null
                ? "bg-primary text-primary-foreground shadow-sm shadow-primary/20 ring-1 ring-primary/50"
                : "bg-card/65 text-muted-foreground ring-1 ring-border/70 hover:bg-card hover:text-foreground hover:ring-border"
            }`}
          >
            <Layers className="size-3" />
            All
          </button>
          {categories.map((cat) => (
            <button
              key={cat}
              onClick={() =>
                setActiveCategory((prev) => (prev === cat ? null : cat))
              }
              className={`inline-flex h-7 items-center rounded-full px-3 text-xs font-medium transition-all duration-300 ${
                activeCategory === cat
                  ? "bg-primary text-primary-foreground shadow-sm shadow-primary/20 ring-1 ring-primary/50"
                  : "bg-card/65 text-muted-foreground ring-1 ring-border/70 hover:bg-card hover:text-foreground hover:ring-border"
              }`}
            >
              {cat}
            </button>
          ))}
        </div>
      )}

      {/* Error state */}
      {error && (
        <div className="flex items-center gap-3 rounded-xl border border-destructive/30 bg-destructive/5 p-4 text-sm text-destructive dark:border-destructive/20 dark:bg-destructive/10">
          <div className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-destructive/10">
            <X className="size-4" />
          </div>
          <p>Failed to load tools: {error}</p>
        </div>
      )}

      {/* Empty state */}
      {!error && filtered.length === 0 && (search || activeCategory) && (
        <EmptyState query={search || activeCategory || ""} />
      )}

      {/* No tools at all */}
      {!error && tools.length === 0 && !search && !activeCategory && (
        <div className="section-surface flex flex-col items-center justify-center gap-6 px-4 py-24 text-center">
          <div className="relative">
            <span className="absolute -inset-6 rounded-full bg-gradient-to-br from-primary/10 to-primary/5 blur-2xl" />
            <div className="relative flex size-20 items-center justify-center rounded-2xl bg-muted/80 ring-1 ring-border/70">
              <Package className="size-10 text-muted-foreground/50" />
            </div>
          </div>
          <div className="max-w-sm space-y-2">
            <h3 className="text-lg font-semibold">No tools registered yet</h3>
            <p className="text-sm leading-relaxed text-muted-foreground">
              Be the first to publish a tool to the Sprout registry.
            </p>
          </div>
          <Link href="/publish">
            <Button size="sm" className="mt-2 gap-1.5">
              <Sparkles className="size-3.5" />
              Publish a tool
            </Button>
          </Link>
        </div>
      )}

      {/* Tool grid */}
      {filtered.length > 0 && (
        <div className="grid grid-cols-1 gap-6 md:grid-cols-2 lg:grid-cols-3">
          {filtered.map((tool) => (
            <ToolCard
              key={tool.id}
              tool={tool}
              confidence={confidenceById.get(tool.id)}
            />
          ))}
        </div>
      )}
    </div>
  )
}
