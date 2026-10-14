"use client";

import { Suspense } from "react";

import { AskView } from "./ask-view";

// useSearchParams (the answer id lives in the URL) needs a Suspense boundary.
export default function AskPage() {
  return (
    <Suspense
      fallback={
        <p className="text-sm text-muted" role="status">
          Loading…
        </p>
      }
    >
      <AskView />
    </Suspense>
  );
}
