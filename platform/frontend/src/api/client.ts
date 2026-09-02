/// <reference types="vite/client" />
const API_BASE = import.meta.env.VITE_API_BASE || "/api/drama";

/** 各端点分级超时（毫秒）：按后端实际耗时分布设定，避免阻塞时前端永久等待 */
export const API_TIMEOUTS = {
  script: 300_000, // 剧本生成（LLM 多场景展开）
  character: 180_000, // 角色卡生成（图像）
  storyboard: 240_000, // 单场景分镜
  storyboardBatch: 600_000, // 批量分镜
  video: 600_000, // 单场景视频
  videoBatch: 1_800_000, // 批量视频
  taskCreate: 30_000, // 异步任务创建/单次轮询请求
  voice: 120_000, // 配音
  subtitle: 120_000, // 字幕
  compose: 900_000, // 剪辑合成
  quality: 300_000, // 整体质检
  visualQuality: 600_000, // 视觉质检（抽帧+VLM）
  pollInterval: 3_000, // 轮询间隔
} as const;

/** 带超时保护的 fetch：超时后抛出友好错误，避免后端阻塞时前端永久等待 */
async function fetchWithTimeout(
  url: string,
  options: RequestInit,
  timeoutMs: number
): Promise<Response> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, { ...options, signal: controller.signal });
  } catch (e) {
    if (e instanceof DOMException && e.name === "AbortError") {
      throw new Error(
        `请求超时（${Math.round(timeoutMs / 1000)}秒）。后端可能负载过高，请稍后重试。`
      );
    }
    throw e;
  } finally {
    clearTimeout(timer);
  }
}

export interface ScriptData {
  project_id: string;
  title: string;
  genre: string;
  aspect_ratio: string;
  total_episodes: number;
  characters: CharacterData[];
  scenes: SceneData[];
}

export interface CharacterData {
  character_id: string;
  name: string;
  role: string;
  age: number | null;
  description: string;
  personality: string;
}

export interface SceneData {
  scene_id: number;
  episode: number;
  shot_type: string;
  description: string;
  prompt: string;
  negative_prompt: string;
  character_actions: string;
  dialogue: string;
  emotion: string;
  duration_seconds: number;
  camera_movement: string;
}

export interface CharacterCardData {
  character_id: string;
  name: string;
  reference_images: Record<string, string>;
  consistency_level: string;
  used_prompts?: {
    positive_prompt: string;
    negative_prompt: string;
  };
}

export interface CharacterPreviewResult {
  character_id: string;
  character: CharacterData;
  style: string;
  search_reference: string;
  prompts: {
    front_view_prompt: string;
    side_view_prompt: string;
    closeup_prompt: string;
    negative_prompt: string;
  };
}

export interface StoryboardData {
  scene_id: number;
  image_url: string;
  prompt_used: string;
  preview_video_url?: string;
  /** M25.9 C1 线稿先行：是否为线稿；线稿时 sketch_seed 为精绘确定性锚点 */
  is_sketch?: boolean;
  sketch_seed?: number | null;
}

export interface StoryboardBatchData {
  results: StoryboardData[];
  failed_scenes: number[];
}

export interface VideoData {
  scene_id: number;
  video_url: string;
  duration_seconds: number;
}

export interface VideoBatchData {
  results: VideoData[];
  failed_scenes: number[];
}

export interface VoiceAudioItem {
  filename: string;
  voice: string;
  text: string;
  audio_url: string;
}

export interface VoiceData {
  scene_id: number;
  audio_urls: VoiceAudioItem[];
  total_lines: number;
}

export interface SubtitleSegment {
  start: number;
  end: number;
  text: string;
}

export interface SubtitleData {
  scene_id: number;
  srt_content: string;
  segments: SubtitleSegment[];
  language: string;
  srt_url: string;
}

export interface EditSegmentInput {
  scene_id: number;
  video_url: string;
  audio_url: string;
  subtitle_url: string;
  duration_seconds?: number;
}

export interface EditData {
  project_id: string;
  title: string;
  final_video_url: string;
  duration_seconds: number;
  segments_count: number;
}

