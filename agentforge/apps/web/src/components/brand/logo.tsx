import { useId } from "react";

import { cn } from "@/lib/utils";

/** The AgentForge mark: a hexagonal anvil-like shape with a spark, in the brand gradient. */
export function LogoMark({ className }: { className?: string }) {
  const id = useId();
  return (
    <svg viewBox="0 0 32 32" aria-hidden="true" className={cn("size-8 shrink-0", className)}>
      <defs>
        <linearGradient id={`${id}-g`} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="oklch(0.62 0.2 272)" />
          <stop offset="1" stopColor="oklch(0.62 0.24 305)" />
        </linearGradient>
      </defs>
      {/* Hexagon with rounded joins */}
      <path
        d="M16 2.5 27.5 9v14L16 29.5 4.5 23V9z"
        fill={`url(#${id}-g)`}
        stroke="oklch(1 0 0 / 0.25)"
        strokeLinejoin="round"
      />
      {/* Anvil: the forge */}
      <path
        d="M9 13.5h11.5c0 2.3-1.6 3.6-3.5 4v2.2h2.2v2.3H11.6v-2.3h2.2v-2.2c-2.3-.4-4.8-1.8-4.8-4z"
        fill="oklch(0.99 0 0)"
      />
      {/* Spark: the agent */}
      <path d="M23 8.2l.7 1.8 1.8.7-1.8.7-.7 1.8-.7-1.8-1.8-.7 1.8-.7z" fill="oklch(0.99 0 0)" />
    </svg>
  );
}

/** Mark plus the wordmark: "Agent" in the text colour, "Forge" in the gradient. */
export function Logo({ className }: { className?: string }) {
  return (
    <span className={cn("inline-flex items-center gap-2.5", className)}>
      <LogoMark />
      <span className="text-lg font-semibold tracking-tight">
        Agent<span className="text-brand">Forge</span>
      </span>
    </span>
  );
}
