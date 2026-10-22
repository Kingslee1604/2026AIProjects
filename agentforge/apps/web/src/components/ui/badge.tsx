import type { HTMLAttributes } from "react";

import { cn } from "@/lib/utils";

type Tone = "neutral" | "progress" | "ok" | "warn" | "danger";

// Tinted fill plus coloured text; the fill is faint so the text keeps its contrast.
const TONES: Record<Tone, string> = {
  neutral: "border-border bg-surface-2/60 text-muted",
  progress: "border-accent/40 bg-accent/10 text-accent",
  ok: "border-ok/40 bg-ok/10 text-ok",
  warn: "border-warn/50 bg-warn/10 text-warn",
  danger: "border-danger/40 bg-danger/10 text-danger",
};

export function Badge({
  tone = "neutral",
  className,
  ...props
}: HTMLAttributes<HTMLSpanElement> & { tone?: Tone }) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium",
        TONES[tone],
        className,
      )}
      {...props}
    />
  );
}

const DOCUMENT_STATUS: Record<string, { label: string; tone: Tone }> = {
  queued: { label: "Queued", tone: "neutral" },
  processing: { label: "Indexing…", tone: "progress" },
  ready: { label: "Ready", tone: "ok" },
  failed: { label: "Failed", tone: "danger" },
};

export function DocumentStatus({ status }: { status: string }) {
  const known = DOCUMENT_STATUS[status] ?? { label: status, tone: "neutral" as const };
  return <Badge tone={known.tone}>{known.label}</Badge>;
}
