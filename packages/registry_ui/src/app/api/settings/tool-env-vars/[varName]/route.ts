import { proxyRegistryRequest } from "@/app/api/settings/_lib"

export async function DELETE(
  _req: Request,
  { params }: { params: Promise<{ varName: string }> },
) {
  const { varName } = await params
  return proxyRegistryRequest(`/auth/tool-env-vars/${encodeURIComponent(varName)}`, {
    method: "DELETE",
  })
}
