"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";

import { DocumentTable } from "./document-table";
import { SearchTester } from "./search-tester";
import { UploadZone } from "./upload-zone";
import { Card, CardTitle } from "@/components/ui/card";
import { ConfirmButton } from "@/components/ui/confirm-button";
import { useDeleteKnowledgeBase, useDocuments, useKnowledgeBases } from "@/lib/knowledge";

export default function KnowledgeBasePage() {
  const { kbId } = useParams<{ kbId: string }>();
  const router = useRouter();
  const kbs = useKnowledgeBases();
  const documents = useDocuments(kbId);
  const deleteKb = useDeleteKnowledgeBase();
  const kb = kbs.data?.find((k) => k.id === kbId);

  if (documents.data === null || (kbs.isSuccess && !kb)) {
    return (
      <div className="space-y-3">
        <h1 className="text-3xl font-semibold tracking-tight">Knowledge base not found</h1>
        <Link href="/app/knowledge" className="text-sm text-accent hover:underline">
          Back to knowledge bases
        </Link>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <Link href="/app/knowledge" className="text-sm text-accent hover:underline">
            ← Knowledge
          </Link>
          <h1 className="mt-1 text-3xl font-semibold tracking-tight">{kb?.name ?? "…"}</h1>
          {kb?.description && <p className="mt-1 text-sm text-muted">{kb.description}</p>}
        </div>
        <ConfirmButton
          label="Delete knowledge base"
          confirmLabel="Delete permanently"
          question="Delete this knowledge base and all its documents?"
          pending={deleteKb.isPending}
          onConfirm={() =>
            deleteKb.mutate(kbId, { onSuccess: () => router.push("/app/knowledge") })
          }
        />
      </div>
      {deleteKb.isError && (
        <p className="text-sm text-danger" role="alert">
          {deleteKb.error.message}
        </p>
      )}

      <Card>
        <CardTitle>Documents</CardTitle>
        <UploadZone kbId={kbId} />
        <DocumentTable kbId={kbId} documents={documents} />
      </Card>

      <Card>
        <CardTitle>Search tester</CardTitle>
        <p className="mt-1 text-sm text-muted">
          See which passages a question retrieves, and why: rank in the vector (meaning) search,
          rank in the keyword search, and the fused score.
        </p>
        <SearchTester kbId={kbId} />
      </Card>
    </div>
  );
}
