"use client";

import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { type Answer, useAnswerCalls } from "@/lib/answers";

const PURPOSE: Record<string, string> = {
  retrieve: "Search (embed the question)",
  draft: "Draft the answer",
  verify: "Verify a sentence",
};

/** Traceability: what each step took, and the exact prompt and reply of every model call. */
export function CallsPanel({ answer }: { answer: Answer }) {
  const [open, setOpen] = useState(false);
  const calls = useAnswerCalls(answer.id, open);
  const total = typeof answer.timings.total_ms === "number" ? answer.timings.total_ms : null;

  return (
    <details
      className="mt-5 border-t border-border pt-4 text-sm"
      onToggle={(e) => setOpen(e.currentTarget.open)}
    >
      <summary className="cursor-pointer font-medium">
        How this was answered
        {total !== null && (
          <span className="font-normal text-muted"> · {(total / 1000).toFixed(1)} s</span>
        )}
      </summary>
      {answer.reason && <p className="mt-2 text-muted">Outcome: {answer.reason}.</p>}
      {calls.isPending && open && <p className="mt-2 text-muted">Loading model calls…</p>}
      {calls.isError && (
        <p className="mt-2 text-danger" role="alert">
          {calls.error.message}
        </p>
      )}
      {calls.data && (
        <ol className="mt-3 space-y-2">
          {calls.data.map((call) => (
            <li key={call.id} className="rounded-md border border-border p-3">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-medium">{PURPOSE[call.purpose] ?? call.purpose}</span>
                <Badge tone={call.status === "ok" ? "ok" : "danger"}>{call.status}</Badge>
                <span className="text-muted">
                  {call.model} · {(call.latency_ms / 1000).toFixed(1)} s
                  {call.prompt_tokens !== null &&
                    ` · ${call.prompt_tokens} in / ${call.completion_tokens ?? 0} out tokens`}
                  {call.attempt > 1 && ` · attempt ${call.attempt}`}
                </span>
              </div>
              {call.purpose !== "retrieve" && (
                <details className="mt-2">
                  <summary className="cursor-pointer text-muted">Prompt and reply</summary>
                  {call.request.map((message, index) => (
                    <div key={index} className="mt-2">
                      <p className="text-xs font-medium text-muted uppercase">
                        {String(message.role)}
                      </p>
                      <pre className="max-h-64 overflow-auto rounded bg-bg p-2 text-xs whitespace-pre-wrap">
                        {String(message.content)}
                      </pre>
                    </div>
                  ))}
                  {call.response && (
                    <div className="mt-2">
                      <p className="text-xs font-medium text-muted uppercase">reply</p>
                      <pre className="max-h-64 overflow-auto rounded bg-bg p-2 text-xs whitespace-pre-wrap">
                        {call.response}
                      </pre>
                    </div>
                  )}
                </details>
              )}
            </li>
          ))}
        </ol>
      )}
    </details>
  );
}
