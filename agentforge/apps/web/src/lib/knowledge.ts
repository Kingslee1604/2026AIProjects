"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, csrfHeaders, problemTitle } from "./api/client";
import type { components } from "./api/schema";

export type KnowledgeBase = components["schemas"]["KnowledgeBaseOut"];
export type KnowledgeDocument = components["schemas"]["DocumentOut"];
export type SearchHit = components["schemas"]["SearchHitOut"];

export const ACCEPTED_FILES = ".pdf,.docx,.md,.markdown,.txt";
export const MAX_UPLOAD_MB = 25;

/** Statuses after which a document no longer changes on its own. */
const SETTLED = new Set(["ready", "failed"]);

export function useKnowledgeBases() {
  return useQuery({
    queryKey: ["knowledge-bases"],
    queryFn: async (): Promise<KnowledgeBase[]> => {
      const { data, error } = await api.GET("/v1/knowledge-bases");
      if (error) throw new Error(problemTitle(error));
      return data;
    },
  });
}

export function useDocuments(kbId: string) {
  return useQuery({
    queryKey: ["knowledge-bases", kbId, "documents"],
    queryFn: async (): Promise<KnowledgeDocument[] | null> => {
      const { data, error, response } = await api.GET("/v1/knowledge-bases/{kb_id}/documents", {
        params: { path: { kb_id: kbId } },
      });
      if (response.status === 404) return null;
      if (error) throw new Error(problemTitle(error));
      return data;
    },
    // Live ingestion status: poll while any document is still being processed.
    refetchInterval: (query) =>
      query.state.data?.some((d) => !SETTLED.has(d.status)) ? 1500 : false,
  });
}

export function useCreateKnowledgeBase() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: { name: string; description: string }) => {
      const { data, error } = await api.POST("/v1/knowledge-bases", { body });
      if (error) throw new Error(problemTitle(error));
      return data;
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["knowledge-bases"] }),
  });
}

/** Uploads one file as the raw request body (the API streams it to disk after sign-in checks). */
export async function uploadDocument(kbId: string, file: File): Promise<KnowledgeDocument> {
  if (file.size > MAX_UPLOAD_MB * 1024 * 1024) {
    throw new Error(`${file.name} is larger than ${MAX_UPLOAD_MB} MB.`);
  }
  const response = await fetch(`/api/v1/knowledge-bases/${encodeURIComponent(kbId)}/documents`, {
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
  return body as KnowledgeDocument;
}

export function useUpload(kbId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (file: File) => uploadDocument(kbId, file),
    onSettled: () => queryClient.invalidateQueries({ queryKey: ["knowledge-bases"] }),
  });
}

export function useDeleteDocument(kbId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (documentId: string) => {
      const { error } = await api.DELETE("/v1/documents/{document_id}", {
        params: { path: { document_id: documentId } },
      });
      if (error) throw new Error(problemTitle(error));
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: ["knowledge-bases", kbId] }),
  });
}

export function useReindexDocument(kbId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (documentId: string) => {
      const { error } = await api.POST("/v1/documents/{document_id}/reindex", {
        params: { path: { document_id: documentId } },
      });
      if (error) throw new Error(problemTitle(error));
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: ["knowledge-bases", kbId] }),
  });
}

export function useDeleteKnowledgeBase() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (kbId: string) => {
      const { error } = await api.DELETE("/v1/knowledge-bases/{kb_id}", {
        params: { path: { kb_id: kbId } },
      });
      if (error) throw new Error(problemTitle(error));
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: ["knowledge-bases"] }),
  });
}

export function useSearch() {
  return useMutation({
    mutationFn: async (body: { query: string; knowledge_base_ids: string[]; k: number }) => {
      const { data, error } = await api.POST("/v1/knowledge/search", { body });
      if (error) throw new Error(problemTitle(error));
      return data;
    },
  });
}
