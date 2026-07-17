import type { Metadata } from "next"
import { ClerkProvider, Show, UserButton, SignInButton } from "@clerk/nextjs"
import { dark } from "@clerk/themes"
import { Figtree, Geist_Mono } from "next/font/google"
import Link from "next/link"
import { Flame } from "lucide-react"

import { NavLinks } from "@/app/_components/nav-links"
import { Providers } from "@/app/_components/providers"
import { Button } from "@/components/ui/button"
import "./globals.css"

const figtree = Figtree({
  variable: "--font-figtree",
  subsets: ["latin"],
  weight: ["400", "500", "600", "700", "800", "900"],
  display: "swap",
})
const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] })

export const metadata: Metadata = {
  title: "Kiln",
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
        <ClerkProvider appearance={{ baseTheme: dark }}>
        <Providers>
          <div className="relative flex min-h-full flex-col overflow-x-clip">
            <div className="pointer-events-none absolute inset-0 -z-10">
              <div className="absolute left-1/2 top-0 h-[28rem] w-[48rem] -translate-x-1/2 rounded-full bg-[radial-gradient(circle_at_center,hsl(24_95%_58%_/_0.12),transparent_70%)] blur-3xl" />
              <div className="absolute -left-40 top-52 h-96 w-96 rounded-full bg-[radial-gradient(circle_at_center,hsl(204_100%_68%_/_0.09),transparent_72%)] blur-3xl" />
              <div className="absolute bottom-0 right-0 h-[30rem] w-[34rem] bg-[radial-gradient(circle_at_center,hsl(258_92%_72%_/_0.08),transparent_72%)] blur-3xl" />
            </div>

            <header className="sticky top-0 z-50 border-b border-border/60 bg-background/75 backdrop-blur-2xl backdrop-saturate-150">
              <div className="mx-auto flex h-16 w-full max-w-[1200px] items-center px-6">
                <Link href="/" className="group mr-8 flex items-center gap-2.5">
                  <span className="relative flex size-9 items-center justify-center rounded-xl border border-primary/30 bg-primary/10 transition-all duration-300 group-hover:border-primary/50 group-hover:bg-primary/15">
                    <Flame className="size-4 text-primary transition-colors duration-300 group-hover:text-primary" />
                  </span>
                  <span className="text-[15px] font-semibold tracking-tight text-foreground/95">
                    Kiln
                  </span>
                </Link>

                <NavLinks items={nav} />

                <div className="ml-auto flex items-center gap-3">
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
                          baseTheme: dark,
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
                  <Flame className="size-3 text-primary/65" />
                  Kiln — Self-evolving tool registry
                </div>
                <div className="text-[11px] text-muted-foreground/80">
                  &copy; {new Date().getFullYear()}
                </div>
              </div>
            </footer>
          </div>
        </Providers>
        </ClerkProvider>
      </body>
    </html>
  )
}
