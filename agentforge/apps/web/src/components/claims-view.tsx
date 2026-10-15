"use client";

import { useRef, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { CLAIM_STATUS, type Claim, type Evidence } from "@/lib/answers";
import { formatPages } from "@/lib/format";
import { highlightQuote } from "@/lib/highlight";
import { cn } from "@/lib/utils";

const BORDER = { ok: "border-l-ok", warn: "border-l-warn", danger: "border-l-danger" } as const;

/** An answer as its checked sentences, colored by how well they are supported; choosing one
 * shows the cited passage with the quote highlighted. Used by Ask and by review. */
export function ClaimsView({ claims, evidence }: { claims: Claim[]; evidence: Evidence[] }) {
  const kept = claims.filter((c) => c.kept);
  const removed = claims.filter((c) => !c.kept);
  const [selected, setSelected] = useState(0);
  const sourceRef = useRef<HTMLDivElement>(null);
  const byId = new Map(evidence.map((e) => [e.id, e]));
  const claim = kept[selected] as Claim | undefined;

  return (
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
                      {c.citations.map((id) => byId.get(id)?.filename ?? id).join(", ")}
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
                <li key={`${index}-${c.text}`} className="rounded-md border border-border p-2">
                  <p className="line-through decoration-danger">{c.drafted_text}</p>
                  <p className="mt-1 text-xs text-muted">Reason: {c.reason ?? "not verified"}</p>
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
          <SourcePassages claim={claim} evidence={byId} />
        ) : (
          <p className="text-sm text-muted">Select a sentence to see its source.</p>
        )}
      </div>
    </div>
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
