"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, problemTitle } from "./api/client";
import type { components } from "./api/schema";

export type ApiKey = components["schemas"]["ApiKeyOut"];
export type Scope = components["schemas"]["ApiKeyIn"]["scopes"][number];

export const SCOPES: { value: Scope; label: string; help: string }[] = [
  { value: "api:read", label: "Read", help: "Read knowledge bases, documents, answers, and runs." },
  {
    value: "api:write",
    label: "Write",
    help: "Upload documents, ask questions, and start questionnaire runs.",
  },
  {
    value: "mcp:use",
    label: "AI assistants (MCP)",
    help: "Let an AI assistant search your documents through the MCP server.",
  },
];

export const EXPIRY_DAYS = [30, 90, 180, 365] as const;

export function keyState(key: ApiKey, now: Date = new Date()): "active" | "revoked" | "expired" {
  if (key.revoked_at) return "revoked";
  return new Date(key.expires_at) <= now ? "expired" : "active";
}

export function useApiKeys() {
  return useQuery({
    queryKey: ["api-keys"],
    queryFn: async (): Promise<ApiKey[]> => {
      const { data, error } = await api.GET("/v1/api-keys");
      if (error) throw new Error(problemTitle(error));
      return data;
    },
  });
}

export function useCreateApiKey() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: { name: string; scopes: Scope[]; expires_in_days: number }) => {
      const { data, error } = await api.POST("/v1/api-keys", { body });
      if (error) throw new Error(problemTitle(error));
      return data;
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["api-keys"] });
    },
  });
}

export function useRevokeApiKey() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (keyId: string) => {
      const { error } = await api.DELETE("/v1/api-keys/{key_id}", {
        params: { path: { key_id: keyId } },
      });
      if (error) throw new Error(problemTitle(error));
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: ["api-keys"] });
    },
  });
}
