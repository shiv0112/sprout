"use client"

import {
  AnimatePresence,
  motion,
  useInView,
  useMotionTemplate,
  useMotionValue,
  useScroll,
  useSpring,
  useTransform,
  type MotionValue,
} from "framer-motion"
import {
  ArrowRight,
  ArrowUpRight,
  Check,
  Cpu,
  Lock,
  Network,
  Package,
  ShieldCheck,
  Sparkles,
  Workflow,
  Zap,
} from "lucide-react"
import Link from "next/link"
import { useEffect, useMemo, useRef, useState } from "react"

import { Show, SignUpButton } from "@clerk/nextjs"

import { Button } from "@/components/ui/button"
import type { Tool } from "@/lib/registry"
import { cn } from "@/lib/utils"

interface LandingClientProps {
  tools: Tool[]
  toolCount: number
}

const PIPELINE = [
  { label: "Ask", desc: "Plain-English request", icon: Sparkles },
  { label: "Plan", desc: "Decompose into a DAG", icon: Workflow },
  { label: "Detect", desc: "Find the missing tool", icon: Zap },
  { label: "Synthesize", desc: "Generate, test, register", icon: Cpu },
  { label: "Execute", desc: "Hot-load and run", icon: ArrowRight },
]

const PROBLEMS = [
  {
    title: "Tools shouldn't be a roadblock",
    body: "When a tool is missing your agent fails. The user retries. The integration ticket gets filed. Six weeks pass.",
    icon: Lock,
  },
  {
    title: "Manual integration is dead weight",
    body: "Writing, deploying, restarting — repeat for every API. Your roadmap turns into glue code.",
    icon: Workflow,
  },
  {
    title: "Tools rot in silos",
    body: "LangChain, AG2, Mistral, Pydantic AI — each gets its own copy of the same tool. Then they drift.",
    icon: Network,
  },
]

const TRUST = [
  { label: "Sandboxed runtime", icon: ShieldCheck },
  { label: "Schema-validated specs", icon: Check },
  { label: "Versioned & rollback-safe", icon: Package },
  { label: "Framework-agnostic", icon: Network },
]

export function LandingClient({ tools, toolCount }: LandingClientProps) {
  return (
    <div className="relative w-full overflow-x-hidden">
      <Backdrop />
      <Hero toolCount={toolCount} />
      <Marquee tools={tools} />
      <Problem />
      <Pipeline />
      <Showcase tools={tools} />
      <Trust />
      <FinalCta />
    </div>
  )
}

/* ───────────────────────────── BACKDROP ───────────────────────────── */

function Backdrop() {
  return (
    <div className="pointer-events-none absolute inset-0 -z-10 overflow-hidden">
      {/* Aurora */}
      <div className="absolute -top-[20%] left-1/2 h-[80vh] w-[120vw] -translate-x-1/2 rounded-[50%] bg-[radial-gradient(ellipse_at_center,hsl(226_61%_60%_/_0.18),transparent_60%)] blur-3xl" />
      <div className="absolute top-[40vh] -left-[10%] h-[70vh] w-[60vw] rounded-full bg-[radial-gradient(circle,hsl(226_61%_60%_/_0.10),transparent_70%)] blur-3xl" />
      <div className="absolute top-[80vh] right-[-15%] h-[60vh] w-[55vw] rounded-full bg-[radial-gradient(circle,hsl(41_72%_55%_/_0.08),transparent_70%)] blur-3xl" />
      {/* Subtle grid */}
      <div
        className="absolute inset-0 opacity-[0.035]"
        style={{
          backgroundImage:
            "linear-gradient(hsl(0 0% 100% / 1) 1px, transparent 1px), linear-gradient(90deg, hsl(0 0% 100% / 1) 1px, transparent 1px)",
          backgroundSize: "56px 56px",
          maskImage:
            "radial-gradient(ellipse 80% 70% at 50% 30%, black, transparent 75%)",
        }}
      />
      {/* Grain */}
      <div
        className="absolute inset-0 opacity-[0.04] mix-blend-overlay"
        style={{
          backgroundImage:
            "url(\"data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='120' height='120'><filter id='n'><feTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='2' stitchTiles='stitch'/></filter><rect width='100%' height='100%' filter='url(%23n)' opacity='0.6'/></svg>\")",
        }}
      />
    </div>
  )
}