export interface QualityCheckIssue {
  category: string;
  severity: "info" | "warning" | "critical";
  scene_id: number | null;
  message: string;
  suggestion: string;
}

export interface QualityCheckData {
  project_id: string;
  title: string;
  score: number;
  summary: string;
  issues: QualityCheckIssue[];
  checked_at: number;
}

export interface QualityVisualIssue {
  category: string;
  severity: "info" | "warning" | "critical";
  timestamp: number | null;
  message: string;
  suggestion: string;
}

export interface QualityVisualData {
  project_id: string;
  title: string;
  scene_id: number;
  score: number;
  summary: string;
  issues: QualityVisualIssue[];
  checked_at: number;
}

/** 字幕回写修正对 */
export interface SubtitleCorrection {
  wrong: string;
  right: string;
}

/** 单段字幕修正明细 */
export interface SubtitleFixDetail {
  scene_id: number;
  original_text: string;
  fixed_text: string;
  applied: SubtitleCorrection[];
}

/** 字幕回写修正结果（P1-2 字幕闭环） */
export interface SubtitleFixResult {
  fixed_subtitles: SubtitleData[];
  corrections: SubtitleCorrection[];
  fixed_count: number;
  details: SubtitleFixDetail[];
  persisted_files: string[];
}

export interface AgentResponse<T> {
  success: boolean;
  data: T | null;
  error: string | null;
  elapsed_seconds: number;
}

export interface AsyncTaskResponse {
  task_id: string;
  agent: string;
  status: string;
  poll_url: string;
  stream_url: string;
}

export interface ProgressEvent {
  task_id: string;
  agent: string;
  status: "pending" | "running" | "completed" | "failed";
  percent: number;
  message: string;
  result: unknown;
  error: string | null;
  updated_at: number;
}

export async function generateScript(params: {
  premise: string;
  genre: string;
  episodes: number;
  scenes_per_episode: number;
}): Promise<AgentResponse<ScriptData>> {
  const resp = await fetchWithTimeout(
    `${API_BASE}/script/generate`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(params),
    },
    API_TIMEOUTS.script
  );
  return resp.json();
}

export async function generateCharacter(params: {
  character: CharacterData;
  style: string;
  consistency_level: string;
  custom_positive_prompt?: string;
  custom_negative_prompt?: string;
  preview_positive_prompt?: string;
  preview_negative_prompt?: string;
}): Promise<AgentResponse<CharacterCardData>> {
  const resp = await fetchWithTimeout(
    `${API_BASE}/character/generate`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(params),
    },
    API_TIMEOUTS.character
  );
  return resp.json();
}

export async function previewCharacter(
  params: {
    character: CharacterData;
    style: string;
  },
  signal?: AbortSignal
): Promise<AgentResponse<CharacterPreviewResult>> {
  const resp = await fetch(`${API_BASE}/character/preview`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params),
    signal,
  });
  return resp.json();
}

export async function generateStoryboard(params: {
  scene: SceneData;
  characters: CharacterData[];
  style: string;
  /** M25.9 C1 线稿先行：true=线稿模式（低步数快速看构图） */
  sketch_mode?: boolean;
  /** M25.9 C1 同 seed 防漂移：精绘时回传线稿 seed */
  refine_seed?: number | null;
}): Promise<AgentResponse<StoryboardData>> {
  const resp = await fetchWithTimeout(
    `${API_BASE}/storyboard/generate`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(params),
    },
    API_TIMEOUTS.storyboard
  );
  return resp.json();
}

export async function generateStoryboardBatch(params: {
  scenes: SceneData[];
  characters: CharacterData[];
  style: string;
}): Promise<AgentResponse<StoryboardBatchData>> {
  const resp = await fetchWithTimeout(
    `${API_BASE}/storyboard/generate_batch`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(params),
    },
    API_TIMEOUTS.storyboardBatch
  );
  return resp.json();
}

export type VideoGenerateParams = {
  scene_id: number;
  image_url: string;
  prompt: string;
  negative_prompt: string;
  duration_seconds: number;
  /** P3: true = H3 Turbo preview; false/omit = 20-step final */
  preview?: boolean;
  /** P3: "preview" | "final". "preview" same as preview=true */
  quality?: "preview" | "final" | string;
};

