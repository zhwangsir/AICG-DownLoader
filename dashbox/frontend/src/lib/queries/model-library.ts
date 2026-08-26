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
  /** SDXL 不兼容（生成失败自学习 denylist：unet-only/Flux 等无文本编码器） */
  sdxl_incompatible?: boolean;
  sdxl_incompatible_reason?: string;
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
// 作品库（works：样本视频矩阵画廊）
// ---------------------------------------------------------------------------

export interface WorkItem {
  id: string;
  title: string;
  titleEn?: string;
  category: "anime" | "real" | "3d" | string;
  duration: string;
  engine: string;
  features: string[];
  nsfw: boolean;
  desc: string;
  seconds?: number;
  createdAt?: string;
  has_cover?: boolean;
  sizeBytes?: number;
}

export interface WorksListResult {
  items: WorkItem[];
  total: number;
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
          // 冷启动首扫 NAS 全量需 96~269s，放宽超时避免首查 ERR_ABORTED
          timeout: 300_000,
          retry: 2,
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

// ---------------------------------------------------------------------------
// 作品库查询
// ---------------------------------------------------------------------------

export function useWorksLibrary(
  params: { category?: string; feature?: string; q?: string } = {},
  enabled = true,
) {
  return useQuery({
    queryKey: queryKeys.worksLibraryList(params),
    queryFn: ({ signal }) =>
      api
        .get("api/v1/works", {
          signal,
          searchParams: {
            ...(params.category ? { category: params.category } : {}),
            ...(params.feature ? { feature: params.feature } : {}),
            ...(params.q ? { q: params.q } : {}),
          },
        })
        .json<OkResponse<WorksListResult>>(),
    enabled,
  });
}

export function useRefreshWorksLibrary() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () =>
      api.post("api/v1/works/refresh").json<OkResponse<{ total: number }>>(),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["works", "list"] });
    },
  });
}

export function workMediaUrl(id: string): string {
  return `api/v1/works/${id}/media`;
}

