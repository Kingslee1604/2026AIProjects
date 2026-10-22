"use client";

import Link from "next/link";

import { Card } from "@/components/ui/card";
import { formatAge } from "@/lib/format";
import { useReviewQueue } from "@/lib/reviews";

export default function ReviewsPage() {
  const queue = useReviewQueue();
  const entries = queue.data ?? [];
  const toDecide = entries.reduce((sum, e) => sum + e.undecided, 0);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-semibold tracking-tight">Reviews</h1>
        <p className="mt-1 text-sm text-muted">
          Every AI answer is checked by a person before it goes into the customer&apos;s file.
          {entries.length > 0 &&
            ` ${entries.length} questionnaire${entries.length === 1 ? "" : "s"} waiting, ${toDecide} answer${toDecide === 1 ? "" : "s"} to decide.`}
        </p>
      </div>

      <Card>
        {queue.isPending ? (
          <p className="text-sm text-muted" role="status">
            Loading…
          </p>
        ) : queue.isError ? (
          <p className="text-sm text-danger" role="alert">
            {queue.error.message}
          </p>
        ) : entries.length === 0 ? (
          <p className="text-sm text-muted">
            Nothing to review. Questionnaires appear here once every question has an answer.
          </p>
        ) : (
          <table className="w-full text-sm">
            <caption className="sr-only">Questionnaires waiting for review</caption>
            <thead className="text-left text-muted">
              <tr>
                <th scope="col" className="pb-2 font-medium">
                  Questionnaire
                </th>
                <th scope="col" className="pb-2 font-medium">
                  To decide
                </th>
                <th scope="col" className="pb-2 font-medium">
                  Verified, ready to approve
                </th>
                <th scope="col" className="pb-2 text-right font-medium">
                  Waiting since
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {entries.map((e) => (
                <tr key={e.run_id} data-testid="review-row">
                  <td className="max-w-0 py-2 pr-4">
                    <Link
                      href={`/app/reviews/${e.run_id}`}
                      className="block truncate font-medium text-accent hover:underline"
                    >
                      {e.title}
                    </Link>
                  </td>
                  <td className="py-2 pr-4 tabular-nums">
                    {e.undecided} of {e.total}
                  </td>
                  <td className="py-2 pr-4 tabular-nums text-muted">{e.candidates}</td>
                  <td className="py-2 text-right text-muted">
                    <time dateTime={e.created_at}>{formatAge(e.created_at)}</time>
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
