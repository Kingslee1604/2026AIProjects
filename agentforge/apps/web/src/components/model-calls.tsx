import { Badge } from "@/components/ui/badge";
import type { components } from "@/lib/api/schema";

export type ModelCall = components["schemas"]["ModelCallOut"];

const PURPOSE: Record<string, string> = {
  retrieve: "Search (embed the question)",
  plan: "Plan the questions",
  draft: "Draft the answer",
  verify: "Verify a sentence",
};

/** Every model call with its outcome, and the exact prompt and reply behind it. */
export function ModelCallList({ calls }: { calls: ModelCall[] }) {
  return (
    <ol className="space-y-2">
      {calls.map((call) => (
        <li key={call.id} className="rounded-md border border-border p-3" data-testid="model-call">
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
                  <p className="text-xs font-medium text-muted uppercase">{String(message.role)}</p>
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
  );
}
