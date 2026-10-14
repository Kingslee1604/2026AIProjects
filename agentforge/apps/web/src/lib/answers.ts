"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, problemTitle } from "./api/client";
import type { components } from "./api/schema";

export type Answer = components["schemas"]["AnswerOut"];
export type Claim = components["schemas"]["ClaimOut"];
export type Evidence = components["schemas"]["EvidenceOut"];
export type ModelCall = components["schemas"]["ModelCallOut"];

export const FINAL = new Set(["answered", "insufficient_evidence", "failed"]);

export function useAsk() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: { question: string; knowledge_base_ids: string[] }) => {
      const { data, error } = await api.POST("/v1/answers", { body });
      if (error) throw new Error(problemTitle(error));
      return data;
    },
    // Refresh the recent list in the background; returning the promise would make the ask
    // wait for that refetch (and its retries) before the answer page opens.
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["answers", "recent"] });
    },
  });
}

export function useAnswer(id: string | null) {
  const queryClient = useQueryClient();
  return useQuery({
    queryKey: ["answers", id],
    enabled: id !== null,
    queryFn: async (): Promise<Answer | null> => {
      const { data, error, response } = await api.GET("/v1/answers/{answer_id}", {
        params: { path: { answer_id: id ?? "" } },
      });
      if (response.status === 404) return null;
      if (error) throw new Error(problemTitle(error));
      if (FINAL.has(data.status)) {
        void queryClient.invalidateQueries({ queryKey: ["answers", "recent"] });
      }
      return data;
    },
    // Poll while the worker is answering (tens of seconds on a CPU-only machine).
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status && !FINAL.has(status) ? 1500 : false;
    },
  });
}

export function useAnswerCalls(id: string, enabled: boolean) {
  return useQuery({
    queryKey: ["answers", id, "calls"],
    enabled,
    queryFn: async (): Promise<ModelCall[]> => {
      const { data, error } = await api.GET("/v1/answers/{answer_id}/calls", {
        params: { path: { answer_id: id } },
      });
      if (error) throw new Error(problemTitle(error));
      return data;
    },
  });
}

export function useRecentAnswers() {
  return useQuery({
    queryKey: ["answers", "recent"],
    queryFn: async (): Promise<Answer[]> => {
      const { data, error } = await api.GET("/v1/answers", { params: { query: { limit: 10 } } });
      if (error) throw new Error(problemTitle(error));
      return data;
    },
  });
}

export const CLAIM_STATUS: Record<
  Claim["status"],
  { label: string; tone: "ok" | "warn" | "danger"; help: string }
> = {
  verified: {
    label: "Verified",
    tone: "ok",
    help: "An exact quote from the cited passage says this.",
  },
  rescued: {
    label: "Checked by AI",
    tone: "warn",
    help: "No exact quote matched; a second AI check found the passage supports it.",
  },
  partial: {
    label: "Partly supported",
    tone: "warn",
    help: "The passage supports only part of this sentence. Review before using it.",
  },
  dropped: {
    label: "Removed",
    tone: "danger",
    help: "Failed verification, so it is not part of the answer.",
  },
};
