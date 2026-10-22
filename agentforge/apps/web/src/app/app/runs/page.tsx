"use client";

import Link from "next/link";
import { useState } from "react";

import { RunStatusBadge } from "./run-status";
import { buttonVariants } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { formatAge } from "@/lib/format";
import { RUN_FILTERS, useRuns } from "@/lib/runs";
import { cn } from "@/lib/utils";

export default function RunsPage() {
  const runs = useRuns();
  const [filter, setFilter] = useState("all");
  const statuses = RUN_FILTERS.find((f) => f.key === filter)?.statuses ?? null;
  const all = runs.data ?? [];
  const shown = statuses ? all.filter((r) => statuses.includes(r.status)) : all;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-3xl font-semibold tracking-tight">Questionnaires</h1>
          <p className="mt-1 text-sm text-muted">
            Upload a security questionnaire and the AI answers it from your knowledge bases. You
            review every answer before the filled-in file is ready to download.
          </p>
        </div>
        <Link href="/app/runs/new" className={cn(buttonVariants(), "shrink-0")}>
          New questionnaire
        </Link>
      </div>

      <div role="group" aria-label="Show" className="flex flex-wrap gap-2">
        {RUN_FILTERS.map((f) => {
          const count = f.statuses
            ? all.filter((r) => f.statuses?.includes(r.status)).length
            : all.length;
          return (
            <button
              key={f.key}
              type="button"
              aria-pressed={filter === f.key}
              onClick={() => setFilter(f.key)}
              className={cn(
                "rounded-full border border-border px-3 py-1 text-sm",
                filter === f.key ? "bg-surface font-medium" : "text-muted hover:bg-surface",
              )}
            >
              {f.label} <span className="text-muted">{count}</span>
            </button>
          );
        })}
      </div>

      <Card>
        {runs.isPending ? (
          <p className="text-sm text-muted" role="status">
            Loading…
          </p>
        ) : runs.isError ? (
          <p className="text-sm text-danger" role="alert">
            {runs.error.message}
          </p>
        ) : all.length === 0 ? (
          <p className="text-sm text-muted">
            No questionnaires yet.{" "}
            <Link href="/app/runs/new" className="text-accent underline">
              Answer your first one
            </Link>
            .
          </p>
        ) : shown.length === 0 ? (
          <p className="text-sm text-muted">Nothing here.</p>
        ) : (
          <table className="w-full text-sm">
            <caption className="sr-only">Questionnaire runs</caption>
            <thead className="text-left text-muted">
              <tr>
                <th scope="col" className="pb-2 font-medium">
                  Questionnaire
                </th>
                <th scope="col" className="pb-2 font-medium">
                  Status
                </th>
                <th scope="col" className="pb-2 font-medium">
                  Answered
                </th>
                <th scope="col" className="pb-2 text-right font-medium">
                  Started
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {shown.map((run) => (
                <tr key={run.id} data-testid="run-row" data-run-id={run.id}>
                  <td className="max-w-0 py-2 pr-4">
                    <Link
                      href={`/app/runs/${run.id}`}
                      className="block truncate font-medium text-accent hover:underline"
                    >
                      {run.title}
                    </Link>
                  </td>
                  <td className="py-2 pr-4">
                    <RunStatusBadge status={run.status} />
                  </td>
                  <td className="py-2 pr-4 tabular-nums text-muted">
                    {run.items_done} of {run.item_count}
                  </td>
                  <td className="py-2 text-right text-muted">
                    <time dateTime={run.created_at}>{formatAge(run.created_at)}</time>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}
