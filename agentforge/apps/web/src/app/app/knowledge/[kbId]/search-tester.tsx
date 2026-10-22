"use client";

import { type FormEvent, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input, Label } from "@/components/ui/input";
import { formatPages } from "@/lib/format";
import { useSearch } from "@/lib/knowledge";

export function SearchTester({ kbId }: { kbId: string }) {
  const search = useSearch();
  const [query, setQuery] = useState("");

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    search.mutate({ query, knowledge_base_ids: [kbId], k: 8 });
  }

  return (
    <div className="mt-3 space-y-4">
      <form className="flex flex-wrap items-end gap-2" onSubmit={onSubmit} role="search">
        <div className="grid min-w-64 flex-1 gap-1">
          <Label htmlFor="search-query">Question</Label>
          <Input
            id="search-query"
            value={query}
            maxLength={2000}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="How long are access logs retained?"
          />
        </div>
        <Button type="submit" disabled={!query.trim() || search.isPending}>
          {search.isPending ? "Searching…" : "Search"}
        </Button>
      </form>

      {search.isError && (
        <p className="text-sm text-danger" role="alert">
          {search.error.message}
        </p>
      )}
      {search.isSuccess && search.data.length === 0 && (
        <p className="text-sm text-muted" role="status">
          No passages found. Are the documents finished indexing?
        </p>
      )}
      {search.isSuccess && search.data.length > 0 && (
        <ol className="space-y-3" aria-label="Search results">
          {search.data.map((hit, index) => (
            <li
              key={hit.chunk_id}
              className="rounded-xl border border-border/70 bg-surface-2/40 p-3"
              data-testid="search-hit"
            >
              <div className="flex flex-wrap items-center gap-2 text-sm">
                <span className="font-medium">
                  {index + 1}. {hit.filename}
                </span>
                <span className="text-muted">{formatPages(hit.page_start, hit.page_end)}</span>
                {hit.section_path.length > 0 && (
                  <span className="text-muted">· {hit.section_path.join(" / ")}</span>
                )}
              </div>
              <div className="mt-2 flex flex-wrap gap-2">
                <Badge>fused {hit.score.toFixed(4)}</Badge>
                <Badge tone={hit.vector_rank ? "progress" : "neutral"}>
                  vector {hit.vector_rank ? `#${hit.vector_rank}` : "—"}
                  {hit.vector_distance !== null && ` (distance ${hit.vector_distance.toFixed(3)})`}
                </Badge>
                <Badge tone={hit.text_rank ? "progress" : "neutral"}>
                  keyword {hit.text_rank ? `#${hit.text_rank}` : "—"}
                </Badge>
              </div>
              <p className="mt-2 line-clamp-4 text-sm whitespace-pre-line text-muted">
                {hit.content}
              </p>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
