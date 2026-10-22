"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";

import { api, setCsrfToken } from "./api/client";
import type { components } from "./api/schema";

export type Session = components["schemas"]["SessionInfo"];

/** The signed-in user, or null when signed out. Keeps the CSRF token current. */
export function useSession() {
  return useQuery({
    queryKey: ["session"],
    queryFn: async (): Promise<Session | null> => {
      const { data, response } = await api.GET("/v1/auth/session");
      if (response.status === 401) {
        setCsrfToken(null);
        return null;
      }
      if (!data) throw new Error(`Session check failed (${response.status})`);
      setCsrfToken(data.csrf_token);
      return data;
    },
    staleTime: 60_000,
    retry: false,
  });
}

export function useSignOut() {
  const queryClient = useQueryClient();
  return async (everywhere = false) => {
    if (everywhere) await api.POST("/v1/auth/logout-all");
    else await api.POST("/v1/auth/logout");
    setCsrfToken(null);
    queryClient.setQueryData(["session"], null);
    queryClient.removeQueries({ predicate: (q) => q.queryKey[0] !== "session" });
  };
}
