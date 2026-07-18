"use client"

import { useMemo } from "react"
import {
  CheckCircle2,
  Circle,
  Loader2,
  AlertCircle,
  ArrowRight,
  Wrench,
  Sparkles,
} from "lucide-react"

// ── Types ──────────────────────────────────────────────────────────────────

export interface DagNode {
  id: string
  role: string
  task: string
  tools: string[]
}

export interface DagPlan {
  task: string
  nodes: DagNode[]
  edges: [string, string][]
  entry_nodes: string[]
  exit_node: string
  missing_tools?: Array<{ id: string; description: string }>
}

export type NodeStatus = "pending" | "running" | "complete" | "error"

// ── Component ──────────────────────────────────────────────────────────────

interface SproutDagProps {
  plan: DagPlan | null
  nodeStatuses: Record<string, NodeStatus>
  nodeResults: Record<string, string>
}

const STATUS_ICON: Record<NodeStatus, React.ReactNode> = {
  pending: <Circle className="size-3 text-muted-foreground/40" />,
  running: <Loader2 className="size-3 animate-spin text-primary" />,
  complete: <CheckCircle2 className="size-3 text-emerald-400" />,
  error: <AlertCircle className="size-3 text-red-400" />,
}

const STATUS_RING: Record<NodeStatus, string> = {
  pending: "ring-border/50",
  running: "ring-primary/60 shadow-lg shadow-primary/15",
  complete: "ring-emerald-500/40",
  error: "ring-red-500/40",
}

export function SproutDag({ plan, nodeStatuses, nodeResults }: SproutDagProps) {
  // Topological layers for visual layout. Hook must run unconditionally
  // (Rules of Hooks), so handle the null/empty case inside the memo and
  // return null below after all hooks have run.
  const layers = useMemo(() => {
    if (!plan || !plan.nodes.length) return []

    const inDegree: Record<string, number> = {}
    const adj: Record<string, string[]> = {}
    for (const n of plan.nodes) {
      inDegree[n.id] = 0
      adj[n.id] = []
    }
    for (const [from, to] of plan.edges) {
      adj[from]?.push(to)
      if (to in inDegree) inDegree[to]++
    }

    const result: DagNode[][] = []
    let queue = plan.nodes.filter((n) => inDegree[n.id] === 0)

    while (queue.length > 0) {
      result.push(queue)
      const next: DagNode[] = []
      for (const node of queue) {
        for (const neighbor of adj[node.id] || []) {
          inDegree[neighbor]--
          if (inDegree[neighbor] === 0) {
            const nNode = plan.nodes.find((n) => n.id === neighbor)
            if (nNode) next.push(nNode)
          }
        }
      }
      queue = next
    }
    return result
  }, [plan])

  if (!plan) return null

  return (
    <div className="space-y-3">
      {/* Header */}
      <div className="flex items-center gap-2 text-xs font-medium uppercase tracking-wider text-muted-foreground/70">
        <Sparkles className="size-3.5" />
        <span>Execution Plan</span>
        <span className="text-muted-foreground/40">
          {plan.nodes.length} nodes
        </span>
      </div>

      {/* Layers */}
      <div className="space-y-2">
        {layers.map((layer, layerIdx) => (
          <div key={layerIdx} className="flex flex-col gap-2">
            {/* Arrow between layers */}
            {layerIdx > 0 && (
              <div className="flex justify-center py-0.5">
                <ArrowRight className="size-3.5 rotate-90 text-muted-foreground/30" />
              </div>
            )}

            {/* Nodes in this layer */}
            <div className="flex flex-wrap gap-2">
              {layer.map((node) => {
                const status = nodeStatuses[node.id] || "pending"
                return (
                  <div
                    key={node.id}
                    className={`flex-1 min-w-[180px] rounded-lg border bg-card/70 p-3 ring-1 transition-all duration-300 ${STATUS_RING[status]}`}
                  >
                    <div className="flex items-start gap-2">
                      <div className="mt-0.5 shrink-0">{STATUS_ICON[status]}</div>
                      <div className="min-w-0 flex-1">
                        <p className="text-xs font-semibold text-foreground truncate">
                          {node.role}
                        </p>
                        <p className="mt-0.5 text-[11px] leading-snug text-muted-foreground line-clamp-2">
                          {node.task}
                        </p>
                        {node.tools.length > 0 && (
                          <div className="mt-1.5 flex flex-wrap gap-1">
                            {node.tools.map((t) => (
                              <span
                                key={t}
                                className="inline-flex items-center gap-0.5 rounded-md bg-muted/50 px-1.5 py-0.5 text-[10px] text-muted-foreground"
                              >
                                <Wrench className="size-2.5" />
                                {t.split(".").pop()}
                              </span>
                            ))}
                          </div>
                        )}
                        {/* Result preview */}
                        {nodeResults[node.id] && (
                          <p className="mt-1.5 rounded bg-muted/30 px-2 py-1 text-[10px] text-muted-foreground line-clamp-2 font-mono">
                            {nodeResults[node.id]}
                          </p>
                        )}
                      </div>
                    </div>
                  </div>
                )
              })}
            </div>
          </div>
        ))}
      </div>

      {/* Missing tools */}
      {plan.missing_tools && plan.missing_tools.length > 0 && (
        <div className="rounded-lg border border-amber-500/30 bg-amber-500/5 p-3">
          <p className="text-xs font-medium text-amber-400">
            Synthesizing {plan.missing_tools.length} missing tool
            {plan.missing_tools.length > 1 ? "s" : ""}...
          </p>
          <div className="mt-1.5 space-y-1">
            {plan.missing_tools.map((t) => (
              <div key={t.id} className="flex items-center gap-2 text-[11px] text-amber-400/70">
                <Loader2 className="size-3 animate-spin" />
                <span className="font-mono">{t.id.split(".").pop()}</span>
                <span className="text-amber-400/50">— {t.description}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
