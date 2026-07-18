"use client"

/**
 * Local-first chat history store.
 *
 * Conversations are persisted to localStorage so refreshes, accidental
 * navigation, and tab closes don't lose context. Each conversation has:
 *   - id          (nanoid-style random string)
 *   - title       (auto-generated from first user message; user-editable later)
 *   - createdAt   (epoch ms — used for sort order)
 *   - updatedAt   (epoch ms — bumped on every message append)
 *   - messages    (UIMessage-shaped array compatible with @ai-sdk/react)
 *
 * Backend persistence can replace this layer later without UI changes —
 * the same hooks expose the same API.
 */

import { useCallback, useEffect, useState } from "react"

import type { UIMessage } from "ai"

const STORAGE_KEY = "sprout.chat.history.v1"
const MAX_TITLE_LEN = 60
const MAX_CONVERSATIONS = 50

export interface Conversation {
  id: string
  title: string
  createdAt: number
  updatedAt: number
  messages: UIMessage[]
}

interface StoreShape {
  version: 1
  conversations: Conversation[]
  activeId: string | null
}

function emptyStore(): StoreShape {
  return { version: 1, conversations: [], activeId: null }
}

function loadStore(): StoreShape {
  if (typeof window === "undefined") return emptyStore()
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    if (!raw) return emptyStore()
    const parsed = JSON.parse(raw) as StoreShape
    if (parsed.version !== 1 || !Array.isArray(parsed.conversations)) {
      return emptyStore()
    }
    return parsed
  } catch {
    return emptyStore()
  }
}

function saveStore(store: StoreShape): void {
  if (typeof window === "undefined") return
  try {
    // Cap at MAX_CONVERSATIONS to keep localStorage from growing unbounded.
    const trimmed: StoreShape = {
      ...store,
      conversations: [...store.conversations]
        .sort((a, b) => b.updatedAt - a.updatedAt)
        .slice(0, MAX_CONVERSATIONS),
    }
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(trimmed))
  } catch {
    // Quota exceeded or storage disabled — fail silently. Worst case the
    // user loses the in-progress conversation on refresh, which matches
    // the pre-iter-41 behaviour anyway.
  }
}

function newId(): string {
  // RFC4122-ish; nanoid not strictly needed and avoids the dep on the
  // server (this file is "use client" but bundlers will tree-shake it).
  return (
    Math.random().toString(36).slice(2, 10) +
    Math.random().toString(36).slice(2, 10)
  )
}

function deriveTitle(messages: UIMessage[]): string {
  for (const msg of messages) {
    if (msg.role !== "user") continue
    const text = msg.parts
      .map((p) => (p.type === "text" ? p.text : ""))
      .join("")
      .trim()
    if (!text) continue
    return text.length > MAX_TITLE_LEN ? text.slice(0, MAX_TITLE_LEN) + "…" : text
  }
  return "New conversation"
}

export function useChatStore() {
  const [store, setStore] = useState<StoreShape>(emptyStore)
  const [hydrated, setHydrated] = useState(false)

  // Hydrate from localStorage on mount (client-only). The "setState in
  // effect" pattern is necessary here because localStorage isn't available
  // during SSR — we MUST defer the read until after mount, then sync the
  // state. The cascading-render warning doesn't apply: this only fires
  // once per mount.
  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    setStore(loadStore())
    setHydrated(true)
  }, [])
  /* eslint-enable react-hooks/set-state-in-effect */

  // Persist on every store mutation (after hydration so we don't clobber).
  useEffect(() => {
    if (!hydrated) return
    saveStore(store)
  }, [store, hydrated])

  const sortedConversations = [...store.conversations].sort(
    (a, b) => b.updatedAt - a.updatedAt,
  )

  const activeConversation =
    store.activeId !== null
      ? store.conversations.find((c) => c.id === store.activeId) ?? null
      : null

  const createConversation = useCallback((): string => {
    const id = newId()
    const now = Date.now()
    const conv: Conversation = {
      id,
      title: "New conversation",
      createdAt: now,
      updatedAt: now,
      messages: [],
    }
    setStore((prev) => ({
      ...prev,
      conversations: [conv, ...prev.conversations],
      activeId: id,
    }))
    return id
  }, [])

  const setActiveConversation = useCallback((id: string | null) => {
    setStore((prev) => ({ ...prev, activeId: id }))
  }, [])

  const deleteConversation = useCallback((id: string) => {
    setStore((prev) => {
      const remaining = prev.conversations.filter((c) => c.id !== id)
      const nextActive =
        prev.activeId === id
          ? remaining[0]?.id ?? null
          : prev.activeId
      return { ...prev, conversations: remaining, activeId: nextActive }
    })
  }, [])

  const renameConversation = useCallback((id: string, title: string) => {
    setStore((prev) => ({
      ...prev,
      conversations: prev.conversations.map((c) =>
        c.id === id
          ? { ...c, title: title.trim() || "Untitled" }
          : c,
      ),
    }))
  }, [])

  const persistMessages = useCallback(
    (id: string, messages: UIMessage[]) => {
      setStore((prev) => {
        const existing = prev.conversations.find((c) => c.id === id)
        if (!existing) return prev
        const titleNeedsUpdate =
          existing.title === "New conversation" && messages.length > 0
        const existingSerialized = JSON.stringify(existing.messages)
        const nextSerialized = JSON.stringify(messages)
        const messagesChanged = existingSerialized !== nextSerialized
        if (!messagesChanged && !titleNeedsUpdate) return prev
        return {
          ...prev,
          conversations: prev.conversations.map((c) =>
            c.id === id
              ? {
                  ...c,
                  messages,
                  updatedAt: messagesChanged ? Date.now() : c.updatedAt,
                  title: titleNeedsUpdate ? deriveTitle(messages) : c.title,
                }
              : c,
          ),
        }
      })
    },
    [],
  )

  return {
    hydrated,
    conversations: sortedConversations,
    activeConversation,
    activeId: store.activeId,
    createConversation,
    setActiveConversation,
    deleteConversation,
    renameConversation,
    persistMessages,
  }
}
