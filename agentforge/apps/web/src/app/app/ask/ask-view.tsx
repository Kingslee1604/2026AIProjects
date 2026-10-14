"use client";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { type FormEvent, useState } from "react";

import { AnswerView } from "./answer-view";
import { Button } from "@/components/ui/button";
import { Card, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/input";
import { useAsk, useRecentAnswers } from "@/lib/answers";
import { formatAge } from "@/lib/format";
import { useKnowledgeBases } from "@/lib/knowledge";

export function AskView() {
  const router = useRouter();
  const pathname = usePathname();
  const answerId = useSearchParams().get("answer");
  const kbs = useKnowledgeBases();
  const ask = useAsk();
  const recent = useRecentAnswers();
  const [question, setQuestion] = useState("");
  const [picked, setPicked] = useState<Set<string> | null>(null);

  const usable = (kbs.data ?? []).filter((kb) => kb.ready_count > 0);
  // Start with every knowledge base that has ready documents, chosen once when the list first
  // loads: knowledge bases created later are not added to the selection behind the user's back.
  // (Setting state during render is React's pattern for deriving initial state from data.)
  // Wait for the fresh list (not a cached one from before the latest uploads finished).
  if (picked === null && kbs.isSuccess && !kbs.isFetching) {
    setPicked(new Set(usable.map((kb) => kb.id)));
  }
  // Only send knowledge bases that still exist and are ready.
  const selected = new Set(usable.map((kb) => kb.id).filter((id) => picked?.has(id)));

  function toggle(id: string) {
    const next = new Set(selected);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setPicked(next);
  }

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    const created = await ask
      .mutateAsync({ question, knowledge_base_ids: [...selected] })
      .catch(() => null);
    if (created) router.push(`${pathname}?answer=${created.id}`);
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Ask</h1>
        <p className="mt-1 text-sm text-muted">
          Answers come only from your documents. Every sentence links to the passage that proves it;
          when the documents do not say, the answer says so instead of guessing.
        </p>
      </div>

      <Card>
        <form className="space-y-4" onSubmit={onSubmit}>
          <fieldset>
            <legend className="text-sm font-medium">Search in</legend>
            {kbs.isPending ? (
              <p className="mt-2 text-sm text-muted">Loading knowledge bases…</p>
            ) : usable.length === 0 ? (
              <p className="mt-2 text-sm text-muted">
                No knowledge base has ready documents yet.{" "}
                <Link href="/app/knowledge" className="text-accent hover:underline">
                  Upload documents first
                </Link>
                .
              </p>
            ) : (
              <div className="mt-2 flex flex-wrap gap-x-5 gap-y-2">
                {usable.map((kb) => (
                  <label key={kb.id} className="flex items-center gap-2 text-sm">
                    <input
                      type="checkbox"
                      className="size-4 accent-accent"
                      checked={selected.has(kb.id)}
                      onChange={() => toggle(kb.id)}
                    />
                    {kb.name}
                    <span className="text-muted">({kb.ready_count})</span>
                  </label>
                ))}
              </div>
            )}
          </fieldset>
          <div className="grid gap-1">
            <Label htmlFor="question">Question</Label>
            <textarea
              id="question"
              required
              minLength={3}
              maxLength={2000}
              rows={3}
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              onKeyDown={(e) => {
                // Ctrl/Cmd+Enter submits; plain Enter adds a line.
                if (e.key === "Enter" && (e.ctrlKey || e.metaKey))
                  e.currentTarget.form?.requestSubmit();
              }}
              placeholder="How long are access logs retained?"
              aria-describedby="question-hint"
              className="w-full rounded-md border border-border bg-surface px-3 py-2 text-sm placeholder:text-muted"
            />
            <p id="question-hint" className="text-xs text-muted">
              Ctrl+Enter to ask.
            </p>
          </div>
          {ask.isError && (
            <p className="text-sm text-danger" role="alert">
              {ask.error.message}
            </p>
          )}
          <Button
            type="submit"
            disabled={ask.isPending || question.trim().length < 3 || selected.size === 0}
          >
            {ask.isPending ? "Sending…" : "Ask"}
          </Button>
        </form>
      </Card>

      {answerId && <AnswerView answerId={answerId} />}

      {(recent.data?.length ?? 0) > 0 && (
        <Card>
          <CardTitle>Recent questions</CardTitle>
          <ul className="mt-3 divide-y divide-border text-sm">
            {recent.data?.map((a) => (
              <li key={a.id} className="flex items-center justify-between gap-4 py-2">
                <Link
                  href={`${pathname}?answer=${a.id}`}
                  aria-current={a.id === answerId ? "true" : undefined}
                  className="min-w-0 truncate text-accent hover:underline"
                >
                  {a.question}
                </Link>
                <span className="shrink-0 text-muted">
                  <time dateTime={a.created_at}>{formatAge(a.created_at)}</time>
                </span>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  );
}
