import { proxyRegistryRequest } from "@/app/api/settings/_lib"

export async function POST() {
  return proxyRegistryRequest("/auth/api-key/regenerate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
  })
}
