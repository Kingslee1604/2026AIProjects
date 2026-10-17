"use client";

import Link from "next/link";
import { useState } from "react";

import { type KnowledgeBase, useKnowledgeBases } from "@/lib/knowledge";

/** Which knowledge bases to use: every one with ready documents to begin with. */
export function useKnowledgeSelection() {
  const kbs = useKnowledgeBases();
  const [picked, setPicked] = useState<Set<string> | null>(null);
  const usable = (kbs.data ?? []).filter((kb) => kb.ready_count > 0);
  // Start with every knowledge base that has ready documents, chosen once when the list first
  // loads: knowledge bases created later are not added to the selection behind the user's back.
  // (Setting state during render is React's pattern for deriving initial state from data.)
  // Wait for the fresh list (not a cached one from before the latest uploads finished).
  if (picked === null && kbs.isSuccess && !kbs.isFetching) {
    setPicked(new Set(usable.map((kb) => kb.id)));
  }
  // Only knowledge bases that still exist and are ready.
  const selected = new Set(usable.map((kb) => kb.id).filter((id) => picked?.has(id)));

  function toggle(id: string) {
    const next = new Set(selected);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setPicked(next);
  }

  return { loading: kbs.isPending, usable, selected, toggle };
}

export function KnowledgePicker({
  legend,
  selection,
}: {
  legend: string;
  selection: {
    loading: boolean;
    usable: KnowledgeBase[];
    selected: Set<string>;
    toggle: (id: string) => void;
  };
}) {
  const { loading, usable, selected, toggle } = selection;
  return (
    <fieldset>
      <legend className="text-sm font-medium">{legend}</legend>
      {loading ? (
        <p className="mt-2 text-sm text-muted">Loading knowledge bases…</p>
      ) : usable.length === 0 ? (
        <p className="mt-2 text-sm text-muted">
          No knowledge base has ready documents yet.{" "}
          <Link href="/app/knowledge" className="text-accent hover:underline">
            Upload documents first
          </Link>
          .
        </p>
      ) : (
        <div className="mt-2 flex flex-wrap gap-x-5 gap-y-2">
          {usable.map((kb) => (
            <label key={kb.id} className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                className="size-4 accent-accent"
                checked={selected.has(kb.id)}
                onChange={() => toggle(kb.id)}
              />
              {kb.name}
              <span className="text-muted">({kb.ready_count})</span>
            </label>
          ))}
        </div>
      )}
    </fieldset>
  );
}
