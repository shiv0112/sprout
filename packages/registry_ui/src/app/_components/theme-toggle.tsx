"use client"

import { useEffect, useState } from "react"
import { Moon, Sun } from "lucide-react"

import { Button } from "@/components/ui/button"

type Theme = "dark" | "light"

/**
 * Toggles the botanical-pigment themes:
 *   dark  → "Woad & Marigold"
 *   light → "Almanac"
 * The active theme is the presence/absence of the `dark` class on <html>,
 * persisted to localStorage. An inline script in the root layout applies the
 * stored choice before paint, so this component only reflects + flips it.
 */
export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme | null>(null)

  useEffect(() => {
    const isDark = document.documentElement.classList.contains("dark")
    setTheme(isDark ? "dark" : "light")
  }, [])

  function toggle() {
    const next: Theme = theme === "dark" ? "light" : "dark"
    document.documentElement.classList.toggle("dark", next === "dark")
    try {
      localStorage.setItem("theme", next)
    } catch {
      /* storage unavailable — session-only toggle still works */
    }
    setTheme(next)
  }

  return (
    <Button
      variant="outline"
      size="icon-sm"
      onClick={toggle}
      aria-label={theme === "light" ? "Switch to dark theme" : "Switch to light theme"}
      className="rounded-lg"
    >
      {/* pre-mount (theme === null) matches the SSR default (dark → show Sun) */}
      {theme === "light" ? <Moon className="size-4" /> : <Sun className="size-4" />}
      <span className="sr-only">Toggle theme</span>
    </Button>
  )
}
