"use client"

import { type ReactNode, useEffect, useState } from "react"
import { ClerkProvider } from "@clerk/nextjs"
import { dark } from "@clerk/themes"

/**
 * Wraps ClerkProvider so its widgets (UserButton menu, sign-in modal, account
 * page) follow the app's botanical-pigment theme instead of being locked dark.
 *   dark  → Clerk `dark` base theme, woad-indigo accent
 *   light → Clerk default (light) base theme, madder-red accent (Almanac)
 * Tracks the `dark` class on <html>, which the theme toggle flips.
 */
export function ClerkAppearanceProvider({ children }: { children: ReactNode }) {
  // SSR default is dark (matches the <html class="dark"> default), so start dark
  // to avoid a hydration mismatch; the effect corrects it on the client.
  const [isDark, setIsDark] = useState(true)

  useEffect(() => {
    const root = document.documentElement
    const sync = () => setIsDark(root.classList.contains("dark"))
    sync()
    const observer = new MutationObserver(sync)
    observer.observe(root, { attributes: true, attributeFilter: ["class"] })
    return () => observer.disconnect()
  }, [])

  return (
    <ClerkProvider
      appearance={{
        baseTheme: isDark ? dark : undefined,
        variables: {
          colorPrimary: isDark ? "#5b74d6" : "#b23a2e",
        },
      }}
    >
      {children}
    </ClerkProvider>
  )
}
