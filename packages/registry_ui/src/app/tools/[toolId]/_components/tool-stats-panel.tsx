"use client"

/**
 * Client-side stats panel for the tool detail page.
 *
 * - Renders the seed stats from the server-fetched ``Tool.stats`` (no
 *   loading flicker on first paint).
 * - Re-fetches in the background so the numbers stay live as the user
 *   interacts (TanStack Query, 30s refetch interval).
 * - Favorite button POSTs to /tools/{id}/favorite with the user's Clerk
 *   token; optimistic update for immediate feedback, rollback on error.
 *
 * Designed to be self-contained: drop into a server component, give it
 * the toolId and the seed stats, and forget about it.
 */

import { useCallback, useEffect, useState } from "react"
import { useAuth } from "@clerk/nextjs"
import { useQuery } from "@tanstack/react-query"
import {
  Activity,
  CheckCircle2,
  Clock,
  PlayCircle,
  Star,
  Timer,
  TrendingUp,
  XCircle,
} from "lucide-react"

import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import {
  fetchToolExecutionStats,
  toggleToolFavorite,
  type ToolExecutionStats,
} from "@/lib/registry"

interface Props {
  toolId: string
  /** Stats already fetched on the server. May be undefined for legacy
   *  registry responses without iter-40 stats; we render zeros in that
   *  case until the client-side query resolves. */
  seedStats?: ToolExecutionStats
}

const ZERO_STATS: ToolExecutionStats = {
  execution_count: 0,
  success_count: 0,
  error_count: 0,
  success_rate: null,
  avg_duration_ms: 0,
  last_executed_at: null,
  last_status: "never",
  favorite_count: 0,
}

function formatCount(n: number): string {
  if (n >= 1000) return `${(n / 1000).toFixed(1).replace(/\.0$/, "")}k`
  return String(n)
}

