import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

/** A number that matters, with what it means. Only real values: no placeholders, no trends
 * we do not measure. `hint` says where the number comes from or what to do about it. */
export function StatTile({
  icon: Icon,
  label,
  value,
  hint,
  tone = "brand",
  className,
}: {
  icon: LucideIcon;
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  tone?: "brand" | "ok" | "warn" | "danger";
  className?: string;
}) {
  const iconTone = {
    brand: "bg-brand text-accent-fg",
    ok: "bg-ok/15 text-ok",
    warn: "bg-warn/15 text-warn",
    danger: "bg-danger/15 text-danger",
  }[tone];
  return (
    <div className={cn("panel flex items-start gap-4 p-4", className)}>
      <span
        className={cn("flex size-11 shrink-0 items-center justify-center rounded-xl", iconTone)}
      >
        <Icon className="size-5" aria-hidden />
      </span>
      <div className="min-w-0">
        <p className="text-sm text-muted">{label}</p>
        <p className="mt-0.5 text-2xl font-semibold tracking-tight tabular-nums">{value}</p>
        {hint && <p className="mt-1 text-xs text-muted">{hint}</p>}
      </div>
    </div>
  );
}
