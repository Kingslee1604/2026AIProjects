"use client";

import { useState } from "react";

import { ModelCallList } from "@/components/model-calls";
import { type Answer, useAnswerCalls } from "@/lib/answers";

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
        <div className="mt-3">
          <ModelCallList calls={calls.data} />
        </div>
      )}
    </details>
  );
}