/* ───────────────────────────── HERO ───────────────────────────── */

function Hero({ toolCount }: { toolCount: number }) {
  const ref = useRef<HTMLDivElement>(null)
  const { scrollYProgress } = useScroll({
    target: ref,
    offset: ["start start", "end start"],
  })
  const y = useTransform(scrollYProgress, [0, 1], [0, -60])
  const opacity = useTransform(scrollYProgress, [0, 1], [1, 0.2])
  const scale = useTransform(scrollYProgress, [0, 1], [1, 0.96])

  // mouse parallax for the orb
  const mx = useMotionValue(0)
  const my = useMotionValue(0)
  const orbX = useSpring(useTransform(mx, [-1, 1], [-30, 30]), { stiffness: 60, damping: 20 })
  const orbY = useSpring(useTransform(my, [-1, 1], [-20, 20]), { stiffness: 60, damping: 20 })

  function handleMove(e: React.PointerEvent<HTMLDivElement>) {
    const rect = e.currentTarget.getBoundingClientRect()
    mx.set(((e.clientX - rect.left) / rect.width - 0.5) * 2)
    my.set(((e.clientY - rect.top) / rect.height - 0.5) * 2)
  }

  return (
    <section
      ref={ref}
      onPointerMove={handleMove}
      className="relative flex min-h-[92vh] flex-col items-center justify-center px-6 pb-24 pt-32 text-center"
    >
      {/* parallax orb */}
      <motion.div
        style={{ x: orbX, y: orbY }}
        className="pointer-events-none absolute inset-0 -z-10"
      >
        <div className="absolute left-1/2 top-1/3 size-[520px] -translate-x-1/2 -translate-y-1/2 rounded-full bg-[radial-gradient(circle,hsl(226_61%_60%_/_0.22),transparent_60%)] blur-3xl" />
      </motion.div>
      <motion.div style={{ y, opacity, scale }} className="mx-auto max-w-5xl">
        <motion.div
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6 }}
          className="mx-auto inline-flex items-center gap-2 rounded-full border border-white/10 bg-white/[0.03] px-3.5 py-1.5 text-[11px] uppercase tracking-[0.2em] text-muted-foreground/80 backdrop-blur"
        >
          <span className="relative flex size-1.5">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-primary opacity-60" />
            <span className="relative inline-flex size-1.5 rounded-full bg-primary" />
          </span>
          <span className="font-medium tracking-[0.18em]">
            <CountUp value={toolCount} /> live tools · auto-evolving
          </span>
        </motion.div>

        <SpotifyHeadline className="mt-8">
          <span className="block text-[clamp(2.75rem,8.5vw,6.75rem)] font-semibold leading-[0.95] tracking-[-0.04em] text-foreground">
            Tools that
          </span>
          <span className="block text-[clamp(2.75rem,8.5vw,6.75rem)] font-semibold leading-[0.95] tracking-[-0.04em]">
            <RotatingWord
              words={["build themselves", "test themselves", "ship themselves", "evolve themselves"]}
              textClassName="bg-gradient-to-r from-primary via-primary/80 to-accent-foreground bg-clip-text text-transparent"
            />
          </span>
        </SpotifyHeadline>

        <motion.p
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.7, delay: 0.4 }}
          className="mx-auto mt-7 max-w-2xl text-balance text-[15px] leading-relaxed text-muted-foreground/80 sm:text-base"
        >
          Sprout is a self-evolving tool registry for AI agents. Ask anything — if
          the right tool doesn&rsquo;t exist, Sprout writes it, tests it, and ships it
          to every framework. Live. No restarts.
        </motion.p>

        <motion.div
          initial={{ opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.7, delay: 0.55 }}
          className="mt-10 flex flex-col items-center justify-center gap-3 sm:flex-row"
        >
          <Show when={"signed-in"}>
            <Magnetic>
              <Link href="/chat">
                <Button
                  size="lg"
                  className="group h-12 gap-2 rounded-2xl bg-primary px-6 text-[14px] font-semibold text-primary-foreground shadow-[var(--shadow-brand)] transition-all hover:bg-primary/95 hover:border-white/35 hover:shadow-[var(--shadow-brand-hover)]"
                >
                  Open the console
                  <ArrowRight className="size-4" />
                </Button>
              </Link>
            </Magnetic>
          </Show>
          <Show when={"signed-out"}>
            <Magnetic>
              <SignUpButton mode="modal" forceRedirectUrl="/chat" signInForceRedirectUrl="/chat">
                <Button
                  size="lg"
                  className="group h-12 gap-2 rounded-2xl bg-primary px-6 text-[14px] font-semibold text-primary-foreground shadow-[var(--shadow-brand)] transition-all hover:bg-primary/95 hover:border-white/35 hover:shadow-[var(--shadow-brand-hover)]"
                >
                  Get started — free
                  <ArrowRight className="size-4" />
                </Button>
              </SignUpButton>
            </Magnetic>
          </Show>
          <Magnetic>
            <Link href="/tools">
              <Button
                size="lg"
                variant="outline"
                className="h-12 gap-2 rounded-2xl border-white/10 bg-white/[0.025] px-5 text-[14px] font-medium text-foreground/85 backdrop-blur transition-colors hover:border-white/40 hover:bg-white/[0.05]"
              >
                Browse the registry
                <ArrowUpRight className="size-4" />
              </Button>
            </Link>
          </Magnetic>
        </motion.div>
      </motion.div>

    </section>
  )
}

