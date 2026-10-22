"use client";

import { useQuery } from "@tanstack/react-query";

import { api, problemTitle } from "./api/client";
import type { components } from "./api/schema";

export type Overview = components["schemas"]["OverviewOut"];

export function useOverview() {
  return useQuery({
    queryKey: ["overview"],
    queryFn: async (): Promise<Overview> => {
      const { data, error } = await api.GET("/v1/overview");
      if (error) throw new Error(problemTitle(error));
      return data;
    },
    refetchInterval: 10_000, // runs move on their own
  });
}

export const ACTIVITY_LABEL: Record<string, string> = {
  run_started: "Run started",
  review_waiting: "Ready for review",
  review_completed: "Review completed",
  run_succeeded: "Answered file ready",
  run_failed: "Run failed",
  run_cancelled: "Run cancelled",
};

/** Share of kept sentences proven by an exact quote, or null with nothing to measure. */
export function provenShare(sentences: Record<string, number>): number | null {
  const total = Object.values(sentences).reduce((sum, n) => sum + n, 0);
  return total === 0 ? null : Math.round(((sentences.verified ?? 0) / total) * 100);
}