export async function generateVideo(params: VideoGenerateParams): Promise<AgentResponse<VideoData>> {
  const resp = await fetchWithTimeout(
    `${API_BASE}/video/generate`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(params),
    },
    API_TIMEOUTS.video
  );
  return resp.json();
}

export async function generateVideoBatch(params: {
  items: VideoGenerateParams[];
}): Promise<AgentResponse<VideoBatchData>> {
  const resp = await fetchWithTimeout(
    `${API_BASE}/video/generate_batch`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(params),
    },
    API_TIMEOUTS.videoBatch
  );
  return resp.json();
}

export async function generateVideoAsync(params: VideoGenerateParams): Promise<AsyncTaskResponse> {
  const resp = await fetchWithTimeout(
    `${API_BASE}/video/generate_async`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(params),
    },
    API_TIMEOUTS.taskCreate
  );
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(`创建异步任务失败: ${resp.status} ${text}`);
  }
  return resp.json();
}

/**
 * M25.1 单镜头锚点重拍：从 shot_params.json 恢复快照参数仅重跑该镜头。
 * 同步接口（重拍单个视频耗时分钟级），用 videoBatch 长超时；
 * seed 不传则沿用快照锁定值，overridePrompt 非空则替换快照提示词。
 */
export async function rerunShot(params: {
  project_id: string;
  scene_id: number;
  seed?: number | null;
  reseed?: boolean;
  override_prompt?: string;
}): Promise<AgentResponse<VideoData>> {
  const resp = await fetchWithTimeout(
    `${API_BASE}/video/rerun-shot`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(params),
    },
    API_TIMEOUTS.videoBatch
  );
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(`锚点重拍失败: ${resp.status} ${text}`);
  }
  return resp.json();
}

export async function generateVoice(params: {
  scene_id: number;
  dialogues: Array<{
    text: string;
    character_name: string;
    character_role: string;
    character_age: number | null;
    rate: string;
  }>;
}): Promise<AgentResponse<VoiceData>> {
  const resp = await fetchWithTimeout(
    `${API_BASE}/voice/generate`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(params),
    },
    API_TIMEOUTS.voice
  );
  return resp.json();
}

export async function generateSubtitle(params: {
  scene_id: number;
  audio_url: string;
  language: string;
}): Promise<AgentResponse<SubtitleData>> {
  const resp = await fetchWithTimeout(
    `${API_BASE}/subtitle/generate`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(params),
    },
    API_TIMEOUTS.subtitle
  );
  return resp.json();
}

export async function composeVideo(params: {
  project_id: string;
  title: string;
  segments: EditSegmentInput[];
  transition?: string;
  bgm_url?: string | null;
  output_resolution?: string;
  output_fps?: number;
}): Promise<AgentResponse<EditData>> {
  const resp = await fetchWithTimeout(
    `${API_BASE}/edit/compose`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(params),
    },
    API_TIMEOUTS.compose
  );
  return resp.json();
}

export async function checkQuality(params: {
  project_id: string;
  title: string;
  characters: CharacterData[];
  scenes: SceneData[];
  subtitles: SubtitleData[];
  check_types?: string[];
}): Promise<AgentResponse<QualityCheckData>> {
  const resp = await fetchWithTimeout(
    `${API_BASE}/quality/check`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(params),
    },
    API_TIMEOUTS.quality
  );
  return resp.json();
}

export async function checkVisualQuality(params: {
  project_id: string;
  title: string;
  scene_id: number;
  video_url: string;
  check_types?: string[];
  max_frames?: number;
}): Promise<AgentResponse<QualityVisualData>> {
  const resp = await fetchWithTimeout(
    `${API_BASE}/quality/visual`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(params),
    },
    API_TIMEOUTS.visualQuality
  );
  return resp.json();
}

/**
 * 字幕闭环（P1-2）：基于质检 issues 自动修正 ASR 错别字，回写 SRT 文件。
 * 流程：质检报告 → 提取 (wrong→right) 修正对 → 替换字幕文本 → 重建 SRT → 覆盖原文件。
 */
