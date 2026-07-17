import { proxyRegistryRequest } from "@/app/api/settings/_lib"

export async function GET() {
  return proxyRegistryRequest("/auth/api-key")
}

export async function POST() {
  return proxyRegistryRequest("/auth/api-key", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
  })
}
