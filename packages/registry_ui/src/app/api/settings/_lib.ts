import { NextResponse } from "next/server"
import { auth } from "@clerk/nextjs/server"

import { getRegistryUrl } from "@/lib/service-urls"

async function getBackendAuthHeader() {
  const { isAuthenticated, getToken } = await auth()
  if (!isAuthenticated) {
    return null
  }

  const token = await getToken()
  if (!token) {
    return null
  }

  return { Authorization: `Bearer ${token}` }
}

export async function proxyRegistryRequest(
  path: string,
  init?: RequestInit,
) {
  const authHeader = await getBackendAuthHeader()
  if (!authHeader) {
    return NextResponse.json({ detail: "Unauthorized" }, { status: 401 })
  }

  const response = await fetch(`${getRegistryUrl()}${path}`, {
    ...init,
    headers: {
      ...authHeader,
      ...(init?.headers || {}),
    },
    cache: "no-store",
  })

  if (response.status === 204) {
    return new NextResponse(null, { status: 204 })
  }

  const contentType = response.headers.get("content-type") || ""
  const body = contentType.includes("application/json")
    ? await response.json().catch(() => ({ detail: "Invalid JSON response" }))
    : { detail: await response.text() }

  return NextResponse.json(body, { status: response.status })
}