export async function applySubtitleFix(params: {
  subtitles: SubtitleData[];
  issues: QualityCheckIssue[];
  persist?: boolean;
}): Promise<AgentResponse<SubtitleFixResult>> {
  const resp = await fetch(`${API_BASE}/quality/apply_subtitle_fix`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params),
  });
  return resp.json();
}

export async function agentAssist(params: {
  text: string;
  context: string;
  action: "polish" | "expand" | "shorten" | "rewrite";
  extra_instruction?: string;
}): Promise<AgentResponse<{ text: string; action: string; context: string }>> {
  const resp = await fetchWithTimeout(
    `${API_BASE}/agent/assist`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(params),
    },
    60_000
  );
  return resp.json();
}

export async function checkHealth(): Promise<Record<string, unknown>> {
  const resp = await fetch(`${API_BASE}/health`);
  return resp.json();
}

/**
 * 视频异步任务轮询：每 3 秒查询一次，直到完成/失败或超过最大等待时间。
 * 单次请求带 taskCreate 超时，整体带截止期限，避免任务卡死时前端永久轮询。
 */
export async function pollVideoTask(
  pollUrl: string,
  maxWaitMs: number = API_TIMEOUTS.video,
  onProgress?: (evt: ProgressEvent) => void
): Promise<ProgressEvent> {
  const deadline = Date.now() + maxWaitMs;
  while (Date.now() < deadline) {
    await new Promise((resolve) =>
      setTimeout(resolve, API_TIMEOUTS.pollInterval)
    );
    const resp = await fetchWithTimeout(pollUrl, {}, API_TIMEOUTS.taskCreate);
    if (!resp.ok) {
      throw new Error(`轮询失败: ${resp.status}`);
    }
    const evt: ProgressEvent = await resp.json();
    onProgress?.(evt);
    if (evt.status === "completed" || evt.status === "failed") {
      return evt;
    }
  }
  throw new Error(
    `轮询超时（${Math.round(maxWaitMs / 1000)}秒）。任务仍在运行，请稍后重试。`
  );
}

/* ------------------------------------------------------------------ */
/* M8 全链路一键成片（/pipeline/*）                                     */
/* ------------------------------------------------------------------ */

export interface PipelineRunParams {
  premise: string;
  genre: string;
  episodes: number;
  scenes_per_episode: number;
  monetization_mode: "iaa" | "iap";
  style: string;
  generate_character_refs: boolean;
  max_character_refs?: number;
  video_duration_seconds?: number;
  run_quality_check: boolean;
  run_visual_check?: boolean;
  ai_label_enabled: boolean;
  license_number?: string;
  output_resolution?: string;
  output_fps?: number;
}

/** 全链路终态报告（result 字段），steps 键为各环节名称 */
export interface PipelineReport {
  project_id: string;
  premise: string;
  started_at: number;
  steps: Record<string, Record<string, unknown>>;
  passed?: boolean;
  cancelled?: boolean;
  error?: string;
  total_elapsed_seconds?: number;
}

/**
 * 将后端返回的绝对任务 URL（http://localhost:PORT/...）重写为与 API_BASE 同源。
 * 后端以 localhost 拼 poll/stream URL，远程部署时浏览器无法直连，需按当前 API 入口重写。
 */
export function resolveTaskUrl(url: string): string {
  try {
    const u = new URL(url);
    if (API_BASE.startsWith("http")) {
      const base = new URL(API_BASE);
      u.protocol = base.protocol;
      u.host = base.host;
      return u.toString();
    }
    // API_BASE 为相对路径（经代理/同源部署）：仅保留 path，走当前页面源
    return u.pathname + u.search;
  } catch {
    return url;
  }
}

/** 将后端返回的媒体路径（/static/...）补全为可访问的绝对 URL */
export function resolveStaticUrl(path: string): string {
  if (!path || path.startsWith("http")) return path;
  if (API_BASE.startsWith("http")) {
    const base = new URL(API_BASE);
    return `${base.origin}${path}`;
  }
  return path;
}

