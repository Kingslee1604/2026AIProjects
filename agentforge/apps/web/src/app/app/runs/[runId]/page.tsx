"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";

import { RunStatusBadge } from "../run-status";
import { EventList } from "./event-list";
import { Phases, type Selection } from "./phases";
import { SidePanel } from "./side-panel";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardTitle } from "@/components/ui/card";
import { ConfirmButton } from "@/components/ui/confirm-button";
import { formatAge, formatBytes, formatDuration } from "@/lib/format";
import { type StreamState, useRunStream } from "@/lib/run-stream";
import {
  downloadUrl,
  FINAL_RUN,
  type Trace,
  useArtifacts,
  useCancelRun,
  useRetryExport,
  useRun,
  useRunItems,
  useTrace,
} from "@/lib/runs";

const STREAM: Record<StreamState, { label: string; tone: "neutral" | "ok" | "warn" }> = {
  connecting: { label: "Connecting…", tone: "neutral" },
  live: { label: "Live", tone: "ok" },
  reconnecting: { label: "Reconnecting…", tone: "warn" },
  ended: { label: "Finished", tone: "neutral" },
};

export default function RunPage() {
  const { runId } = useParams<{ runId: string }>();
  const run = useRun(runId);
  const items = useRunItems(runId);
  const trace = useTrace(runId);
  const stream = useRunStream(runId);
  const artifacts = useArtifacts(runId, run.data?.status === "succeeded");
  const cancel = useCancelRun(runId);
  const retryExport = useRetryExport(runId);
  const [selection, setSelection] = useState<Selection | null>(null);

  if (run.data === null) {
    return (
      <div className="space-y-3">
        <h1 className="text-2xl font-semibold">Questionnaire not found</h1>
        <Link href="/app/runs" className="text-sm text-accent hover:underline">
          Back to questionnaires
        </Link>
      </div>
    );
  }
  if (!run.data) {
    return (
      <p className="text-sm text-muted" role="status">
        {run.isError ? run.error.message : "Loading…"}
      </p>
    );
  }
  const r = run.data;
  const live = STREAM[stream.state];
  const actionError = cancel.error ?? retryExport.error;

  return (
    <div className="space-y-6">
      <div>
        <Link href="/app/runs" className="text-sm text-accent hover:underline">
          ← Questionnaires
        </Link>
        <div className="mt-1 flex flex-wrap items-center gap-3">
          <h1 className="text-2xl font-semibold">{r.title}</h1>
          <RunStatusBadge status={r.status} />
          <Badge tone={live.tone} data-testid="live-state" data-state={stream.state}>
            {live.label}
          </Badge>
        </div>
        <div className="mt-2 flex flex-wrap items-center justify-between gap-3">
          <p className="text-sm text-muted">
            Started <time dateTime={r.created_at}>{formatAge(r.created_at)}</time> · {r.items_done}{" "}
            of {r.item_count} questions answered
          </p>
          <div className="flex flex-wrap gap-2">
            {r.status === "waiting_review" && (
              <Link href={`/app/reviews/${r.id}`} className={buttonVariants({ size: "sm" })}>
                Review answers
              </Link>
            )}
            {r.status === "succeeded" && (
              <Button
                variant="secondary"
                size="sm"
                disabled={retryExport.isPending}
                onClick={() => retryExport.mutate(undefined)}
              >
                {retryExport.isPending ? "Exporting…" : "Export again"}
              </Button>
            )}
            {!FINAL_RUN.has(r.status) && (
              <ConfirmButton
                label="Cancel run"
                confirmLabel="Cancel run"
                backLabel="Keep running"
                pendingLabel="Cancelling…"
                question="Stop answering this questionnaire?"
                pending={cancel.isPending}
                onConfirm={() => cancel.mutate(undefined)}
              />
            )}
          </div>
        </div>
        {actionError && (
          <p className="mt-2 text-sm text-danger" role="alert">
            {actionError.message}
          </p>
        )}
        {r.error && (
          <p className="mt-2 text-sm text-danger" role="alert">
            {r.error_class ? `${r.error_class}: ` : ""}
            {r.error}
          </p>
        )}
      </div>

      {trace.data && <Budget trace={trace.data} />}

      {r.status === "succeeded" && (artifacts.data?.length ?? 0) > 0 && (
        <Card>
          <CardTitle>Download</CardTitle>
          <ul className="mt-3 divide-y divide-border text-sm">
            {artifacts.data?.map((a) => (
              <li key={a.id} className="flex items-center justify-between gap-4 py-2">
                <a href={downloadUrl(a)} className="text-accent hover:underline" download>
                  {a.filename}
                </a>
                <span className="text-muted">{formatBytes(a.size_bytes)}</span>
              </li>
            ))}
          </ul>
        </Card>
      )}

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,26rem)]">
        <Card>
          <CardTitle>Steps</CardTitle>
          <div className="mt-3">
            {trace.data && items.data ? (
              <Phases
                trace={trace.data}
                items={items.data}
                events={stream.events}
                selection={selection}
                onSelect={setSelection}
              />
            ) : (
              <p className="text-sm text-muted" role="status">
                Loading…
              </p>
            )}
          </div>
        </Card>
        <Card className="lg:sticky lg:top-4 lg:self-start">
          {selection && trace.data && items.data ? (
            <SidePanel
              key={selection.kind === "item" ? `item-${selection.index}` : selection.stepId}
              run={r}
              trace={trace.data}
              items={items.data}
              events={stream.events}
              selection={selection}
            />
          ) : (
            <p className="text-sm text-muted">
              Choose a question or a step to see its input, output, model calls, retrieved passages,
              and events.
            </p>
          )}
        </Card>
      </div>

      <Card>
        <CardTitle>Events</CardTitle>
        {/* Focusable, so a keyboard can scroll it once it overflows. */}
        <div
          className="mt-3 max-h-80 overflow-auto"
          data-testid="run-events"
          tabIndex={0}
          role="region"
          aria-label="Run events"
        >
          <EventList events={stream.events} />
        </div>
      </Card>
    </div>
  );
}

function Budget({ trace }: { trace: Trace }) {
  return (
    <dl className="grid gap-3 sm:grid-cols-3" aria-label="Budget used">
      {[
        ["Model calls", `${trace.llm_calls} of ${trace.max_llm_calls}`],
        ["Tokens", trace.tokens.toLocaleString()],
        ["Time", `${formatDuration(trace.seconds)} of ${formatDuration(trace.max_seconds)}`],
      ].map(([label, value]) => (
        <div key={label} className="rounded-lg border border-border bg-surface px-4 py-3">
          <dt className="text-xs font-medium text-muted uppercase">{label}</dt>
          <dd className="mt-1 text-lg font-semibold tabular-nums">{value}</dd>
        </div>
      ))}
    </dl>
  );
}
