"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { type FormEvent, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardTitle } from "@/components/ui/card";
import { Input, Label } from "@/components/ui/input";
import { useCreateKnowledgeBase, useKnowledgeBases } from "@/lib/knowledge";

export default function KnowledgePage() {
  const router = useRouter();
  const kbs = useKnowledgeBases();
  const create = useCreateKnowledgeBase();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");

  async function onCreate(event: FormEvent) {
    event.preventDefault();
    const created = await create.mutateAsync({ name, description }).catch(() => null);
    if (created) router.push(`/app/knowledge/${created.id}`);
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-semibold tracking-tight">Knowledge</h1>
        <p className="mt-1 text-sm text-muted">
          A knowledge base is a set of documents that answers are drawn from: policies, reports, and
          previous questionnaires.
        </p>
      </div>

      <Card>
        <CardTitle>Knowledge bases</CardTitle>
        {kbs.isPending ? (
          <p className="mt-3 text-sm text-muted" role="status">
            Loading…
          </p>
        ) : kbs.isError ? (
          <p className="mt-3 text-sm text-danger" role="alert">
            {kbs.error.message}
          </p>
        ) : kbs.data.length === 0 ? (
          <p className="mt-3 text-sm text-muted">No knowledge bases yet. Create one below.</p>
        ) : (
          <ul className="mt-3 divide-y divide-border">
            {kbs.data.map((kb) => (
              <li key={kb.id} className="flex items-center justify-between gap-4 py-3">
                <div className="min-w-0">
                  <Link
                    href={`/app/knowledge/${kb.id}`}
                    className="font-medium text-accent hover:underline"
                  >
                    {kb.name}
                  </Link>
                  {kb.description && (
                    <p className="truncate text-sm text-muted">{kb.description}</p>
                  )}
                </div>
                <span className="shrink-0 text-sm text-muted">
                  {kb.ready_count} of {kb.document_count} document
                  {kb.document_count === 1 ? "" : "s"} ready
                </span>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card>
        <CardTitle>New knowledge base</CardTitle>
        <form className="mt-3 grid gap-3 sm:max-w-md" onSubmit={onCreate}>
          <div className="grid gap-1">
            <Label htmlFor="kb-name">Name</Label>
            <Input
              id="kb-name"
              required
              maxLength={200}
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Security policies"
            />
          </div>
          <div className="grid gap-1">
            <Label htmlFor="kb-description">Description (optional)</Label>
            <Input
              id="kb-description"
              maxLength={2000}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          </div>
          {create.isError && (
            <p className="text-sm text-danger" role="alert">
              {create.error.message}
            </p>
          )}
          <div>
            <Button type="submit" disabled={create.isPending || !name.trim()}>
              {create.isPending ? "Creating…" : "Create"}
            </Button>
          </div>
        </form>
      </Card>
    </div>
  );
}
