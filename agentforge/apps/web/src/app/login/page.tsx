"use client";

import { useQueryClient } from "@tanstack/react-query";
import { HardDrive, Quote, UserCheck } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { type FormEvent, useState } from "react";

import { Logo } from "@/components/brand/logo";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input, Label } from "@/components/ui/input";
import { api, problemTitle, setCsrfToken } from "@/lib/api/client";

const PROOF = [
  { icon: Quote, text: "Every sentence carries the quote that proves it, or says it cannot." },
  { icon: UserCheck, text: "A person approves every answer before it leaves." },
  { icon: HardDrive, text: "Runs on your own machine; documents never leave it." },
];

export default function LoginPage() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setPending(true);
    setError(null);
    const {
      data,
      error: problem,
      response,
    } = await api.POST("/v1/auth/login", {
      body: { email: String(form.get("email")), password: String(form.get("password")) },
    });
    setPending(false);
    if (data) {
      setCsrfToken(data.csrf_token);
      queryClient.setQueryData(["session"], data);
      router.replace("/app");
      return;
    }
    setError(
      response.status === 429
        ? "Too many failed attempts. Wait 15 minutes and try again."
        : problemTitle(problem, "Sign-in failed"),
    );
  }

  return (
    <main className="grid min-h-screen lg:grid-cols-2">
      <section
        aria-label="About AgentForge"
        className="relative hidden flex-col justify-between overflow-hidden border-r border-border/70 p-12 lg:flex"
      >
        <div
          aria-hidden
          className="pointer-events-none absolute -top-40 -left-40 size-[36rem] rounded-full bg-brand opacity-20 blur-3xl"
        />
        <Link href="/" aria-label="AgentForge home" className="relative w-fit rounded-lg">
          <Logo />
        </Link>
        <div className="relative max-w-md">
          <p className="text-3xl leading-tight font-semibold tracking-tight">
            Answers your auditors can <span className="text-brand">verify</span>.
          </p>
          <ul className="mt-8 space-y-4 text-sm text-muted">
            {PROOF.map(({ icon: Icon, text }) => (
              <li key={text} className="flex items-start gap-3">
                <span className="panel flex size-8 shrink-0 items-center justify-center rounded-lg">
                  <Icon className="size-4 text-accent" aria-hidden />
                </span>
                <span className="pt-1.5">{text}</span>
              </li>
            ))}
          </ul>
        </div>
        <p className="relative text-xs text-muted">Self-hosted · open-weight models · Apache-2.0</p>
      </section>
      <div className="flex items-center justify-center p-6">
        <Card className="w-full max-w-sm p-8" glow>
          <Link
            href="/"
            aria-label="AgentForge home"
            className="mb-8 block w-fit rounded-lg lg:hidden"
          >
            <Logo />
          </Link>
          <h1 className="text-2xl font-semibold tracking-tight">Sign in to AgentForge</h1>
          <p className="mt-1 text-sm text-muted">Accounts are created by an administrator.</p>
          <form className="mt-6 space-y-4" onSubmit={onSubmit} noValidate>
            <div className="space-y-1.5">
              <Label htmlFor="email">Email</Label>
              <Input
                id="email"
                name="email"
                type="email"
                autoComplete="username"
                required
                aria-invalid={error ? true : undefined}
                aria-describedby={error ? "login-error" : undefined}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="password">Password</Label>
              <Input
                id="password"
                name="password"
                type="password"
                autoComplete="current-password"
                required
                aria-invalid={error ? true : undefined}
                aria-describedby={error ? "login-error" : undefined}
              />
            </div>
            {error ? (
              <p id="login-error" role="alert" className="text-sm text-danger">
                {error}
              </p>
            ) : null}
            <Button type="submit" size="lg" className="w-full" disabled={pending}>
              {pending ? "Signing in…" : "Sign in"}
            </Button>
          </form>
        </Card>
      </div>
    </main>
  );
}
