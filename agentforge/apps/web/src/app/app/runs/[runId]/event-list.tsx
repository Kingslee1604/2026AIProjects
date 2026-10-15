import type { RunEvent } from "@/lib/runs";

const LABEL: Record<string, string> = {
  run_created: "Run created",
  run_started: "Started",
  plan_done: "Questions planned",
  item_done: "Answered",
  item_failed: "Could not answer",
  item_retry_requested: "Asked to answer again",
  review_waiting: "Waiting for review",
  review_decided: "Reviewer decided",
  review_bulk_approved: "Verified answers approved",
  review_completed: "Review completed",
  review_done: "Continuing after review",
  export_done: "File written",
  export_retry_requested: "Asked to export again",
  run_succeeded: "Done",
  run_failed: "Failed",
  run_cancelled: "Cancelled",
};

export function EventList({ events }: { events: RunEvent[] }) {
  if (events.length === 0) return <p className="text-sm text-muted">No events yet.</p>;
  return (
    <ol className="space-y-1 text-sm">
      {events.map((event) => (
        <li
          key={event.seq}
          data-testid="event"
          data-seq={event.seq}
          className="flex gap-3 tabular-nums"
        >
          <time dateTime={event.created_at} className="shrink-0 text-muted">
            {new Date(event.created_at).toLocaleTimeString()}
          </time>
          <span>
            {LABEL[event.type] ?? event.type}
            {event.item_index !== null && (
              <span className="text-muted"> · question {event.item_index + 1}</span>
            )}
            {event.epoch !== null && event.epoch > 1 && (
              <span className="text-muted"> · retry {event.epoch - 1}</span>
            )}
          </span>
        </li>
      ))}
    </ol>
  );
}
