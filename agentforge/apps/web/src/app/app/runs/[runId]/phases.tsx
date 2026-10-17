"use client";

import type { ReactNode } from "react";

import { Badge } from "@/components/ui/badge";
import { ITEM_STATUS, type RunEvent, type RunItem, type Step, type Trace } from "@/lib/runs";
import { cn } from "@/lib/utils";

export type Selection =
  { kind: "item"; index: number; stepId: string | null } | { kind: "step"; stepId: string };

const STEP_TONE: Record<Step["status"], string> = {
  running: "border-accent text-accent",
  succeeded: "border-ok/50 text-ok",
  failed: "border-danger/50 text-danger",
  cancelled: "border-border text-muted",
};

const STEP_WORD: Record<Step["status"], string> = {
  running: "running",
  succeeded: "succeeded",
  failed: "failed",
  cancelled: "cancelled",
};

function StepChip({
  step,
  selected,
  onSelect,
  label,
}: {
  step: Step;
  selected: boolean;
  onSelect: () => void;
  label: string;
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      aria-pressed={selected}
      aria-label={`${label}, attempt ${step.attempt}${step.epoch > 1 ? ` (retry ${step.epoch - 1})` : ""}, ${STEP_WORD[step.status]}${step.error_class ? `: ${step.error_class}` : ""}`}
      className={cn(
        "rounded border px-1.5 text-xs tabular-nums",
        STEP_TONE[step.status],
        selected && "ring-2 ring-accent",
      )}
    >
      {step.status === "running" ? "…" : step.status === "succeeded" ? "✓" : "✕"}
      {step.attempt > 1 || step.epoch > 1 ? ` ${step.epoch}.${step.attempt}` : ""}
    </button>
  );
}

function Phase({
  title,
  summary,
  children,
}: {
  title: string;
  summary: string;
  children?: ReactNode;
}) {
  return (
    <section aria-label={title} className="border-t border-border pt-3 first:border-t-0 first:pt-0">
      <h3 className="text-sm font-semibold">
        {title} <span className="font-normal text-muted">· {summary}</span>
      </h3>
      {children && <div className="mt-2">{children}</div>}
    </section>
  );
}

/** Where on the run's timeline a step ran, as left and width percentages. */
function span(step: Step, start: number, end: number): { left: string; width: string } {
  const total = Math.max(1, end - start);
  const from = new Date(step.started_at).getTime();
  const to = step.finished_at ? new Date(step.finished_at).getTime() : end;
  return {
    left: `${((100 * (from - start)) / total).toFixed(2)}%`,
    width: `${Math.max(1, (100 * (to - from)) / total).toFixed(2)}%`,
  };
}

/** The run's steps grouped by phase, with one lane per question: its attempts, and when each
 * ran (lanes that overlap ran in parallel). */
export function Phases({
  trace,
  items,
  events,
  selection,
  onSelect,
}: {
  trace: Trace;
  items: RunItem[];
  events: RunEvent[];
  selection: Selection | null;
  onSelect: (selection: Selection) => void;
}) {
  const plan = trace.steps.filter((s) => s.node_id === "plan");
  const exports = trace.steps.filter((s) => s.node_id === "export");
  const attempts = new Map<number, Step[]>();
  for (const step of trace.steps) {
    if (step.node_id !== "answer" || step.item_index === null) continue;
    attempts.set(step.item_index, [...(attempts.get(step.item_index) ?? []), step]);
  }
  const answerSteps = trace.steps.filter((s) => s.node_id === "answer");
  const start = Math.min(...answerSteps.map((s) => new Date(s.started_at).getTime()));
  const end = Math.max(
    ...answerSteps.map((s) => (s.finished_at ? new Date(s.finished_at) : new Date()).getTime()),
  );
  const done = items.filter((i) => !["pending", "answering"].includes(i.status)).length;
  const waiting = events.some((e) => e.type === "review_waiting");
  const reviewed = events.some((e) => e.type === "review_completed");
  const selectedStep = selection?.stepId ?? null;

  return (
    <div className="space-y-4">
      <Phase
        title="Plan"
        summary={
          plan.length === 0
            ? "not started"
            : plan.some((s) => s.status === "succeeded")
              ? "questions classified and split"
              : "planning…"
        }
      >
        {plan.length > 0 && (
          <div className="flex flex-wrap gap-1">
            {plan.map((step) => (
              <StepChip
                key={step.id}
                step={step}
                label={`Plan from question ${(step.item_index ?? 0) + 1}`}
                selected={selectedStep === step.id}
                onSelect={() => onSelect({ kind: "step", stepId: step.id })}
              />
            ))}
          </div>
        )}
      </Phase>

      <Phase title="Answer" summary={`${done} of ${items.length} done`}>
        <ol className="divide-y divide-border text-sm">
          {items.map((item) => {
            const tries = attempts.get(item.item_index) ?? [];
            const status = ITEM_STATUS[item.status] ?? { label: item.status, tone: "neutral" };
            const active = selection?.kind === "item" && selection.index === item.item_index;
            return (
              <li
                key={item.item_index}
                data-testid="item-lane"
                data-item={item.item_index}
                data-status={item.status}
                className={cn("flex items-center gap-2 py-1.5", active && "bg-bg")}
              >
                <button
                  type="button"
                  onClick={() => onSelect({ kind: "item", index: item.item_index, stepId: null })}
                  aria-pressed={active}
                  className="min-w-0 flex-1 truncate text-left hover:underline"
                >
                  <span className="text-muted tabular-nums">{item.item_index + 1}. </span>
                  {item.question}
                </button>
                <Badge tone={status.tone}>{status.label}</Badge>
                <span className="flex shrink-0 gap-1">
                  {tries.map((step) => (
                    <StepChip
                      key={step.id}
                      step={step}
                      label={`Question ${item.item_index + 1}`}
                      selected={selectedStep === step.id}
                      onSelect={() =>
                        onSelect({ kind: "item", index: item.item_index, stepId: step.id })
                      }
                    />
                  ))}
                </span>
                <span
                  aria-hidden="true"
                  className="relative hidden h-2 w-28 shrink-0 rounded bg-bg sm:block"
                >
                  {tries.map((step) => (
                    <span
                      key={step.id}
                      className={cn(
                        "absolute top-0 h-2 rounded",
                        step.status === "succeeded"
                          ? "bg-ok/70"
                          : step.status === "running"
                            ? "bg-accent/70"
                            : "bg-danger/60",
                      )}
                      style={span(step, start, end)}
                    />
                  ))}
                </span>
              </li>
            );
          })}
        </ol>
      </Phase>

      <Phase
        title="Review"
        summary={reviewed ? "complete" : waiting ? "waiting for a reviewer" : "after answering"}
      />

      <Phase
        title="Export"
        summary={
          exports.some((s) => s.status === "succeeded")
            ? "file written"
            : exports.length
              ? "writing…"
              : "after review"
        }
      >
        {exports.length > 0 && (
          <div className="flex flex-wrap gap-1">
            {exports.map((step) => (
              <StepChip
                key={step.id}
                step={step}
                label="Export"
                selected={selectedStep === step.id}
                onSelect={() => onSelect({ kind: "step", stepId: step.id })}
              />
            ))}
          </div>
        )}
      </Phase>
    </div>
  );
}