/** M8 一键全链路：启动后台流水线任务，返回 task_id + poll/stream URL */
export async function runPipeline(
  params: PipelineRunParams
): Promise<AsyncTaskResponse> {
  const resp = await fetchWithTimeout(
    `${API_BASE}/pipeline/run`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(params),
    },
    API_TIMEOUTS.taskCreate
  );
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(`启动全链路任务失败: ${resp.status} ${text}`);
  }
  return resp.json();
}

/** M8 一键全链路：请求取消任务（步骤间生效） */
export async function cancelPipeline(
  taskId: string
): Promise<{ success: boolean; data?: { cancel_requested: boolean } }> {
  const resp = await fetchWithTimeout(
    `${API_BASE}/pipeline/cancel/${encodeURIComponent(taskId)}`,
    { method: "POST" },
    API_TIMEOUTS.taskCreate
  );
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(`取消任务失败: ${resp.status} ${text}`);
  }
  return resp.json();
}

/**
 * M9 从全链路终态报告中提取完整剧本数据（steps.script.data，M9 起后端内嵌）。
 * 报告结构防御性解析：任何一层缺失都返回 null，由调用方降级处理。
 */
export function extractScriptFromReport(
  report: PipelineReport | null | undefined
): ScriptData | null {
  const data = report?.steps?.script?.data;
  if (!data || typeof data !== "object") return null;
  const s = data as Partial<ScriptData>;
  if (
    typeof s.project_id !== "string" ||
    typeof s.title !== "string" ||
    !Array.isArray(s.characters) ||
    !Array.isArray(s.scenes)
  ) {
    return null;
  }
  return data as ScriptData;
}

/* ------------------------------------------------------------------ */
/* 模型注册表（下载器 ↔ 工作台打通）                                    */
/* ------------------------------------------------------------------ */

/** 注册表中的单个 LoRA 条目 */
export interface ModelLoraEntry {
  filename: string;
  name: string;
  style_key: string;
  trigger_words: string[];
  weight: number;
  sha256: string;
  size_kb: number;
  downloaded: boolean;
  subdir: string;
  downloaded_at: number | null;
}

/** 模型注册表返回结构（GET /models/registry） */
export interface ModelRegistry {
  loras: ModelLoraEntry[];
  downloader_models: Record<string, unknown>[];
  stats: Record<string, unknown>;
  sources: Record<string, unknown>;
}

/** 获取模型注册表：汇总下载器与工作台两侧的 LoRA 模型清单及统计 */
export async function getModelRegistry(): Promise<ModelRegistry> {
  const resp = await fetchWithTimeout(
    `${API_BASE}/models/registry`,
    {},
    API_TIMEOUTS.taskCreate
  );
  return resp.json();
}

/* ------------------------------------------------------------------ */
/* 角色资产库（主体库 @引用可视化的资产侧数据源）                          */
/* ------------------------------------------------------------------ */

/** 角色资产库条目（GET /character-library/list 的 data 元素） */
export interface CharacterAssetEntry {
  character_id: string;
  name: string;
  role: string;
  age: number | null;
  description: string;
  personality: string;
  /** 三视图定妆照 URL（front/side/closeup） */
  reference_images: Record<string, string>;
  appearance_lock: string;
  locked: boolean;
  consistency_level: string;
  created_at: number;
  updated_at: number;
}

/** 获取角色资产库全量列表（按更新时间倒序） */
export async function getCharacterLibrary(): Promise<CharacterAssetEntry[]> {
  const resp = await fetchWithTimeout(
    `${API_BASE}/character-library/list`,
    {},
    API_TIMEOUTS.taskCreate
  );
  const body = await resp.json();
  return (body?.data ?? []) as CharacterAssetEntry[];
}

// ====================================================================
// M27 NAS 模型库 / 模型下载 / NSFW 设置（后端 routers/models.py）
// API_BASE 为 /api/drama，模型/设置端点同前缀替换派生
// ====================================================================
const MODELS_BASE = API_BASE.replace(/\/api\/drama\/?$/, "/api/models");
const SETTINGS_BASE = API_BASE.replace(/\/api\/drama\/?$/, "/api/settings");
const PANEL_BASE = API_BASE.replace(/\/api\/drama\/?$/, "/api/panel");

