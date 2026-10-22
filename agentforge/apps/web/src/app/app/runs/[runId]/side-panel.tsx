"use client";

import { type KeyboardEvent, type ReactNode, useState } from "react";

import { EventList } from "./event-list";
import type { Selection } from "./phases";
import { ModelCallList } from "@/components/model-calls";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { formatPages } from "@/lib/format";
import {
  ITEM_STATUS,
  type Run,
  type RunEvent,
  type RunItem,
  type Trace,
  useRetryItem,
  useStepCalls,
} from "@/lib/runs";
import { cn } from "@/lib/utils";

type Tab = "input" | "output" | "calls" | "retrievals" | "events";

const TAB_LABEL: Record<Tab, string> = {
  input: "Input",
  output: "Output",
  calls: "Model calls",
  retrievals: "Retrievals",
  events: "Events",
};

interface Evidence {
  id: string;
  filename: string;
  page_start: number;
  page_end: number;
  section_path: string[];
  content: string;
  score: number;
  vector_distance: number | null;
}

interface Claim {
  text: string;
  status: string;
  kept: boolean;
  citations?: string[];
  flagged_source?: boolean;
}

/** Details of one question or step: what went in, what came out, the model calls with their
 * prompts and replies, the passages retrieved, and the events. */
export function SidePanel({
  run,
  trace,
  items,
  events,
  selection,
}: {
  run: Run;
  trace: Trace;
  items: RunItem[];
  events: RunEvent[];
  selection: Selection;
}) {
  const item =
    selection.kind === "item" ? items.find((i) => i.item_index === selection.index) : null;
  const itemSteps =
    selection.kind === "item"
      ? trace.steps.filter((s) => s.node_id === "answer" && s.item_index === selection.index)
      : [];
  // The chosen attempt, or the item's latest one.
  const stepId = selection.stepId ?? itemSteps.at(-1)?.id ?? null;
  const step = trace.steps.find((s) => s.id === stepId) ?? null;
  const tabs: [Tab, ...Tab[]] = item
    ? ["input", "output", "calls", "retrievals", "events"]
    : ["output", "calls", "events"];
  const [chosen, setChosen] = useState<Tab>(tabs[0]);
  const tab = tabs.includes(chosen) ? chosen : tabs[0];
  const calls = useStepCalls(run.id, tab === "calls" ? stepId : null);
  const retry = useRetryItem(run.id);

  const title = item
    ? `Question ${item.item_index + 1}`
    : step
      ? step.node_id === "plan"
        ? "Plan"
        : step.node_id === "export"
          ? "Export"
          : step.node_id
      : "Step";
  const shown = events.filter((e) =>
    item
      ? e.item_index === item.item_index
      : step
        ? e.step_id === step.id || e.node_id === step.node_id
        : false,
  );
  const canRetry =
    !!item &&
    run.status === "waiting_review" &&
    !["skipped", "pending", "answering"].includes(item.status);

  function onTabKey(event: KeyboardEvent) {
    const offset = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
    if (!offset) return;
    event.preventDefault();
    const next = tabs[(tabs.indexOf(tab) + offset + tabs.length) % tabs.length] ?? tabs[0];
    setChosen(next);
    document.getElementById(`tab-${next}`)?.focus();
  }

  return (
    <section aria-label={title} data-testid="step-panel" className="text-sm">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="font-semibold">{title}</h2>
        {canRetry && (
          <Button
            variant="secondary"
            size="sm"
            disabled={retry.isPending}
            onClick={() => item && retry.mutate(item.item_index)}
          >
            {retry.isPending ? "Asking…" : "Answer again"}
          </Button>
        )}
      </div>
      {retry.isError && (
        <p className="mt-2 text-danger" role="alert">
          {retry.error.message}
        </p>
      )}
      {step && (
        <p className="mt-1 text-muted">
          Attempt {step.attempt}
          {step.epoch > 1 ? ` of retry ${step.epoch - 1}` : ""} · {step.status}
          {step.error_class ? ` · ${step.error_class}` : ""}
        </p>
      )}

      <div role="tablist" aria-label="Details" className="mt-3 flex gap-1 border-b border-border">
        {tabs.map((t) => (
          <button
            key={t}
            id={`tab-${t}`}
            type="button"
            role="tab"
            aria-selected={tab === t}
            aria-controls={`panel-${t}`}
            tabIndex={tab === t ? 0 : -1}
            onClick={() => setChosen(t)}
            onKeyDown={onTabKey}
            className={cn(
              "-mb-px border-b-2 px-3 py-1.5",
              tab === t ? "border-accent font-medium" : "border-transparent text-muted",
            )}
          >
            {TAB_LABEL[t]}
          </button>
        ))}
      </div>

      <div
        id={`panel-${tab}`}
        role="tabpanel"
        aria-labelledby={`tab-${tab}`}
        tabIndex={0}
        className="mt-3 max-h-[60vh] overflow-auto"
      >
        {tab === "input" && item && <ItemInput item={item} />}
        {tab === "output" &&
          (item ? <ItemOutput item={item} /> : <StepOutput error={step?.error} />)}
        {tab === "calls" &&
          (stepId === null ? (
            <p className="text-muted">Not started yet.</p>
          ) : calls.isPending ? (
            <p className="text-muted">Loading model calls…</p>
          ) : calls.isError ? (
            <p className="text-danger" role="alert">
              {calls.error.message}
            </p>
          ) : calls.data.length === 0 ? (
            <p className="text-muted">No model calls in this step.</p>
          ) : (
            <ModelCallList calls={calls.data} />
          ))}
        {tab === "retrievals" && item && <Retrievals item={item} />}
        {tab === "events" && <EventList events={shown} />}
      </div>
    </section>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <dt className="text-xs font-medium text-muted uppercase">{label}</dt>
      <dd className="mt-0.5">{children}</dd>
    </div>
  );
}

