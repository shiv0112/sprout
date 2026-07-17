import { fetchTools, fetchToolStats } from "@/lib/registry"
import type { Tool, ToolStats } from "@/lib/registry"
import { CatalogClient } from "@/app/_components/catalog-client"

export const dynamic = "force-dynamic"
export const revalidate = 0

export default async function CatalogPage() {
  let tools: Tool[] = []
  let stats: ToolStats | null = null
  let error: string | null = null

  try {
    ;[tools, stats] = await Promise.all([fetchTools(), fetchToolStats()])
  } catch (e) {
    error = e instanceof Error ? e.message : "Failed to load tools"
  }

  return <CatalogClient tools={tools} stats={stats} systemStatus={null} error={error} />
}
