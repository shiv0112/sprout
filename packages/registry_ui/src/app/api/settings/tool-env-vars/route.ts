import { proxyRegistryRequest } from "@/app/api/settings/_lib"

export async function GET() {
  return proxyRegistryRequest("/auth/tool-env-vars")
}

export async function PUT(req: Request) {
  return proxyRegistryRequest("/auth/tool-env-vars", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: await req.text(),
  })
}
