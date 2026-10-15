"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, problemTitle } from "./api/client";
import type { components } from "./api/schema";

export type Review = components["schemas"]["ReviewOut"];
export type ReviewItem = components["schemas"]["ReviewItemOut"];
export type QueueEntry = components["schemas"]["QueueEntryOut"];
export type Decision = NonNullable<ReviewItem["decision"]>;

export const DECISION: Record<Decision, { label: string; tone: "ok" | "warn" | "danger" }> = {
  approved: { label: "Approved", tone: "ok" },
  edited: { label: "Edited", tone: "warn" },
  rejected: { label: "Rejected", tone: "danger" },
};

export function useReviewQueue() {
  return useQuery({
    queryKey: ["reviews"],
    queryFn: async (): Promise<QueueEntry[]> => {
      const { data, error } = await api.GET("/v1/reviews");
      if (error) throw new Error(problemTitle(error));
      return data;
    },
    refetchInterval: 5000, // new runs arrive at the queue on their own
  });
}

export function useReview(runId: string) {
  return useQuery({
    queryKey: ["reviews", runId],
    queryFn: async (): Promise<Review | null> => {
      const { data, error, response } = await api.GET("/v1/runs/{run_id}/review", {
        params: { path: { run_id: runId } },
      });
      if (response.status === 404) return null;
      if (error) throw new Error(problemTitle(error));
      return data;
    },
  });
}

function useReviewAction<T, R>(runId: string, action: (arg: T) => Promise<R>) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: action,
    onSettled: async () => {
      await queryClient.invalidateQueries({ queryKey: ["reviews"] });
      void queryClient.invalidateQueries({ queryKey: ["runs", runId] });
    },
  });
}

export function useDecide(runId: string) {
  return useReviewAction(
    runId,
    async (body: {
      itemIndex: number;
      decision: Decision;
      edited_answer?: string;
      reason?: string;
    }): Promise<ReviewItem> => {
      const { itemIndex, ...rest } = body;
      const { data, error } = await api.POST("/v1/runs/{run_id}/review/items/{item_index}", {
        params: { path: { run_id: runId, item_index: itemIndex } },
        body: rest,
      });
      if (error) throw new Error(problemTitle(error));
      return data;
    },
  );
}

export function useApproveSupported(runId: string) {
  return useReviewAction(runId, async (): Promise<number> => {
    const { data, error } = await api.POST("/v1/runs/{run_id}/review/approve-supported", {
      params: { path: { run_id: runId } },
    });
    if (error) throw new Error(problemTitle(error));
    return data.approved;
  });
}

export function useCompleteReview(runId: string) {
  return useReviewAction(runId, async (): Promise<Review> => {
    const { data, error } = await api.POST("/v1/runs/{run_id}/review/complete", {
      params: { path: { run_id: runId } },
    });
    if (error) throw new Error(problemTitle(error));
    return data;
  });
}
