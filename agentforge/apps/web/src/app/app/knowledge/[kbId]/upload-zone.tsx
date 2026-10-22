"use client";

import { type DragEvent, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { ACCEPTED_FILES, MAX_UPLOAD_MB, useUpload } from "@/lib/knowledge";
import { cn } from "@/lib/utils";

interface UploadState {
  name: string;
  state: "uploading" | "done" | "error";
  message?: string;
}

export function UploadZone({ kbId }: { kbId: string }) {
  const upload = useUpload(kbId);
  const input = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [uploads, setUploads] = useState<UploadState[]>([]);

  async function send(files: File[]) {
    setUploads(files.map((f) => ({ name: f.name, state: "uploading" })));
    // One at a time: uploads are streamed to disk, and order keeps the status list readable.
    for (const [index, file] of files.entries()) {
      const result: UploadState = await upload
        .mutateAsync(file)
        .then(() => ({ name: file.name, state: "done" as const }))
        .catch((error: unknown) => ({
          name: file.name,
          state: "error" as const,
          message: error instanceof Error ? error.message : "Upload failed",
        }));
      setUploads((current) => current.map((u, i) => (i === index ? result : u)));
    }
  }

  function onDrop(event: DragEvent) {
    event.preventDefault();
    setDragging(false);
    const files = Array.from(event.dataTransfer.files);
    if (files.length) void send(files);
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
        <p className="text-sm">Drag files here, or</p>
        <Button variant="secondary" size="sm" onClick={() => input.current?.click()}>
          Choose files
        </Button>
        <p className="text-xs text-muted">
          PDF, Word (.docx), Markdown, or text, up to {MAX_UPLOAD_MB} MB each.
        </p>
        <input
          ref={input}
          type="file"
          multiple
          accept={ACCEPTED_FILES}
          className="sr-only"
          // The "Choose files" button opens it; it is not a second, invisible Tab stop.
          tabIndex={-1}
          aria-hidden="true"
          aria-label="Upload documents"
          data-testid="file-input"
          onChange={(e) => {
            const files = Array.from(e.target.files ?? []);
            e.target.value = "";
            if (files.length) void send(files);
          }}
        />
      </div>
      {uploads.length > 0 && (
        <ul className="space-y-1 text-sm" aria-live="polite">
          {uploads.map((u) => (
            <li key={u.name} className={u.state === "error" ? "text-danger" : "text-muted"}>
              {u.name}:{" "}
              {u.state === "uploading"
                ? "uploading…"
                : u.state === "done"
                  ? "uploaded, indexing in the background"
                  : u.message}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
