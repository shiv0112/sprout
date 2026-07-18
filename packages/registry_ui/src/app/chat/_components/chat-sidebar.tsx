"use client"

import { useMemo, useState } from "react"
import { Plus, Pencil, Trash2, Search } from "lucide-react"

import { Button } from "@/components/ui/button"
import { ScrollArea } from "@/components/ui/scroll-area"
import type { Conversation } from "@/lib/chat-store"

interface ChatSidebarProps {
  conversations: Conversation[]
  activeId: string | null
  onNew: () => void
  onSelect: (id: string) => void
  onDelete: (id: string) => void
  onRename: (id: string, title: string) => void
}

function relativeTime(ts: number): string {
  const diff = Date.now() - ts
  const m = Math.floor(diff / 60_000)
  if (m < 1) return "just now"
  if (m < 60) return `${m}m ago`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h ago`
  const d = Math.floor(h / 24)
  if (d < 7) return `${d}d ago`
  return new Date(ts).toLocaleDateString()
}

function sectionLabel(ts: number): string {
  const now = new Date()
  const then = new Date(ts)
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime()
  const yesterday = today - 86_400_000
  const stamp = new Date(then.getFullYear(), then.getMonth(), then.getDate()).getTime()

  if (stamp >= today) return "Today"
  if (stamp >= yesterday) return "Yesterday"
  return "Earlier"
}

export function ChatSidebar({
  conversations,
  activeId,
  onNew,
  onSelect,
  onDelete,
  onRename,
}: ChatSidebarProps) {
  const [query, setQuery] = useState("")
  const [renamingId, setRenamingId] = useState<string | null>(null)
  const [renameValue, setRenameValue] = useState("")

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return conversations
    return conversations.filter((c) => c.title.toLowerCase().includes(q))
  }, [conversations, query])

  function commitRename(id: string) {
    if (renameValue.trim()) onRename(id, renameValue.trim())
    setRenamingId(null)
  }

  const grouped = useMemo(() => {
    if (query) {
      return [{ label: "Results", items: filtered }]
    }

    const buckets = new Map<string, Conversation[]>()
    for (const conv of filtered) {
      const label = sectionLabel(conv.updatedAt)
      const list = buckets.get(label)
      if (list) {
        list.push(conv)
      } else {
        buckets.set(label, [conv])
      }
    }

    return ["Today", "Yesterday", "Earlier"]
      .map((label) => ({ label, items: buckets.get(label) ?? [] }))
      .filter((group) => group.items.length > 0)
  }, [filtered, query])

  return (
    <aside className="flex h-full w-72 shrink-0 flex-col border-r border-border/60 bg-[linear-gradient(180deg,#171a28_0%,#121520_18%,#10131d_100%)]">
      {/* Header */}
      <div className="shrink-0 space-y-3 border-b border-white/5 px-4 py-4">
        <Button
          onClick={onNew}
          className="h-10 w-full justify-start gap-2 rounded-xl bg-primary px-4 text-sm font-semibold text-primary-foreground shadow-[0_10px_24px_hsl(26_92%_58%_/_0.16)] transition-all hover:-translate-y-px hover:bg-primary/92 hover:shadow-[0_12px_28px_hsl(26_92%_58%_/_0.18)]"
          size="sm"
        >
          <Plus className="size-4" />
          New chat
        </Button>
        <div className="relative">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground/50" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search chats…"
            autoComplete="off"
            name="sprout-chat-search"
            className="h-10 w-full rounded-xl border border-white/8 bg-white/[0.02] py-1.5 pl-8 pr-3 text-[12px] text-foreground placeholder:text-muted-foreground/40 outline-none transition-all focus:border-primary/20 focus:bg-white/[0.04] focus:ring-2 focus:ring-primary/12"
          />
        </div>
      </div>

      {/* Conversation list */}
      <ScrollArea className="flex-1">
        <div className="px-3 pb-5 pt-3">
          {filtered.length === 0 ? (
            <div className="px-3 py-8 text-center text-xs text-muted-foreground/60">
              {query ? "No chats match." : "Start a new chat to begin."}
            </div>
          ) : (
            <>
              <div className="space-y-3">
                {grouped.map((group) => (
                  <section key={group.label} className="space-y-1">
                    <div className="px-3 pb-1 text-[10px] font-medium uppercase tracking-[0.22em] text-muted-foreground/28">
                      {group.label}
                    </div>
                    <ul className="space-y-0.5">
                      {group.items.map((conv) => {
                        const isActive = conv.id === activeId
                        const isRenaming = renamingId === conv.id
                        return (
                          <li key={conv.id}>
                            <div
                              className={`group relative flex items-start gap-2 rounded-xl px-2.5 py-2 text-xs transition-all ${
                                isActive
                                  ? "bg-white/[0.055] text-foreground shadow-[inset_0_1px_0_hsl(0_0%_100%_/_0.03)]"
                                  : "text-muted-foreground hover:bg-white/[0.03] hover:text-foreground"
                              }`}
                            >
                              <button
                                onClick={() => onSelect(conv.id)}
                                className="flex min-w-0 flex-1 items-start overflow-hidden text-left"
                              >
                                <div className="min-w-0 flex-1">
                                  {isRenaming ? (
                                    <input
                                      autoFocus
                                      value={renameValue}
                                      onChange={(e) => setRenameValue(e.target.value)}
                                      onBlur={() => commitRename(conv.id)}
                                      onKeyDown={(e) => {
                                        if (e.key === "Enter") commitRename(conv.id)
                                        if (e.key === "Escape") setRenamingId(null)
                                      }}
                                      className="w-full bg-transparent text-[13px] font-medium text-foreground outline-none"
                                    />
                                  ) : (
                                    <>
                                      <div
                                        className={`truncate text-[13px] leading-5 ${
                                          isActive ? "font-semibold text-foreground" : "font-medium text-foreground/86"
                                        }`}
                                      >
                                        {conv.title}
                                      </div>
                                      <div
                                        className={`mt-0.5 text-[11px] tabular-nums ${
                                          isActive ? "text-primary/72" : "text-muted-foreground/30"
                                        }`}
                                      >
                                        {relativeTime(conv.updatedAt)}
                                      </div>
                                    </>
                                  )}
                                </div>
                              </button>
                              <div
                                className={`flex shrink-0 items-center gap-1 transition-all ${
                                  isActive
                                    ? "translate-x-0 opacity-100"
                                    : "translate-x-1 opacity-0 group-hover:translate-x-0 group-hover:opacity-100"
                                }`}
                              >
                                <button
                                  type="button"
                                  aria-label={`Rename ${conv.title}`}
                                  onClick={() => {
                                    setRenamingId(conv.id)
                                    setRenameValue(conv.title)
                                  }}
                                  className="flex size-6 items-center justify-center rounded-full bg-white/[0.03] outline-none transition-all hover:bg-white/[0.07]"
                                >
                                  <Pencil className="size-3 text-muted-foreground/72" />
                                </button>
                                <button
                                  type="button"
                                  aria-label={`Delete ${conv.title}`}
                                  onClick={() => onDelete(conv.id)}
                                  className="flex size-6 items-center justify-center rounded-full bg-white/[0.03] outline-none transition-all hover:bg-destructive/12"
                                >
                                  <Trash2 className="size-3 text-destructive/72" />
                                </button>
                              </div>
                            </div>
                          </li>
                        )
                      })}
                    </ul>
                  </section>
                ))}
              </div>
            </>
          )}
        </div>
      </ScrollArea>

      {/* Footer count */}
      <div className="shrink-0 border-t border-white/5 px-4 py-3 text-[10px] uppercase tracking-[0.18em] text-muted-foreground/22">
        {conversations.length} {conversations.length === 1 ? "chat" : "chats"}
      </div>
    </aside>
  )
}
