export function getRegistryUrl() {
  if (typeof window === "undefined") {
    return process.env.REGISTRY_API_INTERNAL || process.env.NEXT_PUBLIC_REGISTRY_URL || "http://localhost:8766"
  }

  return process.env.NEXT_PUBLIC_REGISTRY_URL || "http://localhost:8766"
}

export function getChatBackendUrl() {
  if (typeof window === "undefined") {
    return process.env.CHAT_BACKEND_INTERNAL || process.env.NEXT_PUBLIC_CHAT_BACKEND || "http://localhost:8765"
  }

  return process.env.NEXT_PUBLIC_CHAT_BACKEND || "http://localhost:8765"
}

export function getMcpServerUrl() {
  if (typeof window === "undefined") {
    return process.env.MCP_SERVER_INTERNAL || process.env.NEXT_PUBLIC_MCP_SERVER_URL || "http://localhost:8768"
  }

  return process.env.NEXT_PUBLIC_MCP_SERVER_URL || "http://localhost:8768"
}

export function getSynthesisServiceUrl() {
  if (typeof window === "undefined") {
    return process.env.SYNTHESIS_SERVICE_INTERNAL || process.env.NEXT_PUBLIC_SYNTHESIS_SERVICE_URL || "http://localhost:8002"
  }

  return process.env.NEXT_PUBLIC_SYNTHESIS_SERVICE_URL || "http://localhost:8002"
}

export function getToolExecutorUrl() {
  if (typeof window === "undefined") {
    return process.env.TOOL_EXECUTOR_INTERNAL || process.env.NEXT_PUBLIC_TOOL_EXECUTOR_URL || "http://localhost:8767"
  }

  return process.env.NEXT_PUBLIC_TOOL_EXECUTOR_URL || "http://localhost:8767"
}
