import type { Metadata } from "next"
import { Show, UserButton, SignInButton } from "@clerk/nextjs"
import { Figtree, Geist_Mono } from "next/font/google"
import Link from "next/link"
import { Sprout } from "lucide-react"

import { NavLinks } from "@/app/_components/nav-links"
import { Providers } from "@/app/_components/providers"
import { ClerkAppearanceProvider } from "@/app/_components/clerk-appearance-provider"
import { ThemeToggle } from "@/app/_components/theme-toggle"
import { Button } from "@/components/ui/button"
import "./globals.css"

// Applies the stored theme before first paint so there's no flash of the wrong
// palette. Default (no stored choice / no JS) stays dark = "Woad & Marigold".
const themeInitScript = `(function(){try{var t=localStorage.getItem('theme');var d=document.documentElement;if(t==='light'){d.classList.remove('dark')}else if(t==='dark'){d.classList.add('dark')}}catch(e){}})()`

const figtree = Figtree({
  variable: "--font-figtree",
  subsets: ["latin"],
  weight: ["400", "500", "600", "700", "800", "900"],
  display: "swap",
})
const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] })

export const metadata: Metadata = {
  title: "Sprout",
  description: "Self-evolving tool registry for autonomous AI agents",
}

const nav = [
  { href: "/tools", label: "Registry", icon: "LayoutGrid" as const },
  { href: "/chat", label: "Chat", icon: "MessageSquare" as const },
  { href: "/publish", label: "Publish", icon: "Upload" as const },
  { href: "/settings", label: "Settings", icon: "Settings" as const },
]

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html
      lang="en"
      className={`${figtree.variable} ${geistMono.variable} dark h-full bg-background antialiased`}
      suppressHydrationWarning
    >
      <body className="min-h-full bg-background text-foreground">
        <script dangerouslySetInnerHTML={{ __html: themeInitScript }} />
        <ClerkAppearanceProvider>
        <Providers>
          <div className="relative flex min-h-full flex-col overflow-x-clip">
            <div className="pointer-events-none absolute inset-0 -z-10">
              <div className="absolute left-1/2 top-0 h-[28rem] w-[48rem] -translate-x-1/2 rounded-full bg-primary/12 blur-3xl" />
              <div className="absolute -left-40 top-52 h-96 w-96 rounded-full bg-accent-foreground/10 blur-3xl" />
              <div className="absolute bottom-0 right-0 h-[30rem] w-[34rem] rounded-full bg-primary/[0.08] blur-3xl" />
            </div>

            <header className="sticky top-0 z-50 border-b border-border/60 bg-background/75 backdrop-blur-2xl backdrop-saturate-150">
              <div className="mx-auto flex h-16 w-full max-w-[1200px] items-center px-6">
                <Link href="/" className="group mr-8 flex items-center gap-2.5">
                  <span className="relative flex size-9 items-center justify-center rounded-xl border border-sprout/30 bg-sprout/10 transition-all duration-300 group-hover:border-sprout/50 group-hover:bg-sprout/15">
                    <Sprout className="size-4 text-sprout transition-colors duration-300" />
                  </span>
                  <span className="flex flex-col leading-none">
                    <span className="text-[15px] font-semibold tracking-tight text-foreground/95">
                      Sprout
                    </span>
                    <span className="mt-0.5 hidden text-[10px] font-medium text-muted-foreground/70 sm:block">
                      Self evolving tool registry
                    </span>
                  </span>
                </Link>

                <NavLinks items={nav} />

                <div className="ml-auto flex items-center gap-3">
                  <ThemeToggle />
                  <Show when="signed-out">
                    <SignInButton mode="modal">
                      <Button variant="outline" size="sm" className="rounded-lg px-3.5 text-xs">
                        Sign in
                      </Button>
                    </SignInButton>
                  </Show>
                  <Show when="signed-in">
                    <div className="rounded-full border border-border/70 p-[2px] transition-colors duration-300 hover:border-border">
                      <UserButton
                        appearance={{
                          elements: { avatarBox: "h-7 w-7" },
                        }}
                      />
                    </div>
                  </Show>
                </div>
              </div>
            </header>

            <main className="flex-1">
              <div className="mx-auto w-full max-w-[1200px] px-6 pb-14 pt-10">{children}</div>
            </main>

            <footer className="border-t border-border/60">
              <div className="mx-auto flex w-full max-w-[1200px] items-center justify-between px-6 py-5">
                <div className="flex items-center gap-2 text-[11px] text-muted-foreground">
                  <Sprout className="size-3 text-sprout/65" />
                  Sprout — Self evolving tool registry
                </div>
                <div className="text-[11px] text-muted-foreground/80">
                  &copy; {new Date().getFullYear()}
                </div>
              </div>
            </footer>
          </div>
        </Providers>
        </ClerkAppearanceProvider>
      </body>
    </html>
  )
}
