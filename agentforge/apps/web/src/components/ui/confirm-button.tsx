"use client";

import { useEffect, useRef, useState } from "react";

import { Button, type ButtonProps } from "./button";

/** A destructive action that asks once, inline, before running (keyboard and screen-reader
 * friendly, unlike window.confirm). Escape or Cancel backs out. */
export function ConfirmButton({
  label,
  confirmLabel,
  question,
  onConfirm,
  pending = false,
  pendingLabel = "Deleting…",
  backLabel = "Cancel",
  size = "sm",
}: {
  label: string;
  confirmLabel: string;
  question: string;
  onConfirm: () => void;
  pending?: boolean;
  pendingLabel?: string;
  /** The button that backs out (rename it when the action itself is a "cancel"). */
  backLabel?: string;
  size?: ButtonProps["size"];
}) {
  const [asking, setAsking] = useState(false);
  const cancelRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (asking) cancelRef.current?.focus();
  }, [asking]);

  if (!asking) {
    return (
      <Button variant="secondary" size={size} onClick={() => setAsking(true)} disabled={pending}>
        {pending ? pendingLabel : label}
      </Button>
    );
  }
  return (
    <span
      role="group"
      aria-label={question}
      className="inline-flex flex-wrap items-center gap-2"
      onKeyDown={(e) => {
        if (e.key === "Escape") setAsking(false);
      }}
    >
      <span className="text-sm">{question}</span>
      <Button
        size={size}
        className="bg-danger text-accent-fg"
        onClick={() => {
          setAsking(false);
          onConfirm();
        }}
      >
        {confirmLabel}
      </Button>
      <Button ref={cancelRef} variant="secondary" size={size} onClick={() => setAsking(false)}>
        {backLabel}
      </Button>
    </span>
  );
}
