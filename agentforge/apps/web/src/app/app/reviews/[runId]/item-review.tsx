"use client";

import { type FormEvent, useState } from "react";

import { ClaimsView } from "@/components/claims-view";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { Claim, Evidence } from "@/lib/answers";
import { DECISION, type ReviewItem } from "@/lib/reviews";
import type { RunItem } from "@/lib/runs";

export type Mode = "view" | "edit" | "reject";

/** What the export writes for a question the documents do not answer (qa/pipeline.py). */
const INSUFFICIENT = "Insufficient evidence in the knowledge base.";

/** One answer under review: the question, the checked sentences with their sources, and the
 * reviewer's decision (approve, edit and approve, or reject with a reason). */
export function ItemReview({
  item,
  review,
  mode,
  canDecide,
  open,
  pending,
  onMode,
  onApprove,
  onEdit,
  onReject,
}: {
  item: RunItem;
  review: ReviewItem;
  mode: Mode;
  canDecide: boolean;
  open: boolean;
  pending: boolean;
  onMode: (mode: Mode) => void;
  onApprove: () => void;
  onEdit: (answer: string) => void;
  onReject: (reason: string) => void;
}) {
  const [draft, setDraft] = useState(review.edited_answer ?? item.answer ?? "");
  const [reason, setReason] = useState(review.reason ?? "");
  const decision = review.decision ? DECISION[review.decision] : null;
  // Exactly what the export writes into the answer cell.
  const exported =
    review.decision === "rejected"
      ? null
      : review.decision === "edited"
        ? review.edited_answer
        : item.status === "answered"
          ? item.answer
          : item.status === "insufficient_evidence"
            ? INSUFFICIENT
            : null;

  function saveEdit(event?: FormEvent) {
    event?.preventDefault();
    if (draft.trim()) onEdit(draft.trim());
  }

  function saveReject(event: FormEvent) {
    event.preventDefault();
    if (reason.trim()) onReject(reason.trim());
  }

  return (
    <article aria-labelledby="item-question" className="space-y-4" data-testid="review-item">
      <header>
        <p className="text-xs text-muted">
          Question {item.item_index + 1}
          {item.section && ` · ${item.section}`}
          {item.source_ref && ` · row ${item.source_ref}`}
        </p>
        <h2 id="item-question" className="mt-1 text-lg font-semibold">
          {item.question}
        </h2>
        <div className="mt-2 flex flex-wrap items-center gap-2 text-sm">
          {decision ? (
            <Badge tone={decision.tone} data-testid="decision" data-decision={review.decision}>
              {decision.label}
            </Badge>
          ) : (
            <Badge data-testid="decision" data-decision="">
              To decide
            </Badge>
          )}
          {review.auto_accept_candidate && <Badge tone="ok">Verified, high confidence</Badge>}
          {item.confidence && !review.auto_accept_candidate && (
            <span className="text-muted">{item.confidence} confidence</span>
          )}
        </div>
      </header>

      {item.status === "answered" ? (
        <ClaimsView
          key={item.item_index}
          claims={item.claims as unknown as Claim[]}
          evidence={item.evidence as unknown as Evidence[]}
        />
      ) : (
        <div className="rounded-md border border-border border-l-4 border-l-warn p-4 text-sm">
          <p className="font-medium">
            {item.status === "insufficient_evidence"
              ? "The documents do not answer this."
              : "This question could not be answered."}
          </p>
          <p className="mt-1 text-muted">
            {item.reason ?? "No answer was made up."} Approve to say so in the file, write the
            answer yourself with Edit, or reject it to leave the cell empty for someone to fill in.
          </p>
        </div>
      )}

      <div className="rounded-md bg-bg p-3 text-sm">
        <p className="text-xs font-medium text-muted uppercase">Goes into the file</p>
        <p className="mt-1 whitespace-pre-line" data-testid="exported-answer">
          {exported ?? <span className="text-muted">(left empty)</span>}
        </p>
        {review.decision === "rejected" && review.reason && (
          <p className="mt-1 text-muted">Rejected: {review.reason}</p>
        )}
      </div>

      {!canDecide ? (
        <p className="text-sm text-muted">
          You can read this review. Deciding needs the reviewer permission; an admin can grant it.
        </p>
      ) : !open ? null : mode === "edit" ? (
        <form className="space-y-2" onSubmit={saveEdit}>
          <label htmlFor="edited-answer" className="text-sm font-medium">
            Your answer
          </label>
          <textarea
            id="edited-answer"
            autoFocus
            rows={5}
            maxLength={4000}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) saveEdit();
              if (e.key === "Escape") onMode("view");
            }}
            aria-describedby="edit-hint"
            className="w-full rounded-md border border-border bg-surface px-3 py-2 text-sm"
          />
          <p id="edit-hint" className="text-xs text-muted">
            Ctrl+Enter to save and approve, Escape to cancel.
          </p>
          <div className="flex gap-2">
            <Button type="submit" size="sm" disabled={pending || !draft.trim()}>
              Save and approve
            </Button>
            <Button variant="ghost" size="sm" onClick={() => onMode("view")}>
              Cancel
            </Button>
          </div>
        </form>
      ) : mode === "reject" ? (
        <form className="space-y-2" onSubmit={saveReject}>
          <label htmlFor="reject-reason" className="text-sm font-medium">
            Why is this answer rejected?
          </label>
          <input
            id="reject-reason"
            autoFocus
            maxLength={1000}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Escape") onMode("view");
            }}
            aria-describedby="reject-hint"
            className="h-9 w-full rounded-md border border-border bg-surface px-3 text-sm"
          />
          <p id="reject-hint" className="text-xs text-muted">
            Enter to reject, Escape to cancel. The cell is left empty in the file.
          </p>
          <div className="flex gap-2">
            <Button
              type="submit"
              size="sm"
              className="bg-danger"
              disabled={pending || !reason.trim()}
            >
              Reject
            </Button>
            <Button variant="ghost" size="sm" onClick={() => onMode("view")}>
              Cancel
            </Button>
          </div>
        </form>
      ) : (
        <div className="flex flex-wrap gap-2">
          <Button size="sm" disabled={pending} onClick={onApprove}>
            Approve <kbd className="ml-1 text-xs opacity-70">A</kbd>
          </Button>
          <Button variant="secondary" size="sm" disabled={pending} onClick={() => onMode("edit")}>
            Edit <kbd className="ml-1 text-xs opacity-70">E</kbd>
          </Button>
          <Button variant="secondary" size="sm" disabled={pending} onClick={() => onMode("reject")}>
            Reject <kbd className="ml-1 text-xs opacity-70">R</kbd>
          </Button>
        </div>
      )}
    </article>
  );
}
