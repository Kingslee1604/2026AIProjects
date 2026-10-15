"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, csrfHeaders, problemTitle } from "./api/client";
import type { components } from "./api/schema";

export type Run = components["schemas"]["RunOut"];
export type RunStatus = Run["status"];
export type RunItem = components["schemas"]["RunItemOut"];
export type Artifact = components["schemas"]["ArtifactOut"];
export type Questionnaire = components["schemas"]["QuestionnaireOut"];
export type QuestionnaireItem = components["schemas"]["QuestionnaireItemOut"];

export const QUESTIONNAIRE_FILES = ".xlsx,.csv";
export const MAX_QUESTIONNAIRE_MB = 25;

/** Statuses after which a run no longer changes on its own. */
export const FINAL_RUN = new Set<RunStatus>(["succeeded", "failed", "cancelled", "timed_out"]);

export const RUN_STATUS: Record<
  RunStatus,
  { label: string; tone: "neutral" | "progress" | "ok" | "warn" | "danger" }
> = {
  queued: { label: "Queued", tone: "neutral" },
  running: { label: "Answering…", tone: "progress" },
  waiting_review: { label: "Needs review", tone: "warn" },
  succeeded: { label: "Done", tone: "ok" },
  failed: { label: "Failed", tone: "danger" },
  cancelled: { label: "Cancelled", tone: "neutral" },
  timed_out: { label: "Timed out", tone: "danger" },
};

/** List filters: each is a set of statuses (the API filters by one, so the list filters here). */
export const RUN_FILTERS: { key: string; label: string; statuses: RunStatus[] | null }[] = [
  { key: "all", label: "All", statuses: null },
  { key: "active", label: "In progress", statuses: ["queued", "running"] },
  { key: "review", label: "Needs review", statuses: ["waiting_review"] },
  { key: "done", label: "Done", statuses: ["succeeded"] },
  { key: "problems", label: "Failed or cancelled", statuses: ["failed", "cancelled", "timed_out"] },
];

export function useRuns() {
  return useQuery({
    queryKey: ["runs"],
    queryFn: async (): Promise<Run[]> => {
      const { data, error } = await api.GET("/v1/runs", { params: { query: { limit: 100 } } });
      if (error) throw new Error(problemTitle(error));
      return data;
    },
    // Live status in the list: poll while any run is still moving.
    refetchInterval: (query) =>
      query.state.data?.some((r) => !FINAL_RUN.has(r.status) && r.status !== "waiting_review")
        ? 2000
        : false,
  });
}

export function useRun(runId: string) {
  return useQuery({
    queryKey: ["runs", runId],
    queryFn: async (): Promise<Run | null> => {
      const { data, error, response } = await api.GET("/v1/runs/{run_id}", {
        params: { path: { run_id: runId } },
      });
      if (response.status === 404) return null;
      if (error) throw new Error(problemTitle(error));
      return data;
    },
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status && !FINAL_RUN.has(status) ? 2000 : false;
    },
  });
}

export function useArtifacts(runId: string, enabled: boolean) {
  return useQuery({
    queryKey: ["runs", runId, "artifacts"],
    enabled,
    queryFn: async (): Promise<Artifact[]> => {
      const { data, error } = await api.GET("/v1/runs/{run_id}/artifacts", {
        params: { path: { run_id: runId } },
      });
      if (error) throw new Error(problemTitle(error));
      return data;
    },
  });
}

export function downloadUrl(artifact: Artifact): string {
  return `/api/v1/runs/${encodeURIComponent(artifact.run_id)}/artifacts/${encodeURIComponent(
    artifact.id,
  )}/download`;
}

/** Uploads the questionnaire as the raw request body; the response shows what was detected. */
export async function uploadQuestionnaire(file: File): Promise<Questionnaire> {
  if (file.size > MAX_QUESTIONNAIRE_MB * 1024 * 1024) {
    throw new Error(`${file.name} is larger than ${MAX_QUESTIONNAIRE_MB} MB.`);
  }
  const response = await fetch("/api/v1/questionnaires", {
    method: "POST",
    body: file,
    credentials: "same-origin",
    headers: {
      ...csrfHeaders(),
      "Content-Type": "application/octet-stream",
      "X-Filename": encodeURIComponent(file.name),
    },
  });
  const body: unknown = await response.json().catch(() => null);
  if (!response.ok) throw new Error(problemTitle(body, `Upload failed (${response.status})`));
  return body as Questionnaire;
}

export function useUploadQuestionnaire() {
  return useMutation({ mutationFn: uploadQuestionnaire });
}

export interface ColumnChoice {
  question: string;
  id: string | null;
  section: string | null;
  answer: string | null;
}

export function useSetColumns(questionnaireId: string) {
  return useMutation({
    mutationFn: async (body: ColumnChoice): Promise<Questionnaire> => {
      const { data, error } = await api.PUT("/v1/questionnaires/{questionnaire_id}/columns", {
        params: { path: { questionnaire_id: questionnaireId } },
        body,
      });
      if (error) throw new Error(problemTitle(error));
      return data;
    },
  });
}

export function useStartRun() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: {
      questionnaire_id: string;
      knowledge_base_ids: string[];
      title: string | null;
    }): Promise<Run> => {
      const { data, error } = await api.POST("/v1/runs", { body });
      if (error) throw new Error(problemTitle(error));
      return data;
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["runs"] });
    },
  });
}

/** The detected (or chosen) column letters of an uploaded questionnaire. */
export function columnLetters(questionnaire: Questionnaire): ColumnChoice {
  const letters = (questionnaire.columns as { letters?: Record<string, string | null> }).letters;
  return {
    question: letters?.question ?? "",
    id: letters?.id ?? null,
    section: letters?.section ?? null,
    answer: letters?.answer ?? null,
  };
}
