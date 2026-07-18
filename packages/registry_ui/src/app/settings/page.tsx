"use client"

import { useCallback, useEffect, useState } from "react"
import { UserProfile, Show } from "@clerk/nextjs"
import { dark } from "@clerk/themes"
import {
  Key,
  Copy,
  Check,
  RefreshCw,
  Loader2,
  Eye,
  EyeOff,
  Settings,
  ShieldCheck,
  Trash2,
  Plus,
  Wrench,
} from "lucide-react"

import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Separator } from "@/components/ui/separator"

// ---------------------------------------------------------------------------
// API Key Management
// ---------------------------------------------------------------------------

function ApiKeySection() {
  const [apiKey, setApiKey] = useState<string | null>(null)
  const [isServerMaskedKey, setIsServerMaskedKey] = useState(false)
  const [isLoadingExisting, setIsLoadingExisting] = useState(true)
  const [isVisible, setIsVisible] = useState(false)
  const [isGenerating, setIsGenerating] = useState(false)
  const [isRegenerating, setIsRegenerating] = useState(false)
  const [copied, setCopied] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const maskedKey = apiKey
    ? `${apiKey.slice(0, 8)}${"*".repeat(Math.max(0, apiKey.length - 12))}${apiKey.slice(-4)}`
    : null

  const getErrorMessage = useCallback(
    async (res: Response, fallback: string) => {
      const payload = await res
        .json()
        .catch(() => ({ detail: fallback })) as { detail?: string }
      return payload.detail || fallback
    },
    []
  )

  const fetchApiKey = useCallback(async () => {
    setIsGenerating(true)
    setError(null)

    try {
      const res = await fetch("/api/settings/api-key", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
      })

      if (res.status === 401) {
        throw new Error("Authentication expired. Please sign in again.")
      }
      if (!res.ok) {
        throw new Error(await getErrorMessage(res, "Failed to generate API key"))
      }

      const data = await res.json()
      setApiKey(data.api_key || data.key)
      setIsServerMaskedKey(false)
      setIsVisible(true)
    } catch (e) {
      if (e instanceof TypeError) {
        setError("Could not reach the settings API. Check that the Next.js app and registry API are running.")
      } else {
        setError(e instanceof Error ? e.message : "Failed to generate API key")
      }
    } finally {
      setIsGenerating(false)
    }
  }, [getErrorMessage])

  const regenerateApiKey = useCallback(async () => {
    setIsRegenerating(true)
    setError(null)

    try {
      const res = await fetch("/api/settings/api-key/regenerate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
      })

      if (res.status === 401) {
        throw new Error("Authentication expired. Please sign in again.")
      }
      if (!res.ok) {
        throw new Error(
          await getErrorMessage(res, "Failed to regenerate API key")
        )
      }

      const data = await res.json()
      setApiKey(data.api_key || data.key)
      setIsServerMaskedKey(false)
      setIsVisible(true)
    } catch (e) {
      if (e instanceof TypeError) {
        setError("Could not reach the settings API. Check that the Next.js app and registry API are running.")
      } else {
        setError(
          e instanceof Error ? e.message : "Failed to regenerate API key"
        )
      }
    } finally {
      setIsRegenerating(false)
    }
  }, [getErrorMessage])

  const handleCopy = useCallback(() => {
    if (!apiKey || isServerMaskedKey) return
    navigator.clipboard.writeText(apiKey).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    })
  }, [apiKey, isServerMaskedKey])

  useEffect(() => {
    const controller = new AbortController()

    const loadExistingKey = async () => {
      setIsLoadingExisting(true)
      setError(null)

      try {
        const res = await fetch("/api/settings/api-key", {
          method: "GET",
          signal: controller.signal,
        })

        if (res.status === 404 || res.status === 401) {
          setApiKey(null)
          return
        }
        if (!res.ok) {
          throw new Error(
            await getErrorMessage(res, "Failed to load existing API key")
          )
        }

        const data = await res.json()
        setApiKey(data.api_key || data.key || null)
        setIsServerMaskedKey(true)
        setIsVisible(false)
      } catch (e) {
        if (!(e instanceof DOMException && e.name === "AbortError")) {
          setError(
            e instanceof Error ? e.message : "Failed to load existing API key"
          )
        }
      } finally {
        if (!controller.signal.aborted) setIsLoadingExisting(false)
      }
    }

    loadExistingKey()
    return () => {
      controller.abort()
    }
  }, [getErrorMessage])

  return (
    <Card className="section-surface gap-0 py-0">
      <CardHeader className="px-5 pb-2.5 pt-5">
        <CardTitle className="flex items-center gap-2">
          <Key className="size-4" />
          API Key
        </CardTitle>
        <CardDescription>
          Use your API key to authenticate requests to the Sprout registry from
          your code or CI/CD pipelines.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3 px-5 pb-5 pt-0">
        {isLoadingExisting && (
          <div className="flex items-center gap-2 rounded-lg border border-border/70 bg-muted/25 px-3 py-2 text-sm text-muted-foreground">
            <Loader2 className="size-3.5 animate-spin" />
            Loading API key status...
          </div>
        )}

        {apiKey ? (
          <>
            <div className="flex items-center gap-2">
              <div className="relative flex-1">
                <Input
                  readOnly
                  value={
                    isServerMaskedKey ? apiKey : isVisible ? apiKey : maskedKey || ""
                  }
                  className="border-border/70 bg-card/70 pr-20 font-mono text-sm"
                />
                <div className="absolute right-1 top-1/2 flex -translate-y-1/2 items-center gap-0.5">
                  <Button
                    variant="ghost"
                    size="icon-xs"
                    onClick={() => {
                      if (!isServerMaskedKey) setIsVisible(!isVisible)
                    }}
                    disabled={isServerMaskedKey}
                    className="text-muted-foreground hover:text-foreground"
                  >
                    {isVisible ? (
                      <EyeOff className="size-3.5" />
                    ) : (
                      <Eye className="size-3.5" />
                    )}
                  </Button>
                  <Button
                    variant="ghost"
                    size="icon-xs"
                    onClick={handleCopy}
                    disabled={isServerMaskedKey}
                    className="text-muted-foreground hover:text-foreground"
                  >
                    {copied ? (
                      <Check className="size-3.5 text-emerald-500" />
                    ) : (
                      <Copy className="size-3.5" />
                    )}
                  </Button>
                </div>
              </div>
            </div>

            {isServerMaskedKey && (
              <div className="rounded-lg border border-amber-500/30 bg-amber-500/5 p-3 text-xs text-amber-400">
                Your key is masked for security. Click <strong>Reveal &amp; Copy</strong> to
                get a fresh key you can copy. This will replace your current key.
              </div>
            )}

            <div className="flex flex-wrap items-center gap-2.5 pt-0.5">
              {isServerMaskedKey ? (
                <Button
                  variant="default"
                  size="sm"
                  onClick={regenerateApiKey}
                  disabled={isRegenerating}
                  className="gap-1.5"
                >
                  {isRegenerating ? (
                    <Loader2 className="size-3.5 animate-spin" />
                  ) : (
                    <Key className="size-3.5" />
                  )}
                  Reveal &amp; Copy
                </Button>
              ) : (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={regenerateApiKey}
                  disabled={isRegenerating}
                  className="gap-1.5"
                >
                  {isRegenerating ? (
                    <Loader2 className="size-3.5 animate-spin" />
                  ) : (
                    <RefreshCw className="size-3.5" />
                  )}
                  Regenerate
                </Button>
              )}
              {!isServerMaskedKey && (
                <p className="text-xs text-muted-foreground">
                  This will invalidate your current key
                </p>
              )}
            </div>
          </>
        ) : (
          <div className="flex flex-col items-center gap-4 py-6">
            <div className="flex size-12 items-center justify-center rounded-xl bg-muted">
              <Key className="size-6 text-muted-foreground" />
            </div>
            <div className="text-center">
              <p className="text-sm font-medium">No API key generated</p>
              <p className="text-xs text-muted-foreground">
                Generate a key to start using the Sprout API
              </p>
            </div>
            <Button
              onClick={fetchApiKey}
              disabled={isGenerating}
              className="gap-1.5"
            >
              {isGenerating ? (
                <Loader2 className="size-4 animate-spin" />
              ) : (
                <Key className="size-4" />
              )}
              {isGenerating ? "Generating..." : "Generate API Key"}
            </Button>
          </div>
        )}

        {error && (
          <div className="rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">
            {error}
          </div>
        )}
      </CardContent>
    </Card>
  )
}

