"use client";

import { useEffect, useRef, useState } from "react";

import { CallsPanel } from "./calls-panel";
import { Badge } from "@/components/ui/badge";
import { Card, CardTitle } from "@/components/ui/card";
import { type Answer, CLAIM_STATUS, type Claim, type Evidence, useAnswer } from "@/lib/answers";
import { formatPages } from "@/lib/format";
import { highlightQuote } from "@/lib/highlight";
import { cn } from "@/lib/utils";

const BORDER = { ok: "border-l-ok", warn: "border-l-warn", danger: "border-l-danger" } as const;

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
  const kept = answer.claims.filter((c) => c.kept);
  const removed = answer.claims.filter((c) => !c.kept);
  const [selected, setSelected] = useState(0);
  const sourceRef = useRef<HTMLDivElement>(null);
  const evidence = new Map(answer.evidence.map((e) => [e.id, e]));
  const claim = kept[selected] as Claim | undefined;

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
            className="rounded-md border border-border border-l-4 border-l-warn p-4"
            data-testid="insufficient"
          >
            <p className="font-medium">Insufficient evidence in the knowledge base.</p>
            <p className="mt-1 text-sm text-muted">
              Nothing in the selected documents answers this, so no answer was made up. Add a
              document that covers it, or rephrase the question.
            </p>
          </div>
        ) : (
          <div className="grid gap-4 lg:grid-cols-2">
            <div>
              <p className="sr-only">Select a sentence to see the passage that supports it.</p>
              <ol className="space-y-2" aria-label="Answer sentences">
                {kept.map((c, index) => {
                  const status = CLAIM_STATUS[c.status];
                  return (
                    <li key={`${index}-${c.text}`}>
                      <button
                        type="button"
                        aria-pressed={index === selected}
                        aria-describedby={`claim-status-${index}`}
                        data-testid="claim"
                        data-status={c.status}
                        onClick={() => {
                          setSelected(index);
                          sourceRef.current?.focus();
                        }}
                        className={cn(
                          "w-full rounded-md border border-border border-l-4 p-3 text-left text-sm hover:bg-bg",
                          BORDER[status.tone],
                          index === selected && "bg-bg ring-1 ring-accent",
                        )}
                      >
                        <span className="block">{c.text}</span>
                        <span
                          id={`claim-status-${index}`}
                          className="mt-2 flex flex-wrap items-center gap-2"
                        >
                          <Badge tone={status.tone} title={status.help}>
                            {status.label}
                          </Badge>
                          <span className="text-xs text-muted">
                            {c.citations.map((id) => evidence.get(id)?.filename ?? id).join(", ")}
                          </span>
                        </span>
                      </button>
                    </li>
                  );
                })}
              </ol>
              {removed.length > 0 && (
                <details className="mt-3 text-sm">
                  <summary className="cursor-pointer text-muted">
                    {removed.length} drafted sentence{removed.length === 1 ? "" : "s"} removed by
                    verification
                  </summary>
                  <ul className="mt-2 space-y-2">
                    {removed.map((c, index) => (
                      <li
                        key={`${index}-${c.text}`}
                        className="rounded-md border border-border p-2"
                      >
                        <p className="line-through decoration-danger">{c.drafted_text}</p>
                        <p className="mt-1 text-xs text-muted">
                          Reason: {c.reason ?? "not verified"}
                        </p>
                      </li>
                    ))}
                  </ul>
                </details>
              )}
            </div>
            <div
              ref={sourceRef}
              tabIndex={-1}
              aria-live="polite"
              aria-label="Source passage"
              className="rounded-md border border-border p-4 focus:outline-2"
              data-testid="source-panel"
            >
              {claim ? (
                <SourcePassages claim={claim} evidence={evidence} />
              ) : (
                <p className="text-sm text-muted">Select a sentence to see its source.</p>
              )}
            </div>
          </div>
        )}
      </div>

      {["answered", "insufficient_evidence", "failed"].includes(answer.status) && (
        <CallsPanel answer={answer} />
      )}
    </Card>
  );
}

function SourcePassages({ claim, evidence }: { claim: Claim; evidence: Map<string, Evidence> }) {
  const cited = claim.citations.map((id) => evidence.get(id)).filter((e) => e !== undefined);
  return (
    <div className="space-y-4">
      <h3 className="text-sm font-semibold">Source</h3>
      {cited.map((e) => (
        <figure key={e.id} className="space-y-2">
          <figcaption className="text-sm">
            <span className="font-medium">{e.filename}</span>{" "}
            <span className="text-muted">{formatPages(e.page_start, e.page_end)}</span>
            {e.section_path.length > 0 && (
              <span className="block text-xs text-muted">{e.section_path.join(" / ")}</span>
            )}
          </figcaption>
          {e.deleted ? (
            <p className="text-sm text-muted italic">The source document was deleted.</p>
          ) : (
            <blockquote className="max-h-80 overflow-y-auto rounded bg-bg p-3 text-sm whitespace-pre-line">
              {highlightQuote(e.content, claim.quote).map((segment, index) =>
                segment.highlight ? (
                  <mark key={index} className="rounded bg-accent/20 px-0.5 text-fg">
                    {segment.text}
                  </mark>
                ) : (
                  <span key={index}>{segment.text}</span>
                ),
              )}
            </blockquote>
          )}
        </figure>
      ))}
    </div>
  );
}
