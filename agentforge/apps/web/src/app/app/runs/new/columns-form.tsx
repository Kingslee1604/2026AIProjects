"use client";

import { type FormEvent, useState } from "react";

import { Button } from "@/components/ui/button";
import { columnLetter } from "@/lib/format";
import { type ColumnChoice, columnLetters, type Questionnaire, useSetColumns } from "@/lib/runs";

const FIELDS: { key: keyof ColumnChoice; label: string; none: string | null }[] = [
  { key: "question", label: "Questions", none: null },
  { key: "id", label: "Question IDs", none: "None" },
  { key: "section", label: "Sections", none: "None" },
  { key: "answer", label: "Write answers into", none: "A new column after the last one" },
];

/** The detected columns, and a way to choose them when detection got it wrong. Re-mount (key)
 * when the questionnaire's columns change, so the form starts from what was saved. */
export function ColumnsForm({
  questionnaire,
  onChanged,
}: {
  questionnaire: Questionnaire;
  onChanged: (q: Questionnaire) => void;
}) {
  const saved = columnLetters(questionnaire);
  const [choice, setChoice] = useState<ColumnChoice>(saved);
  const setColumns = useSetColumns(questionnaire.id);
  const changed = FIELDS.some((f) => choice[f.key] !== saved[f.key]);
  const options = questionnaire.headers.map((header, index) => ({
    letter: columnLetter(index),
    label: `${columnLetter(index)} · ${header.trim() || "(no header)"}`,
  }));

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    setColumns.mutate(choice, { onSuccess: onChanged });
  }

  return (
    <form className="mt-4 space-y-3" onSubmit={onSubmit} aria-label="Columns">
      <div className="grid gap-3 sm:grid-cols-2">
        {FIELDS.map((field) => (
          <div key={field.key} className="grid gap-1">
            <label htmlFor={`column-${field.key}`} className="text-sm font-medium">
              {field.label}
            </label>
            <select
              id={`column-${field.key}`}
              value={choice[field.key] ?? ""}
              onChange={(e) => setChoice({ ...choice, [field.key]: e.target.value || null })}
              className="h-9 rounded-lg border border-border bg-surface-2/60 px-2 text-sm"
            >
              {field.none !== null && <option value="">{field.none}</option>}
              {options.map((o) => (
                <option key={o.letter} value={o.letter}>
                  {o.label}
                </option>
              ))}
            </select>
          </div>
        ))}
      </div>
      {setColumns.isError && (
        <p className="text-sm text-danger" role="alert">
          {setColumns.error.message}
        </p>
      )}
      {changed && (
        <div className="flex gap-2">
          <Button type="submit" size="sm" disabled={setColumns.isPending || !choice.question}>
            {setColumns.isPending ? "Reading again…" : "Use these columns"}
          </Button>
          <Button variant="ghost" size="sm" onClick={() => setChoice(saved)}>
            Undo
          </Button>
        </div>
      )}
    </form>
  );
}
