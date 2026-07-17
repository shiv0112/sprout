"use client"

import { memo, useCallback, useMemo, useState } from "react"
import { ArrowUpRight, Check, Copy, ExternalLink, ShieldAlert } from "lucide-react"

import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { cn } from "@/lib/utils"

type MarkdownLinkProps = React.AnchorHTMLAttributes<HTMLAnchorElement> & {
  node?: unknown
}

const SAFE_PROTOCOLS = new Set(["http:", "https:", "mailto:", "tel:"])

function parseUrl(href: string | undefined): { url: URL | null; display: string; host: string } {
  if (!href) return { url: null, display: "", host: "" }
  try {
    const u = new URL(href, typeof window === "undefined" ? "https://placeholder.local" : window.location.href)
    return { url: u, display: href, host: u.host }
  } catch {
    return { url: null, display: href, host: "" }
  }
}

export const MarkdownLink = memo(function MarkdownLink({
  href,
  children,
  className,
  node: _node,
  ...rest
}: MarkdownLinkProps) {
  const [open, setOpen] = useState(false)
  const [copied, setCopied] = useState(false)

  const { url, display, host } = useMemo(() => parseUrl(href), [href])
  const isSafe = !!url && SAFE_PROTOCOLS.has(url.protocol)
  const isExternal =
    isSafe &&
    typeof window !== "undefined" &&
    url!.protocol.startsWith("http") &&
    url!.host !== window.location.host

  const handleClick = useCallback(
    (event: React.MouseEvent<HTMLAnchorElement>) => {
      if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return
      if (!isExternal) return
      event.preventDefault()
      setOpen(true)
    },
    [isExternal],
  )

  const handleCopy = useCallback(async () => {
    if (!href) return
    try {
      await navigator.clipboard.writeText(href)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1400)
    } catch {
      /* clipboard blocked — silently noop */
    }
  }, [href])

  const handleOpen = useCallback(() => {
    if (!href) return
    window.open(href, "_blank", "noopener,noreferrer")
    setOpen(false)
  }, [href])

  const safetyClass = isSafe
    ? "text-primary decoration-primary/40 hover:decoration-primary"
    : "text-destructive decoration-destructive/40 hover:decoration-destructive"

  return (
    <>
      <a
        {...rest}
        href={href}
        target={isExternal ? "_blank" : rest.target}
        rel={isExternal ? "noopener noreferrer" : rest.rel}
        onClick={handleClick}
        className={cn(
          "inline underline decoration-1 underline-offset-2 transition-colors",
          safetyClass,
          className,
        )}
      >
        {children}
      </a>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-w-md gap-0 overflow-hidden border border-white/8 bg-[linear-gradient(180deg,#181b27_0%,#10131d_100%)] p-0 shadow-[0_24px_64px_-12px_rgba(0,0,0,0.6)]">
          <DialogHeader className="space-y-2 border-b border-white/5 px-5 pb-4 pt-5 text-left">
            <div className="flex items-center gap-2">
              <span
                className={cn(
                  "flex size-7 items-center justify-center rounded-lg",
                  isSafe ? "bg-primary/12 text-primary" : "bg-destructive/15 text-destructive",
                )}
              >
                <ShieldAlert className="size-3.5" />
              </span>
              <DialogTitle className="text-[13px] font-semibold tracking-tight text-foreground">
                Open external link
              </DialogTitle>
            </div>
            <DialogDescription className="text-[11.5px] leading-relaxed text-muted-foreground/80">
              You&rsquo;re about to leave Kiln and open a link on{" "}
              <span className="font-medium text-foreground/85">{host || "an unknown host"}</span>.
            </DialogDescription>
          </DialogHeader>

          <div className="px-5 pb-4 pt-4">
            <div className="group relative overflow-hidden rounded-lg border border-white/8 bg-black/30 px-3 py-2.5">
              <div className="text-[10px] font-medium uppercase tracking-[0.16em] text-muted-foreground/45">
                Destination
              </div>
              <div className="mt-1 break-all font-mono text-[11.5px] leading-relaxed text-foreground/85">
                {display}
              </div>
              {!isSafe && (
                <div className="mt-2 text-[11px] text-destructive/85">
                  This URL uses an unrecognised protocol. Open with caution.
                </div>
              )}
            </div>
          </div>

          <DialogFooter className="flex-row gap-2 border-t border-white/5 bg-black/20 px-5 pb-5 pt-4">
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => setOpen(false)}
              className="h-9 flex-1 text-[12px] text-muted-foreground hover:text-foreground"
            >
              Cancel
            </Button>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={handleCopy}
              className="h-9 gap-1.5 border-white/10 bg-white/[0.03] text-[12px] text-foreground/85 hover:bg-white/[0.06]"
            >
              {copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}
              {copied ? "Copied" : "Copy"}
            </Button>
            <Button
              type="button"
              size="sm"
              onClick={handleOpen}
              className="h-9 flex-1 gap-1.5 bg-primary text-[12px] font-semibold text-primary-foreground shadow-[0_8px_22px_hsl(26_92%_58%_/_0.22)] hover:bg-primary/92"
            >
              <ExternalLink className="size-3.5" />
              Open
              <ArrowUpRight className="size-3 opacity-80" />
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
})
