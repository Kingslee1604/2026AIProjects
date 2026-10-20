"use client";

import { useEffect, useState } from "react";

import { CallsPanel } from "./calls-panel";
import { ClaimsView } from "@/components/claims-view";
import { Badge } from "@/components/ui/badge";
import { Card, CardTitle } from "@/components/ui/card";
import { type Answer, useAnswer } from "@/lib/answers";

export function AnswerView({ answerId }: { answerId: string }) {
  const answer = useAnswer(answerId);

  if (answer.isPending) {
    return (
      <p className="text-sm text-muted" role="status">
        Loading answer…
      </p>
    );
  }
  if (answer.isError) {
    return (
      <p className="text-sm text-danger" role="alert">
        {answer.error.message}
      </p>
    );
  }
  if (answer.data === null) {
    return (
      <p className="text-sm text-danger" role="alert">
        Answer not found.
      </p>
    );
  }
  return <AnswerCard answer={answer.data} />;
}

function Waiting({ answer }: { answer: Answer }) {
  const [seconds, setSeconds] = useState(() =>
    Math.round((Date.now() - new Date(answer.created_at).getTime()) / 1000),
  );
  useEffect(() => {
    const timer = setInterval(() => setSeconds((s) => s + 1), 1000);
    return () => clearInterval(timer);
  }, []);
  return (
    <div role="status" aria-live="polite" className="text-sm">
      <p className="font-medium">
        {answer.status === "pending" ? "Waiting for the worker…" : "Reading your documents…"}
      </p>
      <p className="mt-1 text-muted">
        {Math.max(seconds, 0)} s. On a CPU-only machine an answer takes 15 to 60 seconds.
        {answer.error && ` Retrying after a problem: ${answer.error}`}
      </p>
    </div>
  );
}

function AnswerCard({ answer }: { answer: Answer }) {
  return (
    <Card aria-labelledby="answer-title">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <CardTitle id="answer-title">Answer</CardTitle>
          <p className="mt-1 text-sm text-muted">{answer.question}</p>
        </div>
        {answer.confidence && (
          <Badge tone={answer.confidence === "high" ? "ok" : "warn"}>
            {answer.confidence} confidence
          </Badge>
        )}
      </div>

      <div className="mt-4">
        {!["answered", "insufficient_evidence", "failed"].includes(answer.status) ? (
          <Waiting answer={answer} />
        ) : answer.status === "failed" ? (
          <p className="text-sm text-danger" role="alert">
            The question could not be answered: {answer.error ?? "unknown error"}. Try again later;
            if it keeps failing, check that the model server is running.
          </p>
        ) : answer.status === "insufficient_evidence" ? (
          <div
            className="rounded-xl border border-border/70 border-l-4 bg-surface-2/40 border-l-warn p-4"
            data-testid="insufficient"
          >
            <p className="font-medium">Insufficient evidence in the knowledge base.</p>
            <p className="mt-1 text-sm text-muted">
              Nothing in the selected documents answers this, so no answer was made up. Add a
              document that covers it, or rephrase the question.
            </p>
          </div>
        ) : (
          <ClaimsView claims={answer.claims} evidence={answer.evidence} />
        )}
      </div>

      {["answered", "insufficient_evidence", "failed"].includes(answer.status) && (
        <CallsPanel answer={answer} />
      )}
    </Card>
  );
}
