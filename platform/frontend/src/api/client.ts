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
  lipSync: 600_000, // 唇形同步
  postprocess: 1_800_000, // 后处理 5 步管线
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

export async function generateVideo(params: {
  scene_id: number;
  image_url: string;
  prompt: string;
  negative_prompt: string;
  duration_seconds: number;
}): Promise<AgentResponse<VideoData>> {
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
  items: Array<{
    scene_id: number;
    image_url: string;
    prompt: string;
    negative_prompt: string;
    duration_seconds: number;
  }>;
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

export async function generateVideoAsync(params: {
  scene_id: number;
  image_url: string;
  prompt: string;
  negative_prompt: string;
  duration_seconds: number;
}): Promise<AsyncTaskResponse> {
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

// ============================================================================
// P4.4 唇形同步 + 后处理编排（LatentSync 1.6 + RealBasicVSR/RIFE/ProPainter/
//                          DeepFilterNet3 + Mac FFmpeg H.265）
// ============================================================================

/** 唇形同步结果（LatentSync 1.6） */
export interface LipSyncData {
  scene_id: number;
  video_url: string;
  original_video_url: string;
  /** false 表示已降级返回原视频 */
  synced: boolean;
  elapsed_seconds: number;
}

/** 后处理步骤枚举（与后端 PostprocessStep 对齐） */
export type PostprocessStep =
  | "super_resolution"
  | "frame_interpolation"
  | "inpainting"
  | "audio_denoise"
  | "final_encode";

/** 单步后处理结果 */
export interface PostprocessStepResult {
  step: PostprocessStep;
  success: boolean;
  output_url: string;
  elapsed_seconds: number;
  message: string;
  skipped: boolean;
}

/** 后处理编排结果（5 步管线） */
export interface PostprocessData {
  scene_id: number;
  final_video_url: string;
  original_video_url: string;
  steps: PostprocessStepResult[];
  success: boolean;
  elapsed_seconds: number;
}

/**
 * 唇形同步（LatentSync 1.6）：将视频人物口型与配音音频对齐。
 * 后端受 settings.lip_sync_enabled 总开关控制，失败自动降级返回原视频。
 */
export async function generateLipSync(params: {
  scene_id: number;
  video_url: string;
  audio_url: string;
  reference_image_url?: string | null;
}): Promise<AgentResponse<LipSyncData>> {
  const resp = await fetchWithTimeout(
    `${API_BASE}/lipsync/generate`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(params),
    },
    API_TIMEOUTS.lipSync
  );
  return resp.json();
}

/**
 * 后处理编排（P4.4）：编排 超分 → 插帧 → 修复 → 降噪 → H.265 编码。
 * 单步失败不阻断整体流程（best-effort），仅 final_encode 失败会回退 H.264。
 * 后端受 settings.postprocess_enabled 总开关 + 各步骤独立开关控制。
 */
export async function generatePostprocess(params: {
  scene_id: number;
  video_url: string;
  audio_url?: string | null;
  steps?: PostprocessStep[];
  output_resolution?: string | null;
}): Promise<AgentResponse<PostprocessData>> {
  const resp = await fetchWithTimeout(
    `${API_BASE}/postprocess/generate`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(params),
    },
    API_TIMEOUTS.postprocess
  );
  return resp.json();
}

/**
 * 视频异步任务轮询：每 3 秒查询一次，直到完成/失败或超过最大等待时间。
 * 单次请求带 taskCreate 超时，整体带截止期限，避免任务卡死时前端永久轮询。
 */
export async function pollVideoTask(
  pollUrl: string,
  maxWaitMs: number = API_TIMEOUTS.video
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
