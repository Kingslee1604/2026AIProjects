"use client";

import type { UseQueryResult } from "@tanstack/react-query";

import { DocumentStatus } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ConfirmButton } from "@/components/ui/confirm-button";
import { formatAge, formatBytes } from "@/lib/format";
import { type KnowledgeDocument, useDeleteDocument, useReindexDocument } from "@/lib/knowledge";

export function DocumentTable({
  kbId,
  documents,
}: {
  kbId: string;
  documents: UseQueryResult<KnowledgeDocument[] | null>;
}) {
  const remove = useDeleteDocument(kbId);
  const reindex = useReindexDocument(kbId);

  if (documents.isPending) {
    return (
      <p className="mt-4 text-sm text-muted" role="status">
        Loading documents…
      </p>
    );
  }
  if (documents.isError) {
    return (
      <p className="mt-4 text-sm text-danger" role="alert">
        {documents.error.message}
      </p>
    );
  }
  const rows = documents.data ?? [];
  if (rows.length === 0) {
    return <p className="mt-4 text-sm text-muted">No documents yet.</p>;
  }

  return (
    <div className="mt-4 overflow-x-auto">
      {(remove.isError || reindex.isError) && (
        <p className="mb-2 text-sm text-danger" role="alert">
          {(remove.error ?? reindex.error)?.message}
        </p>
      )}
      <table className="w-full text-sm">
        <caption className="sr-only">Documents in this knowledge base</caption>
        <thead className="text-left text-muted">
          <tr>
            <th scope="col" className="py-2 pr-3 font-medium">
              File
            </th>
            <th scope="col" className="py-2 pr-3 font-medium">
              Status
            </th>
            <th scope="col" className="py-2 pr-3 font-medium">
              Size
            </th>
            <th scope="col" className="py-2 pr-3 font-medium">
              Passages
            </th>
            <th scope="col" className="py-2 pr-3 font-medium">
              Updated
            </th>
            <th scope="col" className="py-2 font-medium">
              <span className="sr-only">Actions</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((d) => (
            <tr key={d.id} className="border-t border-border align-top" data-testid="document-row">
              <td className="py-2 pr-3">
                <span className="font-medium">{d.filename}</span>
                {d.error && <p className="mt-1 text-xs text-danger">{d.error}</p>}
              </td>
              <td className="py-2 pr-3">
                <DocumentStatus status={d.status} />
              </td>
              <td className="py-2 pr-3 whitespace-nowrap text-muted">
                {formatBytes(d.size_bytes)}
              </td>
              <td className="py-2 pr-3 text-muted">{d.chunk_count ?? "—"}</td>
              <td className="py-2 pr-3 whitespace-nowrap text-muted">
                <time dateTime={d.updated_at}>{formatAge(d.updated_at)}</time>
              </td>
              <td className="py-2">
                <div className="flex flex-wrap justify-end gap-2">
                  {(d.status === "failed" || d.status === "ready") && (
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => reindex.mutate(d.id)}
                      aria-label={`Re-index ${d.filename}`}
                    >
                      Re-index
                    </Button>
                  )}
                  <ConfirmButton
                    label="Delete"
                    confirmLabel="Delete"
                    question={`Delete ${d.filename}?`}
                    pending={remove.isPending && remove.variables === d.id}
                    onConfirm={() => remove.mutate(d.id)}
                  />
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