/** 从 FastAPI 错误响应提取 detail 消息（兼容字符串/对象 detail） */
async function extractError(resp: Response, prefix: string): Promise<Error> {
  let msg = `${prefix}: ${resp.status}`;
  try {
    const body = await resp.json();
    const detail = body?.detail;
    if (typeof detail === "string") msg = detail;
    else if (detail) msg = `${msg} ${JSON.stringify(detail)}`;
  } catch {
    msg = `${msg} ${await resp.text().catch(() => "")}`;
  }
  return new Error(msg);
}

export interface NasModelEntry {
  name: string;
  rel_path: string;
  root: string;
  type: string;
  size: number;
  mtime: number;
  nsfw: boolean;
}

export interface NasLibraryResponse {
  items: NasModelEntry[];
  total: number;
  types: string[];
  scanned_at: number;
  cache_hit: boolean;
}

/** 浏览 NAS 模型库（名称/大小/类型/修改日期） */
export async function getNasLibrary(params: {
  type?: string;
  q?: string;
  include_nsfw?: boolean;
  refresh?: boolean;
}): Promise<NasLibraryResponse> {
  const sp = new URLSearchParams();
  if (params.type) sp.set("type", params.type);
  if (params.q) sp.set("q", params.q);
  if (params.include_nsfw) sp.set("include_nsfw", "true");
  if (params.refresh) sp.set("refresh", "true");
  const qs = sp.toString();
  const resp = await fetchWithTimeout(
    `${MODELS_BASE}/library${qs ? `?${qs}` : ""}`,
    {},
    API_TIMEOUTS.taskCreate
  );
  if (!resp.ok) throw await extractError(resp, "加载模型库失败");
  return resp.json();
}

export interface CivitaiFile {
  name: string;
  size_kb: number;
  download_url: string;
  sha256: string | null;
  primary: boolean;
}

export interface CivitaiVersion {
  id: number;
  name: string;
  files: CivitaiFile[];
}

export interface CivitaiModel {
  id: number;
  name: string;
  type: string;
  nsfw: boolean;
  versions: CivitaiVersion[];
}

export interface CivitaiSearchResponse {
  items: CivitaiModel[];
  total: number;
}

/** 搜索 Civitai 模型（civitai.red 镜像，NSFW 由后端按设置过滤） */
export async function searchCivitaiModels(params: {
  q?: string;
  type?: string;
  limit?: number;
  include_nsfw?: boolean;
}): Promise<CivitaiSearchResponse> {
  const sp = new URLSearchParams();
  if (params.q) sp.set("q", params.q);
  if (params.type) sp.set("type", params.type);
  if (params.limit) sp.set("limit", String(params.limit));
  if (params.include_nsfw) sp.set("include_nsfw", "true");
  const qs = sp.toString();
  const resp = await fetchWithTimeout(
    `${MODELS_BASE}/search${qs ? `?${qs}` : ""}`,
    {},
    API_TIMEOUTS.taskCreate
  );
  if (!resp.ok) throw await extractError(resp, "Civitai 搜索失败");
  return resp.json();
}

export interface ModelDownloadRequest {
  download_url: string;
  filename: string;
  subdir: string;
  sha256?: string | null;
  nsfw?: boolean;
}

export type DownloadTaskStatus =
  | "pending"
  | "running"
  | "done"
  | "error"
  | "canceled";

export interface DownloadTask {
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

/** 启动后台模型下载（写入 NAS 子目录，可选 SHA256 校验） */
export async function startModelDownload(
  req: ModelDownloadRequest
): Promise<DownloadTask> {
  const resp = await fetchWithTimeout(
    `${MODELS_BASE}/download`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req),
    },
    API_TIMEOUTS.taskCreate
  );
  if (!resp.ok) throw await extractError(resp, "启动下载失败");
  return resp.json();
}

/** 全部下载任务（按创建时间倒序） */
export async function getDownloadTasks(): Promise<DownloadTask[]> {
  const resp = await fetchWithTimeout(
    `${MODELS_BASE}/downloads`,
    {},
    API_TIMEOUTS.taskCreate
  );
  if (!resp.ok) throw await extractError(resp, "加载下载任务失败");
  const body = await resp.json();
  return (body?.items ?? []) as DownloadTask[];
}

