import type { HTMLAttributes, ReactNode } from "react";

import { cn } from "@/lib/utils";

/** A glass panel. `glow` adds the soft brand glow, for the one thing that matters most. */
export function Card({
  className,
  glow = false,
  ...props
}: HTMLAttributes<HTMLDivElement> & { glow?: boolean }) {
  return <div className={cn("panel p-5", glow && "glow", className)} {...props} />;
}

export function CardTitle({ className, ...props }: HTMLAttributes<HTMLHeadingElement>) {
  return <h2 className={cn("text-base font-semibold tracking-tight", className)} {...props} />;
}

/** Title row with an optional action on the right (a "View all" link, a filter). */
export function CardHeader({
  title,
  action,
  className,
}: {
  title: ReactNode;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex items-center justify-between gap-3", className)}>
      <CardTitle>{title}</CardTitle>
      {action}
    </div>
  );
}
