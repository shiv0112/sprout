import Link from "next/link"
import { notFound } from "next/navigation"
import { ArrowLeft, User, Play, Clock, Tag, GitBranch } from "lucide-react"

import { fetchTool, fetchToolVersions } from "@/lib/registry"
import type { Tool } from "@/lib/registry"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Separator } from "@/components/ui/separator"
import { ToolDetailTabs } from "@/app/_components/tool-detail-tabs"
import { ToolStatsPanel } from "./_components/tool-stats-panel"

export default async function ToolDetailPage({
  params,
}: {
  params: Promise<{ toolId: string }>
}) {
  const { toolId } = await params

  let tool: Tool
  let versions: { version: string; description: string; author: string }[] = []

  try {
    const [toolData, versionsData] = await Promise.all([
      fetchTool(toolId),
      fetchToolVersions(toolId).catch(() => ({ versions: [], count: 0, tool_id: toolId })),
    ])
    tool = toolData
    versions = versionsData.versions
  } catch {
    notFound()
  }

  return (
    <div className="mx-auto w-full max-w-5xl">
      {/* Back button */}
      <Link href="/tools">
        <Button variant="ghost" size="sm" className="mb-6 -ml-2 gap-1.5 text-muted-foreground hover:text-foreground">
          <ArrowLeft className="size-4" />
          Back to Registry
        </Button>
      </Link>

      {/* Hero section */}
      <div className="hero-surface mb-8">
        {/* Background gradient orbs */}
        <div className="pointer-events-none absolute -left-16 -top-16 size-48 rounded-full bg-gradient-to-br from-primary/[0.14] to-primary/[0.03] blur-3xl" />
        <div className="pointer-events-none absolute -bottom-12 -right-12 size-36 rounded-full bg-gradient-to-br from-violet-500/[0.1] to-blue-500/[0.03] blur-3xl" />

        <div className="relative space-y-5">
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="text-3xl font-bold tracking-tight sm:text-4xl">
              {tool.name}
            </h1>
            <Badge
              variant="outline"
              className="border-border/70 bg-card/70 font-mono text-sm"
            >
              v{tool.version}
            </Badge>
            <Badge
              variant="secondary"
              className="bg-primary/10 text-primary"
            >
              {tool.category}
            </Badge>
          </div>

          <p className="max-w-2xl text-base leading-relaxed text-muted-foreground">
            {tool.description}
          </p>

          <div className="flex flex-wrap items-center gap-x-5 gap-y-2 text-sm text-muted-foreground">
            <span className="flex items-center gap-1.5">
              <User className="size-3.5" />
              By{" "}
              <span className="font-medium text-foreground">{tool.author}</span>
            </span>
            {tool.tags.length > 0 && (
              <>
                <Separator orientation="vertical" className="h-4" />
                <div className="flex flex-wrap items-center gap-1.5">
                  <Tag className="size-3.5 text-muted-foreground/60" />
                  {tool.tags.map((tag) => (
                    <Badge
                      key={tag}
                      variant="outline"
                      className="border-border/70 bg-card/60 text-xs"
                    >
                      {tag}
                    </Badge>
                  ))}
                </div>
              </>
            )}
          </div>

          {/* Try it button */}
          <div className="flex items-center gap-3 pt-1">
            <a href="#playground">
              <Button
                size="sm"
                className="gap-1.5 bg-gradient-to-r from-primary to-primary/80 shadow-sm transition-all duration-300 hover:shadow-md hover:shadow-primary/20"
              >
                <Play className="size-3.5" />
                Try it in Playground
              </Button>
            </a>
          </div>
        </div>
      </div>

      {/* Live usage stats panel — clickable favorite, success rate, latency, last run */}
      <ToolStatsPanel toolId={tool.id} seedStats={tool.stats} />

      {/* Version history */}
      {versions.length > 1 && (
        <Card className="section-surface mb-8 border-0 py-0">
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <GitBranch className="size-4 text-muted-foreground" />
              Version History
              <Badge variant="secondary" className="ml-1 text-xs">
                {versions.length}
              </Badge>
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-0">
              {versions.map((v, index) => (
                <div
                  key={v.version}
                  className={`flex items-center gap-4 py-3 text-sm ${
                    index !== versions.length - 1
                      ? "border-b border-border/60"
                      : ""
                  }`}
                >
                  {/* Timeline dot */}
                  <div className="flex flex-col items-center">
                    <div
                      className={`size-2.5 rounded-full ${
                        index === 0
                          ? "bg-emerald-500 shadow-sm shadow-emerald-500/30"
                          : "bg-muted-foreground/30"
                      }`}
                    />
                  </div>

                  {/* Version info */}
                  <div className="flex flex-1 items-center justify-between gap-4">
                    <div className="flex items-center gap-3">
                      <span className="font-mono text-xs font-medium text-foreground">
                        v{v.version}
                      </span>
                      {index === 0 && (
                        <Badge variant="secondary" className="text-[10px] bg-emerald-500/10 text-emerald-400">
                          latest
                        </Badge>
                      )}
                      {v.description && (
                        <span className="text-xs text-muted-foreground">
                          {v.description.length > 60
                            ? v.description.slice(0, 60) + "..."
                            : v.description}
                        </span>
                      )}
                    </div>
                    <div className="flex items-center gap-1.5 text-xs text-muted-foreground/70">
                      <Clock className="size-3" />
                      <span>{v.author}</span>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      {/* Tabs (client component for interactivity) */}
      <ToolDetailTabs
        toolId={tool.id}
        params={tool.params}
        toolDef={tool.tool_def}
      />
    </div>
  )
}
