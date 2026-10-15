"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { type ReactNode, useEffect } from "react";

import { Button } from "@/components/ui/button";
import { useSession, useSignOut } from "@/lib/session";
import { cn } from "@/lib/utils";

interface NavItem {
  href: string;
  label: string;
  adminOnly?: boolean;
  comingIn?: string;
}

const NAV: NavItem[] = [
  { href: "/app", label: "Overview" },
  { href: "/app/knowledge", label: "Knowledge" },
  { href: "/app/ask", label: "Ask" },
  { href: "/app/runs", label: "Questionnaires" },
  { href: "/app/reviews", label: "Reviews", comingIn: "M4" },
  { href: "/app/admin", label: "Admin", adminOnly: true },
];

/** "/app" matches only itself; sections also match their sub-pages. */
function isActive(pathname: string, href: string): boolean {
  return href === "/app" ? pathname === href : pathname === href || pathname.startsWith(`${href}/`);
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

  return (
    <div className="min-h-screen">
      <header className="border-b border-border bg-surface">
        <div className="mx-auto flex h-14 max-w-6xl items-center justify-between px-4">
          <Link href="/app" className="font-semibold tracking-tight">
            AgentForge
          </Link>
          <div className="flex items-center gap-3 text-sm">
            <span className="text-muted">
              {user.email} · {user.role}
            </span>
            <Button
              variant="secondary"
              size="sm"
              onClick={async () => {
                await signOut();
                router.replace("/login");
              }}
            >
              Sign out
            </Button>
          </div>
        </div>
      </header>
      <div className="mx-auto flex max-w-6xl gap-8 px-4 py-6">
        <nav aria-label="Main" className="w-44 shrink-0">
          <ul className="space-y-1 text-sm">
            {NAV.filter((item) => !item.adminOnly || user.role === "admin").map((item) => (
              <li key={item.href}>
                {item.comingIn ? (
                  <span
                    className="flex justify-between rounded-md px-3 py-2 text-muted"
                    aria-disabled="true"
                  >
                    {item.label}
                    <span className="text-xs">{item.comingIn}</span>
                  </span>
                ) : (
                  <Link
                    href={item.href}
                    aria-current={isActive(pathname, item.href) ? "page" : undefined}
                    className={cn(
                      "block rounded-md px-3 py-2 hover:bg-surface",
                      isActive(pathname, item.href) && "bg-surface font-medium",
                    )}
                  >
                    {item.label}
                  </Link>
                )}
              </li>
            ))}
          </ul>
        </nav>
        <main className="min-w-0 flex-1">{children}</main>
      </div>
    </div>
  );
}
