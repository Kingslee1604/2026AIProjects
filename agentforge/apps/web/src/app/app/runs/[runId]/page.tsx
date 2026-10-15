"use client";

import Link from "next/link";
import { useParams } from "next/navigation";

import { RunStatusBadge } from "../run-status";
import { Card, CardTitle } from "@/components/ui/card";
import { formatAge, formatBytes } from "@/lib/format";
import { downloadUrl, useArtifacts, useRun } from "@/lib/runs";

export default function RunPage() {
  const { runId } = useParams<{ runId: string }>();
  const run = useRun(runId);
  const artifacts = useArtifacts(runId, run.data?.status === "succeeded");

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
  const percent = r.item_count ? Math.round((100 * r.items_done) / r.item_count) : 0;

  return (
    <div className="space-y-6">
      <div>
        <Link href="/app/runs" className="text-sm text-accent hover:underline">
          ← Questionnaires
        </Link>
        <div className="mt-1 flex flex-wrap items-center gap-3">
          <h1 className="text-2xl font-semibold">{r.title}</h1>
          <RunStatusBadge status={r.status} />
        </div>
        <p className="mt-1 text-sm text-muted">
          Started <time dateTime={r.created_at}>{formatAge(r.created_at)}</time>
        </p>
      </div>

      <Card>
        <CardTitle>Progress</CardTitle>
        <p className="mt-2 text-sm">
          {r.items_done} of {r.item_count} questions answered
        </p>
        <div
          role="progressbar"
          aria-label="Questions answered"
          aria-valuemin={0}
          aria-valuemax={r.item_count}
          aria-valuenow={r.items_done}
          className="mt-2 h-2 overflow-hidden rounded-full bg-bg"
        >
          <div className="h-full bg-accent transition-all" style={{ width: `${percent}%` }} />
        </div>
        {r.status === "waiting_review" && (
          <p className="mt-3 text-sm text-warn">
            Every answer is ready and waits for a reviewer. The filled-in file is created once the
            review is complete.
          </p>
        )}
        {r.error && (
          <p className="mt-3 text-sm text-danger" role="alert">
            {r.error}
          </p>
        )}
      </Card>

      {r.status === "succeeded" && (
        <Card>
          <CardTitle>Download</CardTitle>
          {artifacts.data?.length ? (
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
            <p className="mt-3 text-sm text-muted">{artifacts.isPending ? "Loading…" : "None"}</p>
          )}
        </Card>
      )}
    </div>
  );
}
