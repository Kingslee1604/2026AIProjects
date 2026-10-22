"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { type FormEvent, useState } from "react";

import { ColumnsForm } from "./columns-form";
import { QuestionnaireDrop } from "./questionnaire-drop";
import { KnowledgePicker, useKnowledgeSelection } from "@/components/knowledge-picker";
import { Button } from "@/components/ui/button";
import { Card, CardTitle } from "@/components/ui/card";
import { Input, Label } from "@/components/ui/input";
import { type Questionnaire, useStartRun } from "@/lib/runs";

const PREVIEW = 5;

export default function NewRunPage() {
  const router = useRouter();
  const [questionnaire, setQuestionnaire] = useState<Questionnaire | null>(null);
  const [title, setTitle] = useState("");
  const knowledge = useKnowledgeSelection();
  const start = useStartRun();

  async function onStart(event: FormEvent) {
    event.preventDefault();
    if (!questionnaire) return;
    const run = await start
      .mutateAsync({
        questionnaire_id: questionnaire.id,
        knowledge_base_ids: [...knowledge.selected],
        title: title.trim() || null,
      })
      .catch(() => null);
    if (run) router.push("/app/runs");
  }

  return (
    <div className="space-y-6">
      <div>
        <Link href="/app/runs" className="text-sm text-accent hover:underline">
          ← Questionnaires
        </Link>
        <h1 className="mt-1 text-3xl font-semibold tracking-tight">New questionnaire</h1>
        <p className="mt-1 text-sm text-muted">
          The answers are written into a copy of your file, next to each question. Your file itself
          is never changed.
        </p>
      </div>

      <Card>
        <CardTitle>1. Upload the questionnaire</CardTitle>
        {questionnaire ? (
          <div className="mt-3 flex flex-wrap items-center justify-between gap-3 text-sm">
            <p>
              <span className="font-medium">{questionnaire.filename}</span>
              <span className="text-muted">
                {" "}
                · {questionnaire.item_count} question{questionnaire.item_count === 1 ? "" : "s"}
                {questionnaire.sheet ? ` in sheet “${questionnaire.sheet}”` : ""}
              </span>
            </p>
            <Button variant="secondary" size="sm" onClick={() => setQuestionnaire(null)}>
              Choose a different file
            </Button>
          </div>
        ) : (
          <QuestionnaireDrop onUploaded={setQuestionnaire} />
        )}
      </Card>

      {questionnaire && (
        <Card>
          <CardTitle>2. Check the questions</CardTitle>
          <p className="mt-1 text-sm text-muted">
            Found {questionnaire.item_count} question{questionnaire.item_count === 1 ? "" : "s"}{" "}
            below the header row (row {questionnaire.header_row}). If the columns are wrong, choose
            them yourself.
          </p>
          {questionnaire.warnings.length > 0 && (
            <ul className="mt-3 list-disc space-y-1 pl-5 text-sm text-warn" data-testid="warnings">
              {questionnaire.warnings.map((warning) => (
                <li key={warning}>{warning}</li>
              ))}
            </ul>
          )}
          <ColumnsForm
            key={`${questionnaire.id}:${JSON.stringify(questionnaire.columns)}`}
            questionnaire={questionnaire}
            onChanged={setQuestionnaire}
          />
          {questionnaire.items.length > 0 && (
            <table className="mt-4 w-full text-sm" data-testid="question-preview">
              <caption className="pb-2 text-left text-muted">
                First {Math.min(PREVIEW, questionnaire.items.length)} of {questionnaire.item_count}
              </caption>
              <thead className="text-left text-muted">
                <tr>
                  <th scope="col" className="pb-1 pr-3 font-medium">
                    Row
                  </th>
                  <th scope="col" className="pb-1 pr-3 font-medium">
                    ID
                  </th>
                  <th scope="col" className="pb-1 pr-3 font-medium">
                    Section
                  </th>
                  <th scope="col" className="pb-1 font-medium">
                    Question
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border align-top">
                {questionnaire.items.slice(0, PREVIEW).map((item) => (
                  <tr key={item.row}>
                    <td className="py-1.5 pr-3 tabular-nums text-muted">{item.row}</td>
                    <td className="py-1.5 pr-3 text-muted">{item.external_id ?? "—"}</td>
                    <td className="py-1.5 pr-3 text-muted">{item.section ?? "—"}</td>
                    <td className="py-1.5">{item.question}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
      )}

      {questionnaire && (
        <Card>
          <CardTitle>3. Start</CardTitle>
          <form className="mt-3 space-y-4" onSubmit={onStart}>
            <KnowledgePicker legend="Answer from" selection={knowledge} />
            <div className="grid gap-1 sm:max-w-md">
              <Label htmlFor="run-title">Name (optional)</Label>
              <Input
                id="run-title"
                maxLength={300}
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder={questionnaire.filename}
              />
            </div>
            {start.isError && (
              <p className="text-sm text-danger" role="alert">
                {start.error.message}
              </p>
            )}
            <Button
              type="submit"
              disabled={
                start.isPending || questionnaire.item_count === 0 || knowledge.selected.size === 0
              }
            >
              {start.isPending ? "Starting…" : "Start answering"}
            </Button>
          </form>
        </Card>
      )}
    </div>
  );
}
