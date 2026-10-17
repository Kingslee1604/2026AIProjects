"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { type FormEvent, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input, Label } from "@/components/ui/input";
import { api, problemTitle, setCsrfToken } from "@/lib/api/client";

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
    <main className="flex min-h-screen items-center justify-center p-6">
      <Card className="w-full max-w-sm">
        <h1 className="text-xl font-semibold">Sign in to AgentForge</h1>
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
          <Button type="submit" className="w-full" disabled={pending}>
            {pending ? "Signing in…" : "Sign in"}
          </Button>
        </form>
      </Card>
    </main>
  );
}