/* Reveal-by-line headline like a Spotify "Wrapped" cover */
function SpotifyHeadline({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <motion.h1
      initial="hidden"
      animate="show"
      variants={{
        hidden: {},
        show: { transition: { staggerChildren: 0.12, delayChildren: 0.15 } },
      }}
      className={className}
    >
      <Reveal>{children}</Reveal>
    </motion.h1>
  )
}

function Reveal({ children }: { children: React.ReactNode }) {
  return (
    <motion.span
      variants={{
        hidden: { opacity: 0, y: 30, filter: "blur(8px)" },
        show: { opacity: 1, y: 0, filter: "blur(0px)", transition: { duration: 0.8, ease: [0.21, 0.47, 0.32, 0.98] } },
      }}
      className="block"
    >
      {children}
    </motion.span>
  )
}

/* ───────────────────────────── MARQUEE ───────────────────────────── */

function Marquee({ tools }: { tools: Tool[] }) {
  // Duplicate the list so the marquee loops seamlessly.
  const pool = useMemo(() => {
    const base = tools.length > 0 ? tools : Array.from({ length: 12 }).map((_, i) => ({
      id: `placeholder-${i}`,
      name: "synthesizing…",
      version: "0.0.0",
      description: "",
      author: "",
      category: "",
      tags: [],
    } as unknown as Tool))
    return [...base, ...base, ...base]
  }, [tools])

  return (
    <section className="px-4 py-8 sm:px-6">
      <div className="mx-auto max-w-7xl">
        <div className="group/marquee relative isolate overflow-hidden rounded-3xl border border-white/[0.07] bg-gradient-to-b from-white/[0.025] to-white/[0.005] py-6 backdrop-blur-sm">
          <div className="pointer-events-none absolute inset-y-0 left-0 z-10 w-24 rounded-l-3xl bg-gradient-to-r from-background via-background/85 to-transparent" />
          <div className="pointer-events-none absolute inset-y-0 right-0 z-10 w-24 rounded-r-3xl bg-gradient-to-l from-background via-background/85 to-transparent" />
          <div className="relative overflow-hidden">
            <div className="flex w-max gap-3 animate-[marquee_55s_linear_infinite] group-hover/marquee:[animation-play-state:paused]">
              {pool.map((t, i) => (
                <div
                  key={`${t.id}-${i}`}
                  className="group flex shrink-0 items-center gap-2 rounded-full border border-white/10 bg-white/[0.02] px-4 py-1.5 text-[12px] text-muted-foreground/80 transition-colors hover:border-primary/30 hover:text-foreground"
                >
                  <Package className="size-3 text-primary/70" />
                  <span className="font-mono">{t.name}</span>
                  <span className="text-muted-foreground/40">·</span>
                  <span className="text-[10.5px] text-muted-foreground/55">v{t.version}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
      <style jsx>{`
        @keyframes marquee {
          from { transform: translateX(0); }
          to { transform: translateX(-33.333%); }
        }
      `}</style>
    </section>
  )
}

/* ───────────────────────────── PROBLEM ───────────────────────────── */

function Problem() {
  return (
    <section className="mx-auto max-w-6xl px-6 py-32">
      <SectionLabel>The friction</SectionLabel>
      <SectionHeadline>
        Today&rsquo;s agents <span className="text-muted-foreground/60">stop where their tools stop.</span>
      </SectionHeadline>

      <div className="mt-16 grid gap-4 md:grid-cols-3">
        {PROBLEMS.map((p, i) => (
          <motion.div
            key={p.title}
            initial={{ opacity: 0, y: 24 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true, margin: "-60px" }}
            transition={{ duration: 0.5, delay: i * 0.08 }}
            className="group relative overflow-hidden rounded-2xl border border-white/[0.07] bg-gradient-to-b from-white/[0.025] to-transparent p-6 transition-colors hover:border-white/15"
          >
            <span className="pointer-events-none absolute -inset-px rounded-2xl opacity-0 transition-opacity duration-500 group-hover:opacity-100" style={{ background: "radial-gradient(400px circle at var(--x,50%) var(--y,50%), hsl(226 61% 60% / 0.08), transparent 40%)" }} />
            <p.icon className="size-5 text-primary/85" strokeWidth={1.7} />
            <h3 className="mt-5 text-[17px] font-semibold tracking-tight text-foreground">{p.title}</h3>
            <p className="mt-2 text-[13.5px] leading-relaxed text-muted-foreground/85">{p.body}</p>
          </motion.div>
        ))}
      </div>
    </section>
  )
}

/* ───────────────────────────── PIPELINE ───────────────────────────── */

function Pipeline() {
  const ref = useRef<HTMLDivElement>(null)
  const { scrollYProgress } = useScroll({ target: ref, offset: ["start 0.85", "end 0.55"] })
  const smoothProgress = useSpring(scrollYProgress, { stiffness: 90, damping: 22, mass: 0.4 })
  const sparkLeft = useTransform(smoothProgress, [0, 1], ["0%", "100%"])
  const sparkOpacity = useTransform(smoothProgress, [0, 0.05, 0.95, 1], [0, 1, 1, 0])

  return (
    <section className="px-4 py-16 sm:px-6">
      <div className="mx-auto max-w-7xl">
        <div className="relative overflow-hidden rounded-3xl border border-white/[0.07] bg-gradient-to-b from-white/[0.025] to-white/[0.005] px-6 py-20 backdrop-blur-sm sm:px-12 lg:px-16">
          <div className="pointer-events-none absolute -top-32 left-1/2 size-[600px] -translate-x-1/2 rounded-full bg-[radial-gradient(circle,hsl(226_61%_60%_/_0.07),transparent_70%)] blur-3xl" />

          <SectionLabel>The flow</SectionLabel>
          <SectionHeadline>
            From request to result, in one lap.
          </SectionHeadline>
          <p className="mt-5 max-w-xl text-[14.5px] leading-relaxed text-muted-foreground/80">
            ARIA decomposes the ask into a task graph. If a node has no tool, Vibe
            generates one inside a sandbox, validates it against schema and tests,
            and registers it back into the graph mid-execution.
          </p>

          <div ref={ref} className="relative mt-16">
            {/* Background dashed track */}
            <div
              className="absolute left-[34px] right-[34px] top-[34px] hidden h-px md:block"
              style={{
                backgroundImage:
                  "repeating-linear-gradient(90deg, hsl(0 0% 100% / 0.18) 0 6px, transparent 6px 12px)",
              }}
            />
            {/* Animated fill on top */}
            <motion.div
              style={{ scaleX: smoothProgress }}
              className="absolute left-[34px] right-[34px] top-[34px] hidden h-px origin-left bg-gradient-to-r from-primary via-primary/70 to-primary/0 md:block"
            />
            {/* Riding spark */}
            <motion.div
              style={{ left: sparkLeft, opacity: sparkOpacity }}
              className="pointer-events-none absolute top-[34px] hidden -translate-x-1/2 -translate-y-1/2 md:block"
            >
              <div className="relative">
                <span className="absolute -inset-3 rounded-full bg-primary/60 blur-md" />
                <span className="relative block size-2 rounded-full bg-primary shadow-[0_0_18px_hsl(226_61%_60%_/_0.9)]" />
              </div>
            </motion.div>

            <div className="grid gap-5 md:grid-cols-5">
              {PIPELINE.map((s, i) => (
                <PipelineStep key={s.label} step={s} index={i} progress={smoothProgress} total={PIPELINE.length} />
              ))}
            </div>
          </div>
        </div>
      </div>
    </section>
  )
}

function PipelineStep({
  step,
  index,
  progress,
  total,
}: {
  step: (typeof PIPELINE)[number]
  index: number
  progress: MotionValue<number>
  total: number
}) {
  const Icon = step.icon
  const threshold = index / (total - 1)
  const fill = useTransform(progress, [Math.max(0, threshold - 0.05), threshold], [0, 1])
  const ringOpacity = useTransform(fill, [0, 1], [0.15, 1])
  const glow = useTransform(fill, [0, 1], [0, 1])

  return (
    <motion.div
      initial={{ opacity: 0, y: 16 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, margin: "-40px" }}
      transition={{ duration: 0.5, delay: index * 0.06 }}
      className="relative flex flex-col items-start"
    >
      <motion.div
        style={{
          boxShadow: useTransform(
            glow,
            (v: number) => `0 0 0 1px hsl(226 61% 60% / ${0.2 + v * 0.4}), 0 18px 48px -12px hsl(226 61% 60% / ${v * 0.45})`,
          ),
          scale: useTransform(fill, [0, 1], [1, 1.04]),
        }}
        className="relative flex size-[68px] items-center justify-center rounded-3xl border border-white/10 bg-gradient-to-b from-white/[0.05] to-white/[0.015] backdrop-blur"
      >
        <motion.span
          style={{ opacity: ringOpacity }}
          className="absolute inset-0 rounded-3xl bg-primary/10"
        />
        <Icon className="relative size-[22px] text-primary" strokeWidth={1.6} />
      </motion.div>
      <div className="mt-4 text-[10px] font-medium uppercase tracking-[0.22em] text-muted-foreground/55">
        Step {String(index + 1).padStart(2, "0")}
      </div>
      <div className="mt-1 text-[15px] font-semibold tracking-tight text-foreground">{step.label}</div>
      <div className="mt-1 text-[12.5px] leading-relaxed text-muted-foreground/75">{step.desc}</div>
    </motion.div>
  )
}

/* ───────────────────────────── SHOWCASE ───────────────────────────── */

function Showcase({ tools }: { tools: Tool[] }) {
  const featured = useMemo(() => {
    const priority = [
      "image_generate",
      "satellite_image",
      "weather_forecast",
      "stock_quote",
      "wikipedia_search",
      "github_repo_info",
      "hackernews_top",
      "lyrics_fetch",
      "youtube_transcript",
    ]
    const lookup = new Map(tools.map((t) => [t.name, t]))
    const ordered: Tool[] = []
    for (const name of priority) {
      const hit = lookup.get(name)
      if (hit) ordered.push(hit)
    }
    for (const t of tools) {
      if (!ordered.find((o) => o.id === t.id)) ordered.push(t)
    }
    return ordered.slice(0, 6)
  }, [tools])

  return (
    <section className="mx-auto max-w-6xl px-6 py-32">
      <div className="flex flex-col items-start justify-between gap-6 md:flex-row md:items-end">
        <div>
          <SectionLabel>The registry</SectionLabel>
          <SectionHeadline>
            One source of truth, every framework.
          </SectionHeadline>
        </div>
        <Link href="/tools" className="group inline-flex items-center gap-1.5 text-[13px] font-medium text-foreground/70 transition-colors hover:text-foreground">
          Explore all {tools.length} tools
          <ArrowUpRight className="size-3.5" />
        </Link>
      </div>

      <div className="mt-14 grid gap-3 md:grid-cols-2 lg:grid-cols-3">
        {featured.map((tool, i) => (
          <ShowcaseCard key={tool.id} tool={tool} index={i} />
        ))}
      </div>
    </section>
  )
}

function ShowcaseCard({ tool, index }: { tool: Tool; index: number }) {
  const ref = useRef<HTMLAnchorElement>(null)
  const px = useMotionValue(0.5)
  const py = useMotionValue(0.5)
  const rotateX = useSpring(useTransform(py, [0, 1], [6, -6]), { stiffness: 220, damping: 18 })
  const rotateY = useSpring(useTransform(px, [0, 1], [-6, 6]), { stiffness: 220, damping: 18 })
  const spotlight = useMotionTemplate`radial-gradient(360px circle at ${useTransform(px, (v) => `${v * 100}%`)} ${useTransform(py, (v) => `${v * 100}%`)}, hsl(226 61% 60% / 0.18), transparent 42%)`

  function handleMove(e: React.PointerEvent<HTMLAnchorElement>) {
    const el = ref.current
    if (!el) return
    const rect = el.getBoundingClientRect()
    px.set((e.clientX - rect.left) / rect.width)
    py.set((e.clientY - rect.top) / rect.height)
  }

  function handleLeave() {
    px.set(0.5)
    py.set(0.5)
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: 18 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, margin: "-50px" }}
      transition={{ duration: 0.5, delay: index * 0.05 }}
      style={{ perspective: 800 }}
    >
      <motion.div style={{ rotateX, rotateY, transformStyle: "preserve-3d" }}>
        <Link
          href={`/tools/${tool.id}`}
          ref={ref}
          onPointerMove={handleMove}
          onPointerLeave={handleLeave}
          className="group relative block overflow-hidden rounded-2xl border border-white/[0.07] bg-gradient-to-b from-white/[0.025] to-transparent p-5 transition-colors hover:border-white/20"
        >
          <motion.span
            style={{ background: spotlight }}
            className="pointer-events-none absolute -inset-px rounded-2xl opacity-0 transition-opacity duration-300 group-hover:opacity-100"
          />
          <div className="relative flex items-center justify-between gap-3" style={{ transform: "translateZ(20px)" }}>
            <div className="flex min-w-0 items-center gap-3">
              <div className="flex size-9 shrink-0 items-center justify-center rounded-2xl border border-primary/15 bg-primary/10 text-primary">
                <Package className="size-4" strokeWidth={1.7} />
              </div>
              <div className="min-w-0">
                <div className="truncate font-mono text-[13px] font-semibold text-foreground">
                  {tool.name}
                </div>
                <div className="truncate text-[10.5px] uppercase tracking-[0.18em] text-muted-foreground/55">
                  {tool.category || "general"} · v{tool.version}
                </div>
              </div>
            </div>
            <ArrowUpRight className="size-4 shrink-0 text-muted-foreground/40 transition-colors group-hover:text-foreground" />
          </div>
          <p className="relative mt-4 line-clamp-2 text-[12.5px] leading-relaxed text-muted-foreground/85" style={{ transform: "translateZ(12px)" }}>
            {tool.description}
          </p>
        </Link>
      </motion.div>
    </motion.div>
  )
}

/* ───────────────────────────── TRUST ───────────────────────────── */

function Trust() {
  return (
    <section className="px-4 py-12 sm:px-6">
      <div className="mx-auto max-w-7xl">
        <div className="rounded-3xl border border-white/[0.07] bg-gradient-to-b from-white/[0.025] to-white/[0.005] px-6 py-10 backdrop-blur-sm sm:px-10">
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            {TRUST.map((t, i) => (
              <motion.div
                key={t.label}
                initial={{ opacity: 0, y: 14, scale: 0.96 }}
                whileInView={{ opacity: 1, y: 0, scale: 1 }}
                viewport={{ once: true, margin: "-40px" }}
                transition={{ type: "spring", stiffness: 110, damping: 14, delay: i * 0.07 }}
                className="flex items-center gap-3 rounded-2xl border border-white/[0.07] bg-white/[0.02] px-4 py-3 text-[12.5px] font-medium text-foreground/85 transition-colors hover:border-primary/25 hover:bg-white/[0.045]"
              >
                <t.icon className="size-3.5 text-primary" />
                {t.label}
              </motion.div>
            ))}
          </div>
        </div>
      </div>
    </section>
  )
}

/* ───────────────────────────── FINAL CTA ───────────────────────────── */

function FinalCta() {
  return (
    <section className="relative overflow-hidden px-6 py-40 text-center">
      <div className="pointer-events-none absolute inset-0 -z-10">
        <div className="absolute left-1/2 top-1/2 size-[120vw] max-w-[1400px] -translate-x-1/2 -translate-y-1/2 rounded-full bg-[radial-gradient(circle,hsl(226_61%_60%_/_0.18),transparent_60%)] blur-3xl" />
      </div>

      <motion.div
        initial={{ opacity: 0, y: 24 }}
        whileInView={{ opacity: 1, y: 0 }}
        viewport={{ once: true }}
        transition={{ duration: 0.7 }}
        className="mx-auto max-w-4xl"
      >
        <h2 className="text-[clamp(2.25rem,6.5vw,5rem)] font-semibold leading-[1] tracking-[-0.04em] text-foreground">
          Stop building tools.
        </h2>
        <h2 className="text-[clamp(2.25rem,6.5vw,5rem)] font-semibold leading-[1] tracking-[-0.04em]">
          <span
            className="bg-gradient-to-r from-primary via-primary/80 to-accent-foreground bg-clip-text text-transparent"
            style={{ backgroundSize: "200% 100%", animation: "gradientSlide 6s ease-in-out infinite" }}
          >
            Start using them
          </span>
        </h2>
        <p className="mx-auto mt-7 max-w-xl text-[14.5px] leading-relaxed text-muted-foreground/80">
          Plug Sprout in once. Every agent on your team gets a registry that grows
          itself — safely, version-controlled, and observable.
        </p>

        <div className="mt-10 flex flex-col items-center justify-center gap-3 sm:flex-row">
          <Show when={"signed-in"}>
            <Magnetic>
              <Link href="/chat">
                <Button
                  size="lg"
                  className="h-12 gap-2 rounded-2xl bg-primary px-7 text-[14px] font-semibold text-primary-foreground shadow-[var(--shadow-brand)] transition-colors hover:bg-primary/95 hover:border-white/35"
                >
                  Open the console
                  <ArrowRight className="size-4" />
                </Button>
              </Link>
            </Magnetic>
          </Show>
          <Show when={"signed-out"}>
            <Magnetic>
              <SignUpButton mode="modal" forceRedirectUrl="/chat" signInForceRedirectUrl="/chat">
                <Button
                  size="lg"
                  className="h-12 gap-2 rounded-2xl bg-primary px-7 text-[14px] font-semibold text-primary-foreground shadow-[var(--shadow-brand)] transition-colors hover:bg-primary/95 hover:border-white/35"
                >
                  Get started — free
                  <ArrowRight className="size-4" />
                </Button>
              </SignUpButton>
            </Magnetic>
          </Show>
          <Magnetic>
            <Link href="/tools">
              <Button
                size="lg"
                variant="outline"
                className="h-12 gap-2 rounded-2xl border-white/10 bg-white/[0.025] px-6 text-[14px] font-medium text-foreground/85 backdrop-blur transition-colors hover:border-white/40 hover:bg-white/[0.05]"
              >
                Browse the registry
              </Button>
            </Link>
          </Magnetic>
        </div>
      </motion.div>
    </section>
  )
}

/* ───────────────────────────── PRIMITIVES ───────────────────────────── */

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="inline-flex items-center gap-2">
      <span className="h-px w-6 bg-primary/60" />
      <span className="text-[10.5px] font-semibold uppercase tracking-[0.24em] text-primary/85">
        {children}
      </span>
    </div>
  )
}

