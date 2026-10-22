"use client";

import { BadgeCheck, ClipboardList, type LucideIcon, PenLine, UserCheck } from "lucide-react";

import type { Run, RunEvent, RunItem, Trace } from "@/lib/runs";
import { cn } from "@/lib/utils";

type State = "waiting" | "working" | "done" | "needs-you" | "failed";

const STATE: Record<State, { label: string; text: string }> = {
  waiting: { label: "Waiting", text: "text-muted" },
  working: { label: "Working", text: "text-accent" },
  done: { label: "Done", text: "text-ok" },
  "needs-you": { label: "Needs a reviewer", text: "text-warn" },
  failed: { label: "Failed", text: "text-danger" },
};

interface Agent {
  key: string;
  name: string;
  role: string;
  icon: LucideIcon;
  state: State;
  progress: number | null; // 0..100, or null when it does not apply
  lines: string[];
}

interface ClaimJson {
  status?: string;
  kept?: boolean;
  flagged_source?: boolean;
}

function agents(run: Run, trace: Trace, items: RunItem[], events: RunEvent[]): Agent[] {
  const plan = trace.steps.filter((s) => s.node_id === "plan");
  const planned = plan.some((s) => s.status === "succeeded");
  const total = items.length;
  const done = items.filter((i) => !["pending", "answering"].includes(i.status)).length;
  const answering = items.filter((i) => i.status === "answering").length;
  const insufficient = items.filter((i) => i.status === "insufficient_evidence").length;
  const failed = items.filter((i) => i.status === "failed").length;
  const claims = items.flatMap((i) => (i.claims as ClaimJson[]).filter((c) => c.kept));
  const verified = claims.filter((c) => c.status === "verified").length;
  const rescued = claims.filter((c) => c.status === "rescued").length;
  const flagged = claims.filter((c) => c.flagged_source).length;
  const stopped = ["failed", "cancelled", "timed_out"].includes(run.status);
  const answeringDone = total > 0 && done === total;
  const waitingReview = events.some((e) => e.type === "review_waiting");
  const reviewed = events.some((e) => e.type === "review_completed");
  const pct = (n: number) => (total ? Math.round((100 * n) / total) : 0);

  return [
    {
      key: "planner",
      name: "Planner",
      role: "Classifies and splits the questions",
      icon: ClipboardList,
      state: planned ? "done" : plan.length ? "working" : stopped ? "failed" : "waiting",
      progress: planned ? 100 : plan.length ? 50 : 0,
      lines: planned ? [`${total} questions planned`] : ["Reading the questionnaire"],
    },
    {
      key: "drafter",
      name: "Drafter",
      role: "Answers from your documents, with quotes",
      icon: PenLine,
      state: answeringDone ? "done" : answering ? "working" : stopped ? "failed" : "waiting",
      progress: pct(done),
      lines: [
        `${done} of ${total} questions done`,
        ...(answering ? [`${answering} being answered now`] : []),
        ...(insufficient ? [`${insufficient} without enough evidence`] : []),
        ...(failed ? [`${failed} failed`] : []),
      ],
    },
    {
      key: "verifier",
      name: "Verifier",
      role: "Checks every sentence against its source",
      icon: BadgeCheck,
      state: answeringDone ? "done" : done ? "working" : stopped ? "failed" : "waiting",
      progress: pct(done),
      lines: claims.length
        ? [
            `${verified} sentences proven word for word`,
            ...(rescued ? [`${rescued} confirmed by the AI check`] : []),
            ...(flagged ? [`${flagged} cite a flagged source`] : []),
          ]
        : ["No sentences checked yet"],
    },
    {
      key: "reviewer",
      name: "Reviewer",
      role: "A person approves, edits, or rejects",
      icon: UserCheck,
      state: reviewed ? "done" : waitingReview ? "needs-you" : stopped ? "failed" : "waiting",
      progress: reviewed ? 100 : null,
      lines: reviewed
        ? ["Review complete"]
        : waitingReview
          ? ["Answers are ready for review"]
          : ["Starts when every question is answered"],
    },
  ];
}

/** The run as its agents: who is working, how far along, and what they found. */
export function AgentPipeline({
  run,
  trace,
  items,
  events,
}: {
  run: Run;
  trace: Trace;
  items: RunItem[];
  events: RunEvent[];
}) {
  const list = agents(run, trace, items, events);
  return (
    <ol aria-label="Agents" className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
      {list.map((agent, index) => {
        const Icon = agent.icon;
        const state = STATE[agent.state];
        const working = agent.state === "working";
        return (
          <li
            key={agent.key}
            data-testid="agent-card"
            data-state={agent.state}
            className={cn("panel relative p-4", working && "glow")}
          >
            {index > 0 && (
              <span
                aria-hidden
                className="bg-brand absolute top-1/2 -left-4 hidden h-0.5 w-4 opacity-70 xl:block"
              />
            )}
            <div className="flex items-start gap-3">
              <span
                className={cn(
                  "flex size-10 shrink-0 items-center justify-center rounded-xl",
                  agent.state === "waiting" ? "bg-surface-2 text-muted" : "bg-brand text-accent-fg",
                )}
              >
                <Icon className="size-5" aria-hidden />
              </span>
              <div className="min-w-0">
                <h3 className="font-semibold">{agent.name}</h3>
                <p className={cn("text-xs font-medium", state.text)}>
                  {working && (
                    <span
                      aria-hidden
                      className="mr-1.5 inline-block size-1.5 rounded-full bg-current align-middle motion-safe:animate-pulse"
                    />
                  )}
                  {state.label}
                </p>
              </div>
            </div>
            <p className="mt-3 text-xs text-muted">{agent.role}</p>
            {agent.progress !== null && (
              <div
                role="progressbar"
                aria-label={`${agent.name} progress`}
                aria-valuemin={0}
                aria-valuemax={100}
                aria-valuenow={agent.progress}
                className="mt-3 h-1.5 overflow-hidden rounded-full bg-surface-2"
              >
                <div
                  className="bg-brand h-full rounded-full transition-[width] duration-500"
                  style={{ width: `${agent.progress}%` }}
                />
              </div>
            )}
            <ul className="mt-3 space-y-1 text-sm">
              {agent.lines.map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ul>
          </li>
        );
      })}
    </ol>
  );
}
