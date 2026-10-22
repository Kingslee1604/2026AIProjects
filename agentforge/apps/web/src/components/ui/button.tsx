import { cva, type VariantProps } from "class-variance-authority";
import type { ComponentProps } from "react";

import { cn } from "@/lib/utils";

/** Also for links that look like buttons. */
export const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 rounded-lg text-sm font-medium transition disabled:pointer-events-none disabled:opacity-50",
  {
    variants: {
      variant: {
        primary: "bg-brand text-accent-fg glow hover:brightness-110",
        secondary: "border border-border bg-surface-2/60 hover:border-accent/60 hover:bg-surface-2",
        ghost: "text-muted hover:bg-surface-2 hover:text-fg",
      },
      size: { md: "h-9 px-4", sm: "h-8 px-3", lg: "h-11 px-6 text-base" },
    },
    defaultVariants: { variant: "primary", size: "md" },
  },
);

export interface ButtonProps
  extends ComponentProps<"button">, VariantProps<typeof buttonVariants> {}

export function Button({ className, variant, size, type = "button", ...props }: ButtonProps) {
  return (
    <button type={type} className={cn(buttonVariants({ variant, size }), className)} {...props} />
  );
}