function SectionHeadline({ children }: { children: React.ReactNode }) {
  return (
    <h2 className={cn("mt-5 max-w-3xl text-[clamp(2rem,4.5vw,3.25rem)] font-semibold leading-[1.05] tracking-[-0.03em] text-foreground")}>
      {children}
    </h2>
  )
}

/* Magnetic wrapper: gently pulls children toward the cursor, springs back. */
/* Static wrapper. Previously followed the cursor magnetically, which made the
 * buttons drift "in the air" on hover; now it just holds them in place. Hover
 * feedback is a border highlight on the buttons themselves. */
function Magnetic({ children }: { children: React.ReactNode; strength?: number }) {
  return <span className="inline-block">{children}</span>
}

/* Rotating word with blur+slide enter/exit.
 * Renders the gradient text directly on the rendered word spans
 * (background-clip:text only paints text directly inside the element).
 * Reserves space using the longest word so the line never collapses. */
function RotatingWord({
  words,
  interval = 2400,
  textClassName = "",
}: {
  words: string[]
  interval?: number
  textClassName?: string
}) {
  const [index, setIndex] = useState(0)
  useEffect(() => {
    if (words.length < 2) return
    const id = window.setInterval(() => setIndex((i) => (i + 1) % words.length), interval)
    return () => window.clearInterval(id)
  }, [words.length, interval])

  const longest = useMemo(
    () => words.reduce((a, b) => (b.length > a.length ? b : a), words[0] ?? ""),
    [words],
  )

  return (
    <span className="relative inline-block">
      <span
        aria-hidden="true"
        className={cn("invisible whitespace-nowrap pr-[0.12em]", textClassName)}
      >
        {longest}
      </span>
      <span className="absolute inset-0 flex items-center justify-center">
        <AnimatePresence mode="wait" initial={false}>
          <motion.span
            key={words[index]}
            initial={{ opacity: 0, y: "0.35em", filter: "blur(10px)" }}
            animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
            exit={{ opacity: 0, y: "-0.35em", filter: "blur(10px)" }}
            transition={{ duration: 0.55, ease: [0.21, 0.47, 0.32, 0.98] }}
            className={cn("inline-block whitespace-nowrap pr-[0.12em]", textClassName)}
            style={{ backgroundSize: "200% 100%", animation: "gradientSlide 6s ease-in-out infinite" }}
          >
            {words[index]}
          </motion.span>
        </AnimatePresence>
      </span>
    </span>
  )
}

/* Animated integer count-up that runs once on mount. */
function CountUp({ value, duration = 1.2 }: { value: number; duration?: number }) {
  const ref = useRef<HTMLSpanElement>(null)
  const inView = useInView(ref, { once: true, margin: "-20%" })
  const mv = useMotionValue(0)
  const [display, setDisplay] = useState(0)

  useEffect(() => {
    if (!inView) return
    const controls = mv.on("change", (v) => setDisplay(Math.round(v)))
    const startTs = performance.now()
    let raf = 0
    const tick = (ts: number) => {
      const t = Math.min(1, (ts - startTs) / (duration * 1000))
      const eased = 1 - Math.pow(1 - t, 3)
      mv.set(eased * value)
      if (t < 1) raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => {
      controls()
      cancelAnimationFrame(raf)
    }
  }, [inView, value, duration, mv])

  return <span ref={ref}>{display}</span>
}

/* Global keyframes (gradient sweep + marquee already inline-scoped in Marquee). */
function _GlobalKeyframesSink() {
  return null
}
