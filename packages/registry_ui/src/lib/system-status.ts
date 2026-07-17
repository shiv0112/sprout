import {
  getChatBackendUrl,
  getMcpServerUrl,
  getRegistryUrl,
  getSynthesisServiceUrl,
  getToolExecutorUrl,
} from "@/lib/service-urls"

export interface ServiceStatus {
  key: string
  label: string
  status: "ok" | "degraded" | "unreachable"
  detail?: string
  optional?: boolean
  httpStatus?: number
}

export interface SystemStatus {
  status: "ok" | "degraded"
  services: ServiceStatus[]
}

async function probeService(
  key: string,
  label: string,
  url: string,
  successDetail: string,
  fetchOpts?: RequestInit,
  optional?: boolean,
): Promise<ServiceStatus> {
  try {
    const resp = await fetch(url, { ...fetchOpts, signal: AbortSignal.timeout(3000) })
    if (resp.ok) {
      return { key, label, status: "ok", detail: successDetail, httpStatus: resp.status, optional }
    }
    return {
      key,
      label,
      status: "degraded",
      detail: `HTTP ${resp.status}`,
      httpStatus: resp.status,
      optional,
    }
  } catch {
    return { key, label, status: "unreachable", detail: "Connection failed", optional }
  }
}

export async function getSystemStatus(
  fetchOpts?: RequestInit,
): Promise<SystemStatus> {
  const registryUrl = getRegistryUrl()
  const chatBackendUrl = getChatBackendUrl()
  const mcpServerUrl = getMcpServerUrl()
  const synthesisServiceUrl = getSynthesisServiceUrl()
  const toolExecutorUrl = getToolExecutorUrl()

  const services = await Promise.all([
    probeService("registry", "Registry API", `${registryUrl}/readyz`, "Ready", fetchOpts),
    probeService("chat", "Chat Backend", `${chatBackendUrl}/readyz`, "Ready", fetchOpts),
    probeService("mcp", "MCP Server", `${mcpServerUrl}/readyz`, "Ready", fetchOpts),
    probeService("executor", "Tool Executor", `${toolExecutorUrl}/health`, "Ready", fetchOpts),
    probeService("synthesis", "Synthesis Service", `${synthesisServiceUrl}/readyz`, "Ready", fetchOpts, true),
  ])

  const overall = services.every((s) => s.status === "ok" || s.optional) ? "ok" : "degraded"
  return { status: overall, services }
}