function ItemInput({ item }: { item: RunItem }) {
  const plan = item.plan as { kind?: string; search_queries?: string[]; parts?: string[] };
  return (
    <dl className="space-y-3">
      <Field label="Question">{item.question}</Field>
      {item.section && <Field label="Section">{item.section}</Field>}
      {item.source_ref && <Field label="Row in the file">{item.source_ref}</Field>}
      {plan.kind && <Field label="Planned as">{plan.kind.replaceAll("_", " ")}</Field>}
      {plan.parts && plan.parts.length > 0 && (
        <Field label="Split into">
          <ol className="list-decimal pl-5">
            {plan.parts.map((p) => (
              <li key={p}>{p}</li>
            ))}
          </ol>
        </Field>
      )}
      {plan.search_queries && plan.search_queries.length > 0 && (
        <Field label="Searched for">
          <ul className="list-disc pl-5">
            {plan.search_queries.map((q) => (
              <li key={q}>{q}</li>
            ))}
          </ul>
        </Field>
      )}
    </dl>
  );
}

function ItemOutput({ item }: { item: RunItem }) {
  const status = ITEM_STATUS[item.status] ?? { label: item.status, tone: "neutral" as const };
  const claims = item.claims as unknown as Claim[];
  return (
    <dl className="space-y-3">
      <Field label="Status">
        <Badge tone={status.tone}>{status.label}</Badge>
        {item.confidence && <span className="ml-2 text-muted">{item.confidence} confidence</span>}
      </Field>
      {item.answer && <Field label="Answer">{item.answer}</Field>}
      {item.reason && <Field label="Why">{item.reason}</Field>}
      {claims.length > 0 && (
        <Field label="Sentences checked">
          <ul className="space-y-1">
            {claims.map((c, index) => (
              <li key={index} className={c.kept ? "" : "text-muted line-through"}>
                <Badge tone={c.status === "verified" ? "ok" : c.kept ? "warn" : "danger"}>
                  {c.status}
                </Badge>{" "}
                {c.flagged_source && <Badge tone="danger">Check this source</Badge>} {c.text}
              </li>
            ))}
          </ul>
        </Field>
      )}
    </dl>
  );
}

function StepOutput({ error }: { error: string | null | undefined }) {
  return error ? (
    <p className="text-danger" role="alert">
      {error}
    </p>
  ) : (
    <p className="text-muted">No errors. The results are in the questions it covered.</p>
  );
}

function Retrievals({ item }: { item: RunItem }) {
  const evidence = item.evidence as unknown as Evidence[];
  if (evidence.length === 0) return <p className="text-muted">No passages retrieved yet.</p>;
  return (
    <ol className="space-y-2">
      {evidence.map((e) => (
        <li
          key={e.id}
          className="rounded-xl border border-border/70 bg-surface-2/40 p-3"
          data-testid="retrieval"
        >
          <p className="font-medium">
            {e.filename}{" "}
            <span className="font-normal text-muted">{formatPages(e.page_start, e.page_end)}</span>
          </p>
          <p className="text-xs text-muted">
            Score {e.score.toFixed(3)}
            {e.vector_distance !== null && ` · distance ${e.vector_distance.toFixed(3)}`}
            {e.section_path.length > 0 && ` · ${e.section_path.join(" / ")}`}
          </p>
          <p className="mt-1 line-clamp-4 whitespace-pre-wrap">{e.content}</p>
        </li>
      ))}
    </ol>
  );
}