function formatDuration(ms: number): string {
  if (ms === 0) return "—"
  if (ms < 1000) return `${Math.round(ms)} ms`
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)} s`
  return `${(ms / 60_000).toFixed(1)} min`
}

function relativeTime(iso: string | null): string {
  if (!iso) return "never"
  const ts = new Date(iso).getTime()
  if (Number.isNaN(ts)) return "never"
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

export function ToolStatsPanel({ toolId, seedStats }: Props) {
  const { getToken } = useAuth()

  const { data: liveStats } = useQuery({
    queryKey: ["tool-stats", toolId],
    queryFn: () => fetchToolExecutionStats(toolId),
    initialData: seedStats ? { tool_id: toolId, ...seedStats } : undefined,
    refetchInterval: 30_000,
    refetchOnWindowFocus: true,
  })

  const stats: ToolExecutionStats = liveStats ?? seedStats ?? ZERO_STATS

  // Optimistic favorite state. Reset whenever the live query produces a
  // new authoritative count so server truth always wins eventually. The
  // setState-in-effect is intentional here — it's how we sync derived
  // local state with an external query, not a derived render computation.
  const [favCount, setFavCount] = useState(stats.favorite_count)
  const [isFavorited, setIsFavorited] = useState(false)
  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    setFavCount(stats.favorite_count)
  }, [stats.favorite_count])
  /* eslint-enable react-hooks/set-state-in-effect */

  const handleFavorite = useCallback(async () => {
    const next = !isFavorited
    const delta = next ? 1 : -1
    setIsFavorited(next)
    setFavCount((c) => Math.max(0, c + delta))
    try {
      const token = (await getToken()) ?? undefined
      const res = await toggleToolFavorite(toolId, delta, token)
      setFavCount(res.favorite_count)
    } catch (err) {
      console.error("Favorite failed:", err)
      // Rollback
      setIsFavorited(!next)
      setFavCount((c) => Math.max(0, c - delta))
    }
  }, [getToken, isFavorited, toolId])

  const total = stats.execution_count
  const successRatePct =
    stats.success_rate !== null ? Math.round(stats.success_rate * 100) : null

  let successBadge = "bg-muted/40 text-muted-foreground"
  if (successRatePct !== null) {
    if (successRatePct >= 90) successBadge = "bg-emerald-500/15 text-emerald-300 ring-emerald-500/30"
    else if (successRatePct >= 70) successBadge = "bg-amber-500/15 text-amber-300 ring-amber-500/30"
    else successBadge = "bg-red-500/15 text-red-300 ring-red-500/30"
  }

  return (
    <Card className="section-surface mb-8 border-0 py-0">
      <div className="flex flex-col gap-5 p-5">
        {/* Header row: title + favorite */}
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <Activity className="size-4 text-muted-foreground/80" />
            <h2 className="text-sm font-semibold uppercase tracking-wider text-muted-foreground/80">
              Usage statistics
            </h2>
          </div>
          <Button
            onClick={handleFavorite}
            size="sm"
            variant={isFavorited ? "default" : "outline"}
            className={`gap-1.5 transition-all ${
              isFavorited
                ? "bg-amber-500/90 text-amber-50 hover:bg-amber-500"
                : "border-amber-500/30 text-amber-300 hover:border-amber-500/50 hover:bg-amber-500/10"
            }`}
          >
            <Star
              className={`size-3.5 ${isFavorited ? "fill-current" : ""}`}
            />
            {formatCount(favCount)}
          </Button>
        </div>

        {/* Stat tiles */}
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <StatTile
            icon={<PlayCircle className="size-4 text-primary/80" />}
            label="Total runs"
            value={formatCount(total)}
            sublabel={total > 0 ? `${stats.success_count} ✓ / ${stats.error_count} ✗` : "Never run"}
          />
          <StatTile
            icon={<TrendingUp className="size-4 text-primary/80" />}
            label="Success rate"
            value={successRatePct !== null ? `${successRatePct}%` : "—"}
            badgeClass={successBadge}
            sublabel={total > 0 ? `over ${total} runs` : "no data yet"}
          />
          <StatTile
            icon={<Timer className="size-4 text-primary/80" />}
            label="Avg latency"
            value={formatDuration(stats.avg_duration_ms)}
            sublabel="per execution"
          />
          <StatTile
            icon={<Clock className="size-4 text-primary/80" />}
            label="Last run"
            value={relativeTime(stats.last_executed_at)}
            sublabel={
              stats.last_status === "never"
                ? "Try it in the playground"
                : stats.last_status === "success"
                  ? "✓ succeeded"
                  : "✗ failed"
            }
            sublabelClass={
              stats.last_status === "success"
                ? "text-emerald-400/80"
                : stats.last_status === "error"
                  ? "text-red-400/80"
                  : ""
            }
          />
        </div>
      </div>
    </Card>
  )
}

function StatTile({
  icon,
  label,
  value,
  sublabel,
  badgeClass,
  sublabelClass,
}: {
  icon: React.ReactNode
  label: string
  value: string
  sublabel: string
  badgeClass?: string
  sublabelClass?: string
}) {
  return (
    <div className="flex flex-col gap-1 rounded-xl border border-border/60 bg-background/40 px-3.5 py-3 ring-1 ring-border/40">
      <div className="flex items-center gap-1.5">
        {icon}
        <span className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground/70">
          {label}
        </span>
      </div>
      <div
        className={`text-2xl font-semibold tabular-nums ${
          badgeClass
            ? `inline-flex w-fit items-center rounded-md px-2 py-0.5 text-base ring-1 ${badgeClass}`
            : "text-foreground"
        }`}
      >
        {value === "✓" ? <CheckCircle2 className="size-5" /> : value === "✗" ? <XCircle className="size-5" /> : value}
      </div>
      <span className={`text-[11px] text-muted-foreground/70 ${sublabelClass ?? ""}`}>
        {sublabel}
      </span>
    </div>
  )
}
