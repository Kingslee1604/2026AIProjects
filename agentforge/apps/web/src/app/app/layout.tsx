"use client";

import {
  ClipboardList,
  KeyRound,
  LayoutDashboard,
  Library,
  LogOut,
  type LucideIcon,
  MessageSquareText,
  ShieldCheck,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { type ReactNode, useEffect } from "react";

import { Logo } from "@/components/brand/logo";
import { ThemeToggle } from "@/components/theme-toggle";
import { useSession, useSignOut } from "@/lib/session";
import { cn } from "@/lib/utils";

interface NavItem {
  href: string;
  label: string;
  icon: LucideIcon;
  adminOnly?: boolean;
}

const NAV: NavItem[] = [
  { href: "/app", label: "Overview", icon: LayoutDashboard },
  { href: "/app/knowledge", label: "Knowledge", icon: Library },
  { href: "/app/ask", label: "Ask", icon: MessageSquareText },
  { href: "/app/runs", label: "Questionnaires", icon: ClipboardList },
  { href: "/app/reviews", label: "Reviews", icon: ShieldCheck },
  { href: "/app/admin", label: "Admin", icon: KeyRound, adminOnly: true },
];

/** "/app" matches only itself; sections also match their sub-pages. */
function isActive(pathname: string, href: string): boolean {
  return href === "/app" ? pathname === href : pathname === href || pathname.startsWith(`${href}/`);
}

function initials(email: string): string {
  const name = email.split("@")[0] ?? "";
  const parts = name.split(/[._-]+/).filter(Boolean);
  return ((parts[0]?.[0] ?? "") + (parts[1]?.[0] ?? parts[0]?.[1] ?? "")).toUpperCase() || "?";
}

export default function AppLayout({ children }: { children: ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const session = useSession();
  const signOut = useSignOut();

  useEffect(() => {
    if (session.data === null) router.replace("/login");
  }, [session.data, router]);

  if (!session.data) {
    return (
      <p className="p-8 text-sm text-muted" role="status">
        {session.isError ? "Could not reach the server." : "Loading…"}
      </p>
    );
  }
  const user = session.data;
  const items = NAV.filter((item) => !item.adminOnly || user.role === "admin");

  return (
    <div className="min-h-screen md:flex">
      {/* First Tab stop: keyboard users skip the header and navigation on every page. */}
      <a
        href="#main"
        className="sr-only rounded-lg bg-surface px-3 py-2 text-sm font-medium shadow focus:not-sr-only focus:absolute focus:top-3 focus:left-4 focus:z-50"
      >
        Skip to content
      </a>
      <aside className="flex flex-col border-b border-border/70 bg-surface/60 backdrop-blur md:sticky md:top-0 md:h-screen md:w-64 md:shrink-0 md:border-r md:border-b-0">
        <div className="flex items-center justify-between px-5 py-4 md:py-6">
          <Link href="/app" aria-label="AgentForge home" className="rounded-lg">
            <Logo />
          </Link>
          <div className="flex items-center gap-2 md:hidden">
            <ThemeToggle />
            <button
              type="button"
              aria-label="Sign out"
              onClick={async () => {
                await signOut();
                router.replace("/login");
              }}
              className="inline-flex size-9 items-center justify-center rounded-lg border border-border text-muted hover:bg-surface-2 hover:text-fg"
            >
              <LogOut className="size-4" aria-hidden />
            </button>
          </div>
        </div>
        <nav aria-label="Main" className="px-3 md:flex-1">
          <ul className="flex gap-1 overflow-x-auto pb-3 text-sm md:flex-col md:pb-0">
            {items.map(({ href, label, icon: Icon }) => {
              const active = isActive(pathname, href);
              return (
                <li key={href}>
                  <Link
                    href={href}
                    aria-current={active ? "page" : undefined}
                    className={cn(
                      "relative flex items-center gap-3 rounded-xl px-3 py-2.5 whitespace-nowrap text-muted transition-colors hover:bg-surface-2 hover:text-fg",
                      active && "bg-surface-2 font-medium text-fg",
                    )}
                  >
                    {active && (
                      <span
                        aria-hidden
                        className="bg-brand absolute top-2 bottom-2 left-0 w-1 rounded-full"
                      />
                    )}
                    <Icon className={cn("size-[18px]", active && "text-accent")} aria-hidden />
                    {label}
                  </Link>
                </li>
              );
            })}
          </ul>
        </nav>
        <div className="hidden p-3 md:block">
          <div className="panel flex items-center gap-3 p-3">
            <span
              aria-hidden
              className="bg-brand flex size-9 shrink-0 items-center justify-center rounded-full text-xs font-semibold text-accent-fg"
            >
              {initials(user.email)}
            </span>
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-medium" title={user.email}>
                {user.email}
              </p>
              <p className="text-xs text-muted capitalize">
                {user.role}
                {user.can_review ? " · reviewer" : ""}
              </p>
            </div>
          </div>
          <div className="mt-2 flex items-center gap-2">
            <ThemeToggle />
            <button
              type="button"
              onClick={async () => {
                await signOut();
                router.replace("/login");
              }}
              className="inline-flex h-9 flex-1 items-center justify-center gap-2 rounded-lg border border-border text-sm text-muted transition-colors hover:bg-surface-2 hover:text-fg"
            >
              <LogOut className="size-4" aria-hidden />
              Sign out
            </button>
          </div>
        </div>
      </aside>
      <main
        id="main"
        tabIndex={-1}
        className="mx-auto w-full max-w-6xl min-w-0 flex-1 px-4 py-6 focus:outline-none md:px-8 md:py-8"
      >
        {children}
      </main>
    </div>
  );
}
