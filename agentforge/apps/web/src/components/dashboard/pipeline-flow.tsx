import type { LucideIcon } from "lucide-react";

import { cn } from "@/lib/utils";

export interface Stage {
  key: string;
  label: string;
  count: number;
  icon: LucideIcon;
  /** Work happening now: the node glows and pulses (no motion if the user prefers less). */
  live?: boolean;
  tone?: "brand" | "ok" | "warn" | "danger";
}

const TONE = {
  brand: "text-accent",
  ok: "text-ok",
  warn: "text-warn",
  danger: "text-danger",
};

/** Stages joined by glowing connectors. The list itself is the accessible content; the
 * connectors are decoration. */
export function PipelineFlow({ stages, label }: { stages: Stage[]; label: string }) {
  return (
    <ol aria-label={label} className="grid grid-cols-2 gap-5 sm:flex sm:gap-0">
      {stages.map((stage, index) => {
        const Icon = stage.icon;
        return (
          <li key={stage.key} className="relative flex flex-col items-center text-center sm:flex-1">
            {index > 0 && (
              <span
                aria-hidden
                className={cn(
                  // From the previous node's centre to this one's; the nodes sit on top.
                  "bg-brand absolute top-7 right-1/2 hidden h-0.5 w-full rounded-full opacity-40 sm:block",
                  stage.count > 0 && "opacity-100 shadow-[0_0_12px_var(--color-glow)]",
                )}
              />
            )}
            <span
              className={cn(
                "panel relative z-10 flex size-14 items-center justify-center rounded-2xl",
                stage.live && stage.count > 0 && "glow motion-safe:animate-pulse",
              )}
            >
              <Icon className={cn("size-6", TONE[stage.tone ?? "brand"])} aria-hidden />
            </span>
            <span className="mt-3 text-2xl font-semibold tabular-nums">{stage.count}</span>
            <span className="text-xs text-muted">{stage.label}</span>
          </li>
        );
      })}
    </ol>
  );
}
