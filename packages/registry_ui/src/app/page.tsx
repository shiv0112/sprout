import { fetchTools } from "@/lib/registry"
import type { Tool } from "@/lib/registry"
import { LandingClient } from "@/app/_components/landing-client"

export const dynamic = "force-dynamic"
export const revalidate = 0

export default async function LandingPage() {
  let tools: Tool[] = []

  try {
    tools = await fetchTools()
  } catch {
    /* registry unreachable — render with placeholders so the page still ships */
  }

  return <LandingClient tools={tools} toolCount={tools.length} />
}
