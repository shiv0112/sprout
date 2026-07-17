"use client"

import { AnimatePresence, motion } from "framer-motion"
import { Download, Expand, ImageOff, Sparkles } from "lucide-react"
import { memo, useCallback, useMemo, useState } from "react"

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { cn } from "@/lib/utils"

import { ATT_URL_PREFIX, useAttachment } from "./attachments-context"

type ImgProps = React.ImgHTMLAttributes<HTMLImageElement> & { node?: unknown }

// Two prefixes: the new sanitize-friendly URL produced by the rewriter, plus
// the legacy custom scheme so old persisted messages keep rendering.
const LEGACY_PREFIX = "kiln-att://"

function extractAttachmentId(src: unknown): string | undefined {
  if (typeof src !== "string") return undefined
  if (src.startsWith(ATT_URL_PREFIX)) return src.slice(ATT_URL_PREFIX.length)
  if (src.startsWith(LEGACY_PREFIX)) return src.slice(LEGACY_PREFIX.length)
  return undefined
}

function formatBytes(n?: number): string {
  if (!n || n < 1024) return n ? `${n} B` : ""
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / 1024 / 1024).toFixed(2)} MB`
}

export const MarkdownImage = memo(function MarkdownImage(props: ImgProps) {
  const { src, alt, className, node: _node, ...rest } = props
  const id = extractAttachmentId(src)
  const attachment = useAttachment(id)

  // External image (not an attachment marker) — render with the same wrapper
  // styling but skip the attachment metadata footer.
  if (!attachment) {
    if (id !== undefined) {
      return <MissingAttachment id={id} />
    }
    return (
      // eslint-disable-next-line @next/next/no-img-element
      <img
        {...rest}
        src={src}
        alt={alt ?? ""}
        className={cn("my-3 max-w-full rounded-2xl border border-white/10", className)}
      />
    )
  }

  return <AttachmentImage attachment={attachment} alt={alt} />
})

function AttachmentImage({
  attachment,
  alt,
}: {
  attachment: { id: string; mime: string; data_url: string; bytes?: number; meta?: Record<string, string | number | boolean>; tool?: string }
  alt?: string
}) {
  // For data URLs (the common case for tool-generated images) the browser
  // can complete decoding before React attaches `onLoad`, so the event
  // never fires and the image stays at opacity 0 — making the click target
  // invisible and giving the impression that "click to zoom" is broken.
  // Default `loaded=true` for `data:` URLs and rely on `onLoad`/`onError`
  // only for remote sources.
  const isDataUrl = attachment.data_url.startsWith("data:")
  const [loaded, setLoaded] = useState(isDataUrl)
  const [errored, setErrored] = useState(false)
  const [zoomed, setZoomed] = useState(false)

  const meta = attachment.meta ?? {}
  const caption = useMemo(() => {
    const prompt = meta.prompt as string | undefined
    const title = meta.title as string | undefined
    return alt && alt !== "image" ? alt : (prompt || title || attachment.tool || "Generated image")
  }, [alt, meta, attachment.tool])

  const sub = useMemo(() => {
    const parts: string[] = []
    if (meta.model) parts.push(String(meta.model))
    if (meta.seed !== undefined) parts.push(`seed ${meta.seed}`)
    if (meta.width && meta.height) parts.push(`${meta.width}×${meta.height}`)
    if (attachment.bytes) parts.push(formatBytes(attachment.bytes))
    return parts.join(" · ")
  }, [meta, attachment.bytes])

  const handleDownload = useCallback(() => {
    const a = document.createElement("a")
    a.href = attachment.data_url
    const ext = (attachment.mime.split("/")[1] || "png").split("+")[0]
    const stem = (meta.prompt as string | undefined)?.slice(0, 32).replace(/[^a-z0-9]+/gi, "_").toLowerCase() || attachment.id
    a.download = `${stem}.${ext}`
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
  }, [attachment, meta])

  return (
    <>
      <figure className="my-4 max-w-full">
        <div
          className="group relative overflow-hidden rounded-2xl border border-white/10 bg-gradient-to-b from-white/[0.025] to-transparent shadow-[0_18px_56px_-18px_rgba(0,0,0,0.55)]"
        >
          {/* Skeleton shimmer while loading */}
          <AnimatePresence>
            {!loaded && !errored && (
              <motion.div
                key="skeleton"
                initial={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                transition={{ duration: 0.4 }}
                className="absolute inset-0 z-0"
              >
                <div className="absolute inset-0 bg-[linear-gradient(110deg,hsl(0_0%_100%_/_0.02)_8%,hsl(0_0%_100%_/_0.06)_18%,hsl(0_0%_100%_/_0.02)_33%)] bg-[length:200%_100%]"
                  style={{ animation: "shimmer 1.6s linear infinite" }}
                />
                <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 text-muted-foreground/55">
                  <Sparkles className="size-5 animate-pulse text-primary" />
                  <span className="text-[11px] uppercase tracking-[0.22em]">Rendering</span>
                </div>
              </motion.div>
            )}
          </AnimatePresence>

          {errored ? (
            <div className="flex aspect-video w-full items-center justify-center gap-2 text-muted-foreground/60">
              <ImageOff className="size-4" />
              <span className="text-[12px]">Image failed to load.</span>
            </div>
          ) : (
            // Plain <button>, no opacity/scale animation: any animation that
            // gates visibility on `loaded` also gates clickability for users
            // who try to zoom the moment the image renders.
            <button
              type="button"
              onClick={() => setZoomed(true)}
              className="relative block w-full cursor-zoom-in"
              aria-label="Open image"
            >
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={attachment.data_url}
                alt={alt ?? caption}
                onLoad={() => setLoaded(true)}
                onError={() => setErrored(true)}
                className="block h-auto w-full"
              />
              <span className="pointer-events-none absolute inset-0 rounded-2xl ring-1 ring-inset ring-white/[0.04] transition-all group-hover:ring-white/15" />
              <span className="pointer-events-none absolute right-3 top-3 flex items-center gap-1 rounded-full border border-white/15 bg-black/45 px-2 py-1 text-[10.5px] text-white/85 opacity-0 backdrop-blur-md transition-opacity group-hover:opacity-100">
                <Expand className="size-3" />
                Click to zoom
              </span>
            </button>
          )}

          {/* Footer toolbar */}
          {loaded && !errored && (
            <motion.div
              initial={{ opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.4, delay: 0.05 }}
              className="flex items-center justify-between gap-3 border-t border-white/[0.06] bg-black/35 px-3.5 py-2 backdrop-blur"
            >
              <div className="flex min-w-0 flex-col">
                <div className="truncate text-[12.5px] font-medium text-foreground/90" title={caption}>
                  {caption}
                </div>
                {sub && (
                  <div className="truncate text-[10.5px] uppercase tracking-[0.18em] text-muted-foreground/55">
                    {sub}
                  </div>
                )}
              </div>
              <button
                type="button"
                onClick={handleDownload}
                className="inline-flex h-7 shrink-0 items-center gap-1.5 rounded-full border border-white/10 bg-white/[0.04] px-2.5 text-[11px] font-medium text-foreground/85 transition-colors hover:border-white/20 hover:bg-white/[0.08]"
              >
                <Download className="size-3" />
                Save
              </button>
            </motion.div>
          )}
        </div>
      </figure>

      <Dialog open={zoomed} onOpenChange={setZoomed}>
        {/* Inline style forces a real width — DialogContent bakes in
            `sm:max-w-sm` (384 px) which twMerge can't override with an
            unprefixed `max-w-[...]` class. Inline style sits outside
            Tailwind's cascade so the zoom modal always gets room for the
            image regardless of viewport. */}
        <DialogContent
          style={{ width: "min(92vw, 1200px)", maxWidth: "min(92vw, 1200px)" }}
          className="flex max-h-[92vh] flex-col gap-0 overflow-hidden border border-white/10 bg-[#0b0d14] p-0"
        >
          <DialogHeader className="flex flex-row items-center justify-between gap-3 border-b border-white/5 px-5 py-3">
            <div className="min-w-0">
              <DialogTitle className="truncate text-[13px] font-semibold tracking-tight text-foreground">
                {caption}
              </DialogTitle>
              <DialogDescription className="truncate text-[11px] text-muted-foreground/70">
                {sub || attachment.mime}
              </DialogDescription>
            </div>
            <button
              type="button"
              onClick={handleDownload}
              className="inline-flex h-8 shrink-0 items-center gap-1.5 rounded-full border border-white/10 bg-white/[0.04] px-3 text-[11.5px] font-medium text-foreground/85 hover:bg-white/[0.08]"
            >
              <Download className="size-3.5" />
              Download
            </button>
          </DialogHeader>
          {/* Div (not button) because nested interactive elements complicate
              keyboard focus, and the backdrop already handles dismiss. Clicking
              the image still closes — we keep the cursor-zoom-out affordance. */}
          <div
            onClick={() => setZoomed(false)}
            role="button"
            tabIndex={0}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") setZoomed(false)
            }}
            aria-label="Close image"
            className="flex min-h-[60vh] flex-1 cursor-zoom-out items-center justify-center bg-black/40 p-4"
          >
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={attachment.data_url}
              alt={alt ?? caption}
              className="max-h-[78vh] max-w-full object-contain"
            />
          </div>
        </DialogContent>
      </Dialog>
    </>
  )
}

function MissingAttachment({ id }: { id: string }) {
  return (
    <span className="my-2 inline-flex items-center gap-2 rounded-md border border-amber-500/20 bg-amber-500/8 px-2.5 py-1 text-[11px] text-amber-200/85">
      <ImageOff className="size-3" />
      Image attachment <code className="font-mono text-[10px]">{id}</code> not available
    </span>
  )
}