export function workCoverUrl(id: string): string {
  return `api/v1/works/${id}/cover`;
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

/** 生图测试台请求体 */
export interface GenerateImagePayload {
  prompt: string;
  negative_prompt?: string;
  checkpoint: string;
  size?: string;
  /** 提供则把结果落盘为项目媒体并返回 url（画布 R18 节点用）。 */
  project_id?: string;
  /** 参考图 URL（绝对地址，IPAdapter 锚定，走 images/edits）。 */
  reference_url?: string;
}

/** 生图响应（OpenAI images 风格，b64 内嵌；project_id 时附 url/rel_path） */
export interface GenerateImageResult {
  data: { b64_json: string }[];
  url?: string;
  rel_path?: string;
}

export function useGenerateImage() {
  return useMutation({
    mutationFn: (payload: GenerateImagePayload) =>
      api
        .post("api/v1/model-library/generate-image", {
          json: payload,
          timeout: 600_000,
        })
        .json<OkResponse<GenerateImageResult> | ErrorResponse>(),
  });
}

// ---------------------------------------------------------------------------
// R18 视频生成（画布节点：4 预设直提 ComfyUI）
// ---------------------------------------------------------------------------

export interface NsfwVideoPreset {
  id: string;
  label: string;
  trigger: string;
  route: "wan" | "h3";
}

export interface GenerateVideoPayload {
  preset_id: string;
  prompt: string;
  negative_prompt?: string;
  first_frame_url: string;
  width: number;
  height: number;
  length: number;
  seed?: number;
  project_id?: string;
}

export interface GenerateVideoResult {
  seed: number;
  preset_id: string;
  filename: string;
  backend: string;
  url?: string;
  rel_path?: string;
  size?: number;
}

export function useVideoPresets(enabled = true) {
  return useQuery({
    queryKey: ["model-library", "video-presets"] as const,
    queryFn: ({ signal }) =>
      api
        .get("api/v1/model-library/video-presets", { signal })
        .json<OkResponse<{ items: NsfwVideoPreset[] }>>(),
    enabled,
  });
}

export function useGenerateVideo() {
  return useMutation({
    mutationFn: (payload: GenerateVideoPayload) =>
      api
        .post("api/v1/model-library/generate-video", {
          json: payload,
          // 视频出片慢（wan 81帧≈4min / h3 124帧≈4min），上限 15 分钟
          timeout: 900_000,
        })
        .json<OkResponse<GenerateVideoResult> | ErrorResponse>(),
  });
}

// ---------------------------------------------------------------------------
// R18 短剧分镜规划（r18-script/plan：梗概+角色卡 → 结构化 scenes JSON）
// ---------------------------------------------------------------------------

export interface R18SceneData {
  scene_no: number;
  kind: "plot" | "action" | "portrait";
  title: string;
  shot_description: string;
  /** 英文首帧/出图提示词（action 含触发词，跨镜头重复角色锚点）。 */
  image_prompt: string;
  /** I2V 运动提示词（portrait 为空）。 */
  video_prompt: string;
  /** action 镜头预设 id（wan22-* / h3-aio），其余空。 */
  preset_id: string;
  dialogue: string;
  narration: string;
  duration_sec: number;
  audio: "native" | "tts" | "none";
  /** 影视分镜补充字段（工厂流水线：分镜表/数字资产工序消费）。 */
  shot_size?: string;
  camera_move?: string;
  action_desc?: string;
  expression?: string;
  scene_desc?: string;
  /** 配音情绪（TTS instruct2 情感指令）。 */
  emotion?: string;
}

export interface R18ScriptPlanResult {
  title: string;
  scenes: R18SceneData[];
  /** 分集编号（单集恒为 1）。 */
  episode_no?: number;
  /** 分集剧本全集（episode_count > 1 时返回）。 */
  episodes?: R18ScriptPlanResult[];
}

export interface R18ScriptPlanPayload {
  synopsis: string;
  characters?: { name: string; description?: string }[];
  style_hint?: string;
  duration_sec?: number;
  aspect?: "9:16" | "16:9" | "1:1";
  /** 分集数（1=单集；>1 逐集生成并带连贯上下文）。 */
  episode_count?: number;
}

export function useR18ScriptPlan() {
  return useMutation({
    mutationFn: (payload: R18ScriptPlanPayload) =>
      api
        .post("api/v1/model-library/r18-script/plan", {
          json: payload,
          // LLM 结构化输出（多镜头 + output_retries）可能较慢
          timeout: 300_000,
        })
        .json<OkResponse<R18ScriptPlanResult> | ErrorResponse>(),
  });
}

/** ky v2 对非 2xx 抛 HTTPError 时响应体已解析在 error.data（{detail}）——
 *  先读 detail 再回退 message，403/422/502 的人话错误才能到节点 UI。 */
export function gatewayErrorMessage(error: unknown, fallback = "请求失败"): string {
  const data = (error as { data?: { detail?: string; error?: string } } | null)?.data;
  if (data) {
    if (typeof data.detail === "string" && data.detail) return data.detail;
    if (typeof data.error === "string" && data.error) return data.error;
  }
  if (error instanceof Error && error.message) return error.message;
  return fallback;
}

// ---------------------------------------------------------------------------
// R18 配音（r18-tts：CosyVoice2 代理，画布 R18 出片节点用）
// ---------------------------------------------------------------------------

/** 音色列表（后端 R18_TTS_VOICES 同源）：
 * 真人配音演员（2026-08-19 注册，推荐）+ edge-tts 合成（兜底）。 */
export const R18_TTS_VOICE_OPTIONS: ReadonlyArray<{
  value: string;
  label: string;
}> = [
  // --- 真人中文（配音演员） ---
  { value: "human-zh-paimon", label: "派蒙（真人·元气）" },
  { value: "human-zh-ganyu", label: "甘雨（真人·温柔）" },
  { value: "human-zh-nahida", label: "纳西妲（真人·幼齿）" },
  { value: "human-zh-barbara", label: "芭芭拉（真人·治愈）" },
  { value: "human-zh-hutao", label: "胡桃（真人·俏皮）" },
  { value: "human-zh-klee", label: "可莉（真人·活泼）" },
  { value: "human-zh-raiden", label: "雷电将军（真人·威严）" },
  { value: "human-zh-keqing", label: "刻晴（真人·干练）" },
  { value: "human-zh-yae", label: "八重神子（真人·妩媚）" },
  { value: "human-zh-ayaka", label: "神里绫华（真人·优雅）" },
  // --- 真人日文（动漫声优，可说中文） ---
  { value: "human-ja-moan", label: "R18·气声（真人日文）" },
  { value: "human-ja-oneesan", label: "御姐（真人日文）" },
  { value: "human-ja-panting", label: "R18·喘息（真人日文）" },
  { value: "human-ja-soft", label: "软妹（真人日文）" },
  { value: "human-ja-timid", label: "怯懦（真人日文）" },
  // --- edge-tts 合成（兜底） ---
  { value: "zh-CN-XiaoxiaoNeural", label: "晓晓（合成·温婉）" },
  { value: "zh-CN-XiaohanNeural", label: "晓涵（合成·知性）" },
  { value: "zh-CN-XiaoyiNeural", label: "晓伊（合成·活泼）" },
  { value: "zh-CN-YunjianNeural", label: "云健（合成·沉稳）" },
  { value: "zh-CN-YunxiNeural", label: "云希（合成·阳光）" },
  { value: "zh-CN-YunyangNeural", label: "云扬（合成·新闻）" },
];

export interface R18TtsPayload {
  text: string;
  voice?: string;
  emotion?: string;
  speed?: number;
  source?: "dialogue" | "narration";
  project_id?: string;
}

export interface R18TtsResult {
  url?: string;
  rel_path?: string;
  audio_b64?: string;
  format: string;
  size: number;
}

export function useR18Tts() {
  return useMutation({
    mutationFn: (payload: R18TtsPayload) =>
      api
        .post("api/v1/model-library/r18-tts", {
          json: payload,
          timeout: 120_000,
        })
        .json<OkResponse<R18TtsResult> | ErrorResponse>(),
  });
}

// ---------------------------------------------------------------------------
// R18 成片合成（r18-compose：镜头 concat + 分层混音 + 字幕烧录）
// ---------------------------------------------------------------------------

export interface R18ComposeShotPayload {
  video_url: string;
  tts_url?: string;
  audio_mode: "native" | "tts" | "none";
}

export interface R18TitleCardPayload {
  text?: string;
  duration_sec?: number;
  bg_color?: string;
}

export interface R18ComposePayload {
  project_id: string;
  title?: string;
  shots: R18ComposeShotPayload[];
  srt?: string;
  /** 逐镜头字幕文本（与 shots 对齐；后端按真实时长重建 SRT 烧录，优先生于 srt）。 */
  subtitles?: Array<string | null>;
  /** ---- 工厂后期合成 v2（全部可选） ---- */
  bgm_url?: string;
  bgm_volume?: number;
  /** 环境音效轨（循环铺满全片）。 */
  sfx_url?: string;
  sfx_volume?: number;
  color_profile?: "none" | "warm" | "cool" | "film";
  transition?: "none" | "fade";
  transition_sec?: number;
  opening?: R18TitleCardPayload;
  closing?: R18TitleCardPayload;
}

export interface R18ComposeResult {
  url: string;
  rel_path: string;
  size: number;
  duration_sec: number;
  shots: number;
  /** 烧录用的最终 SRT（供 QC 工序 ASR 回读比对）。 */
  srt?: string;
}

export function useR18Compose() {
  return useMutation({
    mutationFn: (payload: R18ComposePayload) =>
      api
        .post("api/v1/model-library/r18-compose", {
          json: payload,
          timeout: 600_000,
        })
        .json<OkResponse<R18ComposeResult> | ErrorResponse>(),
  });
}

// ---------------------------------------------------------------------------
// R18 工厂质检（r18-factory/qc：时长/AV同步/音轨/字幕ASR回读/剧情LLM）
// ---------------------------------------------------------------------------

export interface R18FactoryQcPayload {
  project_id: string;
  compose_url: string;
  srt?: string;
  scenes?: Array<{
    scene_no: number;
    shot_description?: string;
    dialogue?: string;
    narration?: string;
    duration_sec: number;
  }>;
  llm_review?: boolean;
}

export interface R18FactoryQcResult {
  passed: boolean;
  duration_sec: number;
  expected_duration_sec: number;
  av_sync_ok: boolean | null;
  has_audio: boolean;
  asr_similarity: number | null;
  subtitle_ok: boolean | null;
  llm: { passed: boolean; issues: Array<{ severity: string; scene_no?: number | null; message: string }> } | null;
}

export function useR18FactoryQc() {
  return useMutation({
    mutationFn: (payload: R18FactoryQcPayload) =>
      api
        .post("api/v1/model-library/r18-factory/qc", {
          json: payload,
          // ASR 回读 + LLM 审查较慢
          timeout: 300_000,
        })
        .json<OkResponse<R18FactoryQcResult> | ErrorResponse>(),
  });
}
