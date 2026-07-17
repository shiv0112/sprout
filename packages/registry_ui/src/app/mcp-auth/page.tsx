"use client";

import { useAuth } from "@clerk/nextjs";
import { useSearchParams, useRouter } from "next/navigation";
import { useEffect, Suspense } from "react";

function McpAuthInner() {
  const { isSignedIn, isLoaded, getToken } = useAuth();
  const searchParams = useSearchParams();
  const router = useRouter();
  const callback = searchParams.get("callback");

  useEffect(() => {
    if (!isLoaded || !callback) return;

    if (!isSignedIn) {
      const returnUrl = `/mcp-auth?callback=${encodeURIComponent(callback)}`;
      router.push(`/sign-in?redirect_url=${encodeURIComponent(returnUrl)}`);
      return;
    }

    getToken().then((token) => {
      if (token) {
        const sep = callback.includes("?") ? "&" : "?";
        window.location.href = `${callback}${sep}__clerk_session_token=${token}`;
      }
    });
  }, [isLoaded, isSignedIn, callback, getToken, router]);

  if (!callback) {
    return <div className="flex items-center justify-center min-h-screen text-white">Missing callback parameter.</div>;
  }

  return (
    <div className="flex items-center justify-center min-h-screen text-white">
      {isSignedIn ? "Authenticating with MCP server..." : "Redirecting to sign in..."}
    </div>
  );
}

export default function McpAuthPage() {
  return (
    <Suspense fallback={<div className="flex items-center justify-center min-h-screen text-white">Loading...</div>}>
      <McpAuthInner />
    </Suspense>
  );
}
