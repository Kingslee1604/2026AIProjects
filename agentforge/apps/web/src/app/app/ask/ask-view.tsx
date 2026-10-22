"use client";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { type FormEvent, useState } from "react";

import { AnswerView } from "./answer-view";
import { KnowledgePicker, useKnowledgeSelection } from "@/components/knowledge-picker";
import { Button } from "@/components/ui/button";
import { Card, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/input";
import { useAsk, useRecentAnswers } from "@/lib/answers";
import { formatAge } from "@/lib/format";

export function AskView() {
  const router = useRouter();
  const pathname = usePathname();
  const answerId = useSearchParams().get("answer");
  const ask = useAsk();
  const recent = useRecentAnswers();
  const [question, setQuestion] = useState("");
  const knowledge = useKnowledgeSelection();
  const { selected } = knowledge;

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
        <h1 className="text-3xl font-semibold tracking-tight">Ask</h1>
        <p className="mt-1 text-sm text-muted">
          Answers come only from your documents. Every sentence links to the passage that proves it;
          when the documents do not say, the answer says so instead of guessing.
        </p>
      </div>

      <Card>
        <form className="space-y-4" onSubmit={onSubmit}>
          <KnowledgePicker legend="Search in" selection={knowledge} />
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
              className="w-full rounded-lg border border-border bg-surface-2/60 px-3 py-2 text-sm placeholder:text-muted"
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
