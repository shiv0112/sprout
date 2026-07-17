"use client"

import Link from "next/link"
import { usePathname } from "next/navigation"
import { LayoutGrid, MessageSquare, Upload, Settings } from "lucide-react"
import type { LucideIcon } from "lucide-react"

import { cn } from "@/lib/utils"

const iconMap: Record<string, LucideIcon> = {
  LayoutGrid,
  MessageSquare,
  Upload,
  Settings,
}

interface NavItem {
  href: string
  label: string
  icon: string
}

export function NavLinks({ items }: { items: NavItem[] }) {
  const pathname = usePathname()

  return (
    <nav className="flex items-center gap-1 rounded-xl border border-border/60 bg-card/50 p-1">
      {items.map(({ href, label, icon }) => {
        const isActive = href === "/" ? pathname === "/" : pathname.startsWith(href)
        const Icon = iconMap[icon]

        return (
          <Link
            key={href}
            href={href}
            className={cn(
              "inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-[13px] font-medium transition-all duration-200",
              isActive
                ? "bg-primary/15 text-foreground shadow-sm ring-1 ring-primary/30"
                : "text-muted-foreground hover:bg-muted/70 hover:text-foreground"
            )}
          >
            {Icon && (
              <Icon
                className={cn(
                  "size-3.5",
                  isActive ? "text-primary" : "text-muted-foreground"
                )}
              />
            )}
            <span className="hidden sm:inline">{label}</span>
          </Link>
        )
      })}
    </nav>
  )
}
