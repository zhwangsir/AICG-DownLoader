// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import { queryKeys } from "@/lib/query-keys";
import type { ErrorResponse, OkResponse } from "@/types/api";

// ---------------------------------------------------------------------------
// 类型（与后端 novelvideo.model_library 契约对齐）
// ---------------------------------------------------------------------------

export interface ModelLibraryEntry {
  name: string;
  rel_path: string;
  root: string;
  type: string;
  size: number;
  mtime: number;
  nsfw: boolean;
}

export interface ModelLibraryListResult {
  items: ModelLibraryEntry[];
  total: number;
  types: string[];
  scanned_at: number;
  cache_hit: boolean;
}

export interface CivitaiFile {
  name: string;
  size_kb: number;
  download_url: string;
  sha256: string | null;
  primary: boolean;
}

export interface CivitaiVersion {
  id: number | null;
  name: string;
  files: CivitaiFile[];
}

export interface CivitaiModel {
  id: number | null;
  name: string;
  type: string;
  nsfw: boolean;
  versions: CivitaiVersion[];
}

export interface CivitaiSearchResult {
  items: CivitaiModel[];
  total: number;
}

export type DownloadTaskStatus =
  | "pending"
  | "running"
  | "done"
  | "error"
  | "canceled";

export interface ModelDownloadTask {
  task_id: string;
  filename: string;
  subdir: string;
  dest: string;
  source_url: string;
  sha256: string | null;
  nsfw: boolean;
  status: DownloadTaskStatus;
  downloaded: number;
  total: number;
  speed_bps: number;
  error: string | null;
  created_at: number;
  finished_at?: number;
}

export interface NsfwStatus {
  nsfw_enabled: boolean;
}

export interface StartDownloadInput {
  download_url: string;
  filename: string;
  subdir: string;
  sha256?: string | null;
  nsfw?: boolean;
}

export interface SetNsfwInput {
  enabled: boolean;
}

export interface PreflightRef {
  node_id: string;
  class_type: string;
  field: string;
  filename: string;
  expected_types: string[];
  present: boolean;
  present_anywhere: boolean;
}

export interface PreflightResult {
  refs: PreflightRef[];
  missing: PreflightRef[];
  total: number;
  missing_count: number;
  checked_at: number;
}

// ---------------------------------------------------------------------------
// 查询
// ---------------------------------------------------------------------------

export function useNsfwStatus(enabled = true) {
  return useQuery({
    queryKey: queryKeys.modelLibraryNsfw(),
    queryFn: ({ signal }) =>
      api
        .get("api/v1/model-library/nsfw", { signal })
        .json<OkResponse<NsfwStatus>>(),
    enabled,
  });
}

export function useModelLibrary(
  params: { type?: string; q?: string; includeNsfw?: boolean },
  enabled = true,
) {
  return useQuery({
    queryKey: queryKeys.modelLibraryList(params),
    queryFn: ({ signal }) =>
      api
        .get("api/v1/model-library/models", {
          signal,
          searchParams: {
            ...(params.type ? { type: params.type } : {}),
            ...(params.q ? { q: params.q } : {}),
            ...(params.includeNsfw ? { include_nsfw: "true" } : {}),
          },
        })
        .json<OkResponse<ModelLibraryListResult>>(),
    enabled,
  });
}

export function useRefreshModelLibrary() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () =>
      api
        .get("api/v1/model-library/models", {
          searchParams: { refresh: "true" },
        })
        .json<OkResponse<ModelLibraryListResult>>(),
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: ["model-library", "models"],
      });
    },
  });
}

export function useCivitaiSearch(
  params: { q: string; type?: string },
  enabled = true,
) {
  return useQuery({
    queryKey: queryKeys.modelLibrarySearch(params),
    queryFn: ({ signal }) =>
      api
        .get("api/v1/model-library/search", {
          signal,
          searchParams: {
            q: params.q,
            ...(params.type ? { type: params.type } : {}),
          },
        })
        .json<OkResponse<CivitaiSearchResult>>(),
    enabled: enabled && params.q.trim().length > 0,
    retry: 1,
  });
}

const downloadTasksPollMs = 1500;

export function useModelDownloadTasks(enabled = true) {
  return useQuery({
    queryKey: queryKeys.modelLibraryDownloads(),
    queryFn: ({ signal }) =>
      api
        .get("api/v1/model-library/downloads", { signal })
        .json<OkResponse<{ items: ModelDownloadTask[] }>>(),
    enabled,
    refetchInterval: (query) => {
      const items = query.state.data?.data?.items ?? [];
      const active = items.some(
        (t) => t.status === "pending" || t.status === "running",
      );
      return active ? downloadTasksPollMs : false;
    },
  });
}

// ---------------------------------------------------------------------------
// 变更
// ---------------------------------------------------------------------------

export function useStartModelDownload() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: StartDownloadInput) =>
      api
        .post("api/v1/model-library/downloads", { json: input })
        .json<OkResponse<ModelDownloadTask> | ErrorResponse>(),
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: queryKeys.modelLibraryDownloads(),
      });
    },
  });
}

export function useCancelModelDownload() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (taskId: string) =>
      api
        .delete(`api/v1/model-library/downloads/${taskId}`)
        .json<OkResponse<{ task_id: string }> | ErrorResponse>(),
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: queryKeys.modelLibraryDownloads(),
      });
    },
  });
}

export function useSetNsfw() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: SetNsfwInput) =>
      api
        .post("api/v1/model-library/nsfw", { json: input })
        .json<OkResponse<NsfwStatus> | ErrorResponse>(),
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: queryKeys.modelLibraryNsfw(),
      });
      void queryClient.invalidateQueries({
        queryKey: ["model-library", "models"],
      });
    },
  });
}

// ---------------------------------------------------------------------------
// NSFW 手动标记（覆盖关键词判定）
// ---------------------------------------------------------------------------

export interface NsfwMarksResult {
  marks: Record<string, boolean>;
  count: number;
}

export interface SetNsfwMarkInput {
  rel_path: string;
  /** true=标 NSFW，false=标 SFW，null=清除覆盖回退关键词 */
  nsfw: boolean | null;
}

export function useNsfwMarks(enabled = true) {
  return useQuery({
    queryKey: ["model-library", "nsfw-marks"] as const,
    queryFn: ({ signal }) =>
      api
        .get("api/v1/model-library/nsfw/marks", { signal })
        .json<OkResponse<NsfwMarksResult>>(),
    enabled,
  });
}

export function useSetNsfwMark() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: SetNsfwMarkInput) =>
      api
        .post("api/v1/model-library/nsfw/marks", { json: input })
        .json<OkResponse<NsfwMarksResult> | ErrorResponse>(),
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: ["model-library", "nsfw-marks"],
      });
      void queryClient.invalidateQueries({
        queryKey: ["model-library", "models"],
      });
    },
  });
}

/** 全量模型条目（含 NSFW，供 picker/预检共用；React Query 缓存去重） */
export function useModelLibraryItems(enabled = true) {
  const query = useModelLibrary({ includeNsfw: true }, enabled);
  return { ...query, items: query.data?.data?.items ?? [] };
}

export function usePreflightWorkflow() {
  return useMutation({
    mutationFn: (workflow: Record<string, unknown>) =>
      api
        .post("api/v1/model-library/preflight", { json: { workflow } })
        .json<OkResponse<PreflightResult> | ErrorResponse>(),
  });
}
