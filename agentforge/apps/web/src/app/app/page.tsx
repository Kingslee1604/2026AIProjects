"use client";

import { useRouter } from "next/navigation";

import { Button } from "@/components/ui/button";
import { Card, CardTitle } from "@/components/ui/card";
import { useSession, useSignOut } from "@/lib/session";

export default function Overview() {
  const router = useRouter();
  const { data: user } = useSession();
  const signOut = useSignOut();
  if (!user) return null;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Welcome</h1>
        <p className="mt-1 text-sm text-muted">
          Signed in as {user.email} ({user.role}
          {user.can_review ? ", reviewer" : ""}).
        </p>
      </div>
      <div className="grid gap-4 sm:grid-cols-2">
        <Card>
          <CardTitle>Ask your documents</CardTitle>
          <p className="mt-2 text-sm text-muted">
            Upload documents, ask questions, and get answers where every claim links to its source.
            Arrives in milestone M2.
          </p>
        </Card>
        <Card>
          <CardTitle>Fill a questionnaire</CardTitle>
          <p className="mt-2 text-sm text-muted">
            Upload an Excel questionnaire; AgentForge drafts every answer from your documents and a
            reviewer approves them. Arrives in milestone M4.
          </p>
        </Card>
      </div>
      <Card>
        <CardTitle>Security</CardTitle>
        <p className="mt-2 text-sm text-muted">
          Signed in on another device you no longer use? Sign out of every session at once.
        </p>
        <Button
          variant="secondary"
          className="mt-3"
          onClick={async () => {
            await signOut(true);
            router.replace("/login");
          }}
        >
          Sign out everywhere
        </Button>
      </Card>
    </div>
  );
}