/** 取消下载任务 */
export async function cancelDownloadTask(taskId: string): Promise<void> {
  const resp = await fetchWithTimeout(
    `${MODELS_BASE}/downloads/${taskId}`,
    { method: "DELETE" },
    API_TIMEOUTS.taskCreate
  );
  if (!resp.ok) throw await extractError(resp, "取消下载失败");
}

export interface NsfwStatus {
  nsfw_enabled: boolean;
  has_pin: boolean;
}

/** NSFW 状态（开关 + 是否已设 PIN） */
export async function getNsfwStatus(): Promise<NsfwStatus> {
  const resp = await fetchWithTimeout(
    `${SETTINGS_BASE}/nsfw`,
    {},
    API_TIMEOUTS.taskCreate
  );
  if (!resp.ok) throw await extractError(resp, "读取 NSFW 状态失败");
  return resp.json();
}

/** 开启/关闭 NSFW（首次开启需 newPin 设置管理 PIN） */
export async function setNsfwEnabled(
  enabled: boolean,
  pin: string,
  newPin?: string
): Promise<NsfwStatus> {
  const resp = await fetchWithTimeout(
    `${SETTINGS_BASE}/nsfw`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled, pin, new_pin: newPin ?? null }),
    },
    API_TIMEOUTS.taskCreate
  );
  if (!resp.ok) throw await extractError(resp, "NSFW 设置失败");
  return resp.json();
}

/** 修改 NSFW 管理 PIN（需旧 PIN 验证） */
export async function changeNsfwPin(
  pin: string,
  newPin: string
): Promise<NsfwStatus> {
  const resp = await fetchWithTimeout(
    `${SETTINGS_BASE}/nsfw/pin`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pin, new_pin: newPin }),
    },
    API_TIMEOUTS.taskCreate
  );
  if (!resp.ok) throw await extractError(resp, "修改 PIN 失败");
  return resp.json();
}

/** 融合面板状态（GET /api/panel/status） */
export interface PanelStatus {
  backend: string;
  product: string;
  downloader_config_path: string;
  downloader_config_readable: boolean;
  models_json_path: string;
  models_json_readable: boolean;
  nas_model_roots?: { path: string; readable: boolean }[];
  nas_model_roots_error?: string | null;
  drama_backend?: {
    api: string;
    note: string;
  };
  dashbox: {
    web: string;
    api: string;
    note: string;
    web_listening?: boolean;
    api_listening?: boolean;
  };
}

/** 读取后端/下载器配置/DashBox 默认 URL 状态；不启动 Rust 二进制 */
export async function getPanelStatus(): Promise<PanelStatus> {
  const resp = await fetchWithTimeout(
    `${PANEL_BASE}/status`,
    {},
    API_TIMEOUTS.taskCreate
  );
  if (!resp.ok) throw await extractError(resp, "读取面板状态失败");
  return resp.json();
}

/* ------------------------------------------------------------------ */
/* M25.3 画布工作流模板库（/pipeline/templates）                        */
/* ------------------------------------------------------------------ */

/** 类型片叙事镜头模板条目（GET /pipeline/templates 的 templates 元素） */
export interface PipelineTemplateItem {
  id: string;
  title: string;
  category: string;
  tags: string[];
  summary: string;
  content: string;
}

/** 模板库列表响应 */
export interface PipelineTemplateListResponse {
  templates: PipelineTemplateItem[];
  total: number;
  categories: string[];
}

/** 获取模板库列表：可选 category 过滤（默认 genre_trope） */
export async function getPipelineTemplates(params?: {
  category?: string;
}): Promise<PipelineTemplateListResponse> {
  const sp = new URLSearchParams();
  if (params?.category) sp.set("category", params.category);
  const qs = sp.toString();
  const resp = await fetchWithTimeout(
    `${API_BASE}/pipeline/templates${qs ? `?${qs}` : ""}`,
    {},
    API_TIMEOUTS.taskCreate
  );
  if (!resp.ok) throw await extractError(resp, "加载模板库失败");
  return resp.json();
}
