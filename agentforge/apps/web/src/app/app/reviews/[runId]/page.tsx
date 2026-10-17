"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { ItemReview, type Mode } from "./item-review";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardTitle } from "@/components/ui/card";
import { formatBytes } from "@/lib/format";
import {
  DECISION,
  type Decision,
  useApproveSupported,
  useCompleteReview,
  useDecide,
  useReview,
} from "@/lib/reviews";
import { downloadUrl, useArtifacts, useRun, useRunItems } from "@/lib/runs";
import { useSession } from "@/lib/session";
import { cn } from "@/lib/utils";

export default function ReviewPage() {
  const { runId } = useParams<{ runId: string }>();
  const session = useSession();
  const run = useRun(runId);
  const items = useRunItems(runId);
  const review = useReview(runId);
  const decide = useDecide(runId);
  const approveAll = useApproveSupported(runId);
  const complete = useCompleteReview(runId);
  const artifacts = useArtifacts(runId, run.data?.status === "succeeded");
  const [position, setPosition] = useState(0);
  const [mode, setMode] = useState<Mode>("view");
  const completeRef = useRef<HTMLButtonElement>(null);

  const rows = review.data?.items ?? [];
  const byIndex = new Map((items.data ?? []).map((i) => [i.item_index, i]));
  const at = Math.min(position, Math.max(0, rows.length - 1));
  const current = rows[at];
  const currentItem = current ? byIndex.get(current.item_index) : undefined;
  const open = review.data?.status === "open";
  const canDecide = session.data?.role === "admin" || !!session.data?.can_review;
  const undecided = rows.filter((r) => !r.decision).length;
  const candidates = rows.filter((r) => !r.decision && r.auto_accept_candidate).length;
  const counts = Object.fromEntries(
    (Object.keys(DECISION) as Decision[]).map((d) => [
      d,
      rows.filter((r) => r.decision === d).length,
    ]),
  ) as Record<Decision, number>;
  const error = decide.error ?? approveAll.error ?? complete.error;

  function move(by: number) {
    setMode("view");
    setPosition(Math.min(Math.max(at + by, 0), Math.max(0, rows.length - 1)));
  }

  async function submit(decision: Decision, extra: { edited_answer?: string; reason?: string }) {
    if (!current) return;
    const saved = await decide
      .mutateAsync({ itemIndex: current.item_index, decision, ...extra })
      .catch(() => null);
    if (!saved) return;
    setMode("view");
    // On to the next answer without a decision (after this one, then from the top).
    const order = [...rows.keys()].filter((p) => p !== at && !rows[p]?.decision);
    const next = order.find((p) => p > at) ?? order[0];
    if (next !== undefined) setPosition(next);
  }

  // Keyboard: A approve, E edit, R reject, J/K next and previous (not while typing).
  const keys = useRef<(event: KeyboardEvent) => void>(() => undefined);
  const onKey = (event: KeyboardEvent) => {
    if (event.ctrlKey || event.metaKey || event.altKey || mode !== "view") return;
    if ((event.target as HTMLElement).closest("input, textarea, select, [contenteditable]")) return;
    const key = event.key.toLowerCase();
    if (key === "j") move(1);
    else if (key === "k") move(-1);
    else if (canDecide && open && current && !decide.isPending && key === "a")
      void submit("approved", {});
    else if (canDecide && open && current && key === "e") setMode("edit");
    else if (canDecide && open && current && key === "r") setMode("reject");
    else return;
    event.preventDefault();
  };
  useEffect(() => {
    keys.current = onKey; // the latest state, for the one listener below
  });
  useEffect(() => {
    const listener = (event: KeyboardEvent) => keys.current(event);
    document.addEventListener("keydown", listener);
    return () => document.removeEventListener("keydown", listener);
  }, []);

  // Every answer decided: the next step is to complete the review.
  useEffect(() => {
    if (open && rows.length > 0 && undecided === 0) completeRef.current?.focus();
  }, [open, rows.length, undecided]);

  if (review.data === null || run.data === null) {
    return (
      <div className="space-y-3">
        <h1 className="text-2xl font-semibold">Review not found</h1>
        <Link href="/app/reviews" className="text-sm text-accent hover:underline">
          Back to reviews
        </Link>
      </div>
    );
  }
  if (!review.data || !run.data || !items.data) {
    return (
      <p className="text-sm text-muted" role="status">
        {review.isError ? review.error.message : "Loading…"}
      </p>
    );
  }

  return (
    <div className="space-y-6">
      <div>
        <Link href="/app/reviews" className="text-sm text-accent hover:underline">
          ← Reviews
        </Link>
        <h1 className="mt-1 text-2xl font-semibold">{run.data.title}</h1>
        <p className="mt-1 text-sm text-muted" data-testid="review-counts">
          {undecided} to decide · {counts.approved} approved · {counts.edited} edited ·{" "}
          {counts.rejected} rejected
        </p>
      </div>

      {open ? (
        canDecide && (
          <div className="flex flex-wrap items-center gap-3">
            <Button
              variant="secondary"
              disabled={candidates === 0 || approveAll.isPending}
              onClick={() => approveAll.mutate(undefined)}
            >
              Approve all verified ({candidates})
            </Button>
            <Button
              ref={completeRef}
              disabled={undecided > 0 || complete.isPending}
              onClick={() => complete.mutate(undefined)}
            >
              {complete.isPending ? "Completing…" : "Complete review"}
            </Button>
            <p className="text-xs text-muted">
              Keys: <kbd>A</kbd> approve · <kbd>E</kbd> edit · <kbd>R</kbd> reject · <kbd>J</kbd>/
              <kbd>K</kbd> next/previous
            </p>
          </div>
        )
      ) : (
        <Card data-testid="review-complete">
          <CardTitle>Review complete</CardTitle>
          {run.data.status === "succeeded" ? (
            artifacts.data?.length ? (
              <ul className="mt-3 divide-y divide-border text-sm">
                {artifacts.data.map((a) => (
                  <li key={a.id} className="flex items-center justify-between gap-4 py-2">
                    <a href={downloadUrl(a)} className="text-accent hover:underline" download>
                      {a.filename}
                    </a>
                    <span className="text-muted">{formatBytes(a.size_bytes)}</span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="mt-2 text-sm text-muted">Loading the file…</p>
            )
          ) : (
            <p className="mt-2 text-sm text-muted" role="status">
              Writing the answers into the file…
            </p>
          )}
          <Link
            href={`/app/runs/${runId}`}
            className="mt-3 inline-block text-sm text-accent hover:underline"
          >
            Open the run
          </Link>
        </Card>
      )}

      {error && (
        <p className="text-sm text-danger" role="alert">
          {error.message}
        </p>
      )}

      <div className="grid gap-6 lg:grid-cols-[18rem_minmax(0,1fr)]">
        <Card className="lg:self-start">
          <CardTitle>Answers</CardTitle>
          <ol className="mt-3 space-y-1 text-sm" aria-label="Answers to review">
            {rows.map((row, p) => {
              const item = byIndex.get(row.item_index);
              const decision = row.decision ? DECISION[row.decision] : null;
              return (
                <li key={row.item_index}>
                  <button
                    type="button"
                    aria-current={p === at ? "true" : undefined}
                    onClick={() => {
                      setMode("view");
                      setPosition(p);
                    }}
                    data-testid="review-row-item"
                    className={cn(
                      "flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left hover:bg-bg",
                      p === at && "bg-bg font-medium",
                    )}
                  >
                    <span className="min-w-0 flex-1 truncate">
                      <span className="text-muted tabular-nums">{row.item_index + 1}. </span>
                      {item?.question}
                    </span>
                    {decision ? (
                      <Badge tone={decision.tone}>{decision.label}</Badge>
                    ) : (
                      <Badge>To decide</Badge>
                    )}
                  </button>
                </li>
              );
            })}
          </ol>
        </Card>
        <Card>
          <p className="sr-only" aria-live="polite">
            {current ? `Answer ${at + 1} of ${rows.length}` : ""}
          </p>
          {current && currentItem ? (
            <ItemReview
              key={`${current.item_index}:${current.decision ?? ""}`}
              item={currentItem}
              review={current}
              mode={mode}
              canDecide={canDecide}
              open={open}
              pending={decide.isPending}
              onMode={setMode}
              onApprove={() => void submit("approved", {})}
              onEdit={(answer) => void submit("edited", { edited_answer: answer })}
              onReject={(reason) => void submit("rejected", { reason })}
            />
          ) : (
            <p className="text-sm text-muted">No answers in this review.</p>
          )}
        </Card>
      </div>
    </div>
  );
}