// ---------------------------------------------------------------------------
// Tool Environment Variables
// ---------------------------------------------------------------------------

function ToolEnvVarsSection() {
  const [envVars, setEnvVars] = useState<Record<string, string>>({})
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [deletingKey, setDeletingKey] = useState<string | null>(null)

  // Add form state
  const [newName, setNewName] = useState("")
  const [newValue, setNewValue] = useState("")
  const [isSaving, setIsSaving] = useState(false)
  const [showAddForm, setShowAddForm] = useState(false)

  const getErrorMessage = useCallback((err: unknown) => {
    return err instanceof Error ? err.message : "Unknown error"
  }, [])

  // Fetch saved env vars on mount
  useEffect(() => {
    const controller = new AbortController()

    async function load() {
      try {
        const res = await fetch("/api/settings/tool-env-vars", {
          signal: controller.signal,
        })
        if (!res.ok) {
          if (res.status === 404 || res.status === 401) {
            setEnvVars({})
            return
          }
          throw new Error(`Failed to load: ${res.status}`)
        }
        const data = await res.json()
        setEnvVars(data.env_vars || {})
      } catch (err) {
        if (!(err instanceof DOMException && err.name === "AbortError")) {
          setError(getErrorMessage(err))
        }
      } finally {
        if (!controller.signal.aborted) {
          setIsLoading(false)
        }
      }
    }
    load()
    return () => controller.abort()
  }, [getErrorMessage])

  const handleDelete = async (varName: string) => {
    setDeletingKey(varName)
    try {
      const res = await fetch(`/api/settings/tool-env-vars/${encodeURIComponent(varName)}`, {
        method: "DELETE",
      })
      if (!res.ok) throw new Error(`Delete failed: ${res.status}`)
      setEnvVars((prev) => {
        const next = { ...prev }
        delete next[varName]
        return next
      })
    } catch (err) {
      setError(getErrorMessage(err))
    } finally {
      setDeletingKey(null)
    }
  }

  const handleAdd = async () => {
    if (!newName.trim() || !newValue.trim()) return
    setIsSaving(true)
    setError(null)
    try {
      const res = await fetch("/api/settings/tool-env-vars", {
        method: "PUT",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ env_vars: { [newName.trim()]: newValue.trim() } }),
      })
      if (!res.ok) throw new Error(`Save failed: ${res.status}`)

      // Show masked version locally
      const val = newValue.trim()
      const masked = val.length > 4 ? "*".repeat(val.length - 4) + val.slice(-4) : "****"
      setEnvVars((prev) => ({ ...prev, [newName.trim()]: masked }))
      setNewName("")
      setNewValue("")
      setShowAddForm(false)
    } catch (err) {
      setError(getErrorMessage(err))
    } finally {
      setIsSaving(false)
    }
  }

  const hasVars = Object.keys(envVars).length > 0

  return (
    <Card className="border border-border/70 bg-card/40">
      <CardHeader>
        <div className="flex items-center gap-2">
          <Wrench className="size-4 text-primary" />
          <CardTitle className="text-lg">Tool API Keys</CardTitle>
        </div>
        <CardDescription>
          API keys for third-party services used by tools (e.g. SERPER_API_KEY). These are
          automatically prompted in chat when a tool needs them.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {isLoading ? (
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="size-4 animate-spin" />
            Loading...
          </div>
        ) : hasVars ? (
          <div className="space-y-2">
            {Object.entries(envVars).map(([name, maskedValue]) => (
              <div
                key={name}
                className="flex items-center justify-between rounded-lg border border-border/50 bg-white/[0.02] px-3 py-2.5"
              >
                <div className="flex items-center gap-3 min-w-0">
                  <Key className="size-3.5 shrink-0 text-muted-foreground/60" />
                  <span className="text-sm font-medium">{name}</span>
                  <span className="truncate text-xs text-muted-foreground font-mono">
                    {maskedValue}
                  </span>
                </div>
                <Button
                  variant="ghost"
                  size="icon"
                  className="size-7 shrink-0 text-muted-foreground hover:text-destructive"
                  onClick={() => handleDelete(name)}
                  disabled={deletingKey === name}
                >
                  {deletingKey === name ? (
                    <Loader2 className="size-3.5 animate-spin" />
                  ) : (
                    <Trash2 className="size-3.5" />
                  )}
                </Button>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">
            No tool API keys saved yet. They will be added automatically when prompted in chat.
          </p>
        )}

        {/* Add key form */}
        {showAddForm ? (
          <div className="space-y-3 rounded-lg border border-border/50 bg-white/[0.02] p-3">
            <Input
              value={newName}
              onChange={(e) => setNewName(e.target.value.toUpperCase())}
              placeholder="Variable name (e.g. SERPER_API_KEY)"
              className="font-mono text-sm"
            />
            <Input
              type="password"
              value={newValue}
              onChange={(e) => setNewValue(e.target.value)}
              placeholder="API key value"
              className="text-sm"
            />
            <div className="flex gap-2">
              <Button size="sm" onClick={handleAdd} disabled={isSaving || !newName.trim() || !newValue.trim()}>
                {isSaving ? <Loader2 className="mr-1.5 size-3.5 animate-spin" /> : null}
                Save
              </Button>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => { setShowAddForm(false); setNewName(""); setNewValue("") }}
              >
                Cancel
              </Button>
            </div>
          </div>
        ) : (
          <Button
            variant="outline"
            size="sm"
            onClick={() => setShowAddForm(true)}
            className="gap-1.5"
          >
            <Plus className="size-3.5" />
            Add Key
          </Button>
        )}

        {error && (
          <div className="rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">
            {error}
          </div>
        )}
      </CardContent>
    </Card>
  )
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function SettingsPage() {
  return (
    <div className="w-full">
      <Show when="signed-out">
        <div className="hero-surface flex min-h-[32rem] flex-col items-center justify-center gap-6 text-center">
          <div className="relative">
            <span className="absolute -inset-4 rounded-2xl bg-gradient-to-br from-primary/30 to-primary/10 blur-xl" />
            <div className="relative flex size-16 items-center justify-center rounded-2xl border border-primary/35 bg-primary/15">
              <Settings className="size-8 text-primary" />
            </div>
          </div>
          <div className="max-w-sm space-y-2">
            <h3 className="text-lg font-semibold">Sign in required</h3>
            <p className="text-sm leading-relaxed text-muted-foreground">
              You need to sign in to access account settings.
            </p>
          </div>
        </div>
      </Show>

      <Show when="signed-in">
        <SettingsContent />
      </Show>
    </div>
  )
}

function SettingsContent() {
  return (
    <div className="mx-auto max-w-3xl space-y-8">
      {/* Header */}
      <div className="hero-surface mb-8">
        <div className="flex items-center gap-3">
          <div className="flex size-11 items-center justify-center rounded-xl border border-primary/30 bg-primary/15">
            <Settings className="size-5 text-primary" />
          </div>
          <div>
            <h1 className="text-3xl font-bold tracking-tight">Settings</h1>
            <p className="mt-0.5 text-sm text-muted-foreground">
              Manage your account and API keys
            </p>
          </div>
        </div>
      </div>

      {/* API Key */}
      <ApiKeySection />

      <Separator />

      {/* Tool Environment Variables */}
      <ToolEnvVarsSection />

      <Separator />

      {/* Clerk profile — embedded */}
      <div className="space-y-4">
        <div className="flex items-center gap-2">
          <ShieldCheck className="size-4 text-muted-foreground" />
          <h2 className="text-lg font-semibold">Account</h2>
        </div>
        <div className="section-surface w-full p-4 [&>div]:w-full [&>div>div]:w-full">
          <UserProfile
            routing="hash"
            appearance={{
              baseTheme: dark,
              elements: {
                rootBox: { width: "100%", maxWidth: "100%" },
                card: { width: "100%", maxWidth: "100%", boxShadow: "none" },
              },
            }}
          />
        </div>
      </div>
    </div>
  )
}
