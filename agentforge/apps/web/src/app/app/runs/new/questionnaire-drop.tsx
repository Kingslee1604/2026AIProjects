"use client";

import { type DragEvent, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  MAX_QUESTIONNAIRE_MB,
  type Questionnaire,
  QUESTIONNAIRE_FILES,
  useUploadQuestionnaire,
} from "@/lib/runs";
import { cn } from "@/lib/utils";

export function QuestionnaireDrop({ onUploaded }: { onUploaded: (q: Questionnaire) => void }) {
  const upload = useUploadQuestionnaire();
  const input = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);

  function send(file: File | undefined) {
    if (file) upload.mutate(file, { onSuccess: onUploaded });
  }

  function onDrop(event: DragEvent) {
    event.preventDefault();
    setDragging(false);
    send(event.dataTransfer.files[0]);
  }

  return (
    <div className="mt-3 space-y-2">
      <div
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        className={cn(
          "flex flex-col items-center gap-2 rounded-lg border-2 border-dashed border-border p-6 text-center",
          dragging && "border-accent bg-bg",
        )}
      >
        <p className="text-sm">Drag the questionnaire here, or</p>
        <Button
          variant="secondary"
          size="sm"
          disabled={upload.isPending}
          onClick={() => input.current?.click()}
        >
          {upload.isPending ? "Reading…" : "Choose file"}
        </Button>
        <p className="text-xs text-muted">
          Excel (.xlsx) or CSV, up to {MAX_QUESTIONNAIRE_MB} MB. Macro-enabled workbooks are not
          accepted.
        </p>
        <input
          ref={input}
          type="file"
          accept={QUESTIONNAIRE_FILES}
          className="sr-only"
          aria-label="Upload questionnaire"
          data-testid="questionnaire-input"
          onChange={(e) => {
            const file = e.target.files?.[0];
            e.target.value = "";
            send(file);
          }}
        />
      </div>
      {upload.isError && (
        <p className="text-sm text-danger" role="alert">
          {upload.error.message}
        </p>
      )}
    </div>
  );
}
