// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
/**
 * Platform short-drama module (platform/ FastAPI, default :8100).
 *
 * Browser stays same-origin: DashBox web (:8080) nginx-proxies /api/drama/*
 * to DashBox API (:8780), which reverse-proxies to ST_DRAMA_API_URL.
 * Do not point the SPA at :8100 directly — CSP connect-src is 'self'.
 *
 * Script / keyframes / TTS / video / compose: POST
 * /api/drama/{script|storyboard|voice|video|edit}/generate_async then
 * poll GET /api/drama/pipeline/status/{task_id} (same progress_tracker).
 * Do not call /api/drama/pipeline/run from the studio node (full film).
 */

import type { R18SceneData } from "@/lib/queries/model-library";

export type DramaStudioEngine = "drama" | "r18";

export interface DramaHealth {
  status: string;
  version?: string;
  [key: string]: unknown;
}

export interface DramaCharacter {
  character_id: string;
  name: string;
  role?: string;
  age?: number | null;
  description?: string;
  personality?: string;
}

export interface DramaScene {
  scene_id: number;
  episode?: number;
  shot_type?: string;
  description?: string;
  prompt?: string;
  negative_prompt?: string;
  character_actions?: string;
  dialogue?: string;
  emotion?: string;
  duration_seconds?: number;
  camera_movement?: string;
}

export interface DramaScript {
  project_id?: string;
  title: string;
  genre?: string;
  aspect_ratio?: string;
  total_episodes?: number;
  characters: DramaCharacter[];
  scenes: DramaScene[];
}

export interface DramaStoryboard {
  scene_id: number;
  image_url: string;
  prompt_used?: string;
  preview_video_url?: string;
  is_sketch?: boolean;
  sketch_seed?: number | null;
}

export interface DramaAsyncTask {
  task_id: string;
  agent: string;
  status: string;
  poll_url?: string;
  stream_url?: string;
}

export interface DramaTaskStatus {
  task_id: string;
  status: "pending" | "running" | "completed" | "failed" | string;
  percent: number;
  message: string;
  error: string | null;
  result: unknown;
  updated_at?: number;
}

export function dramaApiPath(suffix: string): string {
  const cleaned = suffix.replace(/^\/+/, "");
  return cleaned ? `/api/drama/${cleaned}` : "/api/drama";
}

export function resolveStudioEngine(engine: DramaStudioEngine | undefined | null): DramaStudioEngine {
  return engine === "r18" ? "r18" : "drama";
}

/** Map platform /static/* (served on :8100) onto the same-origin drama proxy. */
export function rewriteDramaAssetUrl(url: string): string {
  const trimmed = (url || "").trim();
  if (!trimmed) return trimmed;
  if (trimmed.startsWith("/api/drama/")) return trimmed;
  if (trimmed.startsWith("/static/")) {
    return dramaApiPath(`static/${trimmed.slice("/static/".length)}`);
  }
  try {
    const parsed = new URL(trimmed);
    if (parsed.pathname.startsWith("/static/")) {
      return dramaApiPath(
        `static/${parsed.pathname.slice("/static/".length)}${parsed.search}`,
      );
    }
  } catch {
    // not an absolute URL
  }
  return trimmed;
}

/** Reverse same-origin /api/drama/static onto a URL the platform process can fetch. */
export function toDramaBackendAssetUrl(url: string): string {
  const trimmed = (url || "").trim();
  if (!trimmed) return trimmed;
  if (trimmed.startsWith("/api/drama/static/")) {
    const rest = trimmed.slice("/api/drama/static/".length);
    return `http://127.0.0.1:8100/static/${rest}`;
  }
  try {
    const parsed = new URL(trimmed);
    if (parsed.pathname.startsWith("/static/")) {
      return `http://127.0.0.1:8100${parsed.pathname}${parsed.search}`;
    }
  } catch {
    // not an absolute URL
  }
  return trimmed;
}

export interface DramaVoiceLine {
  text: string;
  character_name?: string;
  character_role?: string;
  character_age?: number | null;
  rate?: string;
}

export interface DramaVoiceResult {
  scene_id?: number;
  audio_urls?: Array<{ audio_url?: string; url?: string; [key: string]: unknown }>;
  total_lines?: number;
  audio_url?: string;
}

export interface DramaVideoResult {
  scene_id?: number;
  video_url: string;
  duration_seconds?: number;
}

export interface DramaEditSegment {
  scene_id: number;
  video_url: string;
  audio_url?: string;
  subtitle_url?: string;
  duration_seconds?: number;
}

export interface DramaEditResult {
  project_id?: string;
  title?: string;
  final_video_url: string;
  duration_seconds?: number;
  segments_count?: number;
}

export function firstDramaAudioUrl(
  result: DramaVoiceResult | null | undefined,
): string {
  const row = result?.audio_urls?.[0];
  const raw = (row?.audio_url || row?.url || result?.audio_url || "").trim();
  return rewriteDramaAssetUrl(raw);
}

export function scenesPerEpisodeForDuration(durationSec: number): number {
  return Math.min(30, Math.max(3, Math.round((durationSec || 90) / 8)));
}

export function mapDramaScenesToR18(scenes: DramaScene[]): R18SceneData[] {
  return (scenes ?? []).map((scene) => {
    const dialogue = (scene.dialogue || "").trim();
    const description = (scene.description || "").trim();
    const prompt = (scene.prompt || "").trim();
    return {
      scene_no: scene.scene_id,
      kind: "plot",
      title: (description || `镜头 ${scene.scene_id}`).slice(0, 40),
      shot_description: description,
      image_prompt: prompt || description,
      video_prompt: (scene.character_actions || scene.camera_movement || "").trim(),
      preset_id: "",
      dialogue,
      narration: "",
      duration_sec: scene.duration_seconds || 5,
      audio: dialogue ? "tts" : "none",
      emotion: scene.emotion || "",
      shot_size: scene.shot_type || "",
      camera_move: scene.camera_movement || "",
      action_desc: scene.character_actions || "",
      scene_desc: description,
    };
  });
}

async function dramaFetchJson<T>(suffix: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(dramaApiPath(suffix), {
    credentials: "include",
    cache: "no-store",
    ...init,
  });
  if (!response.ok) {
    let detail = `drama ${suffix} HTTP ${response.status}`;
    try {
      const body = (await response.json()) as { detail?: unknown; error?: string };
      if (typeof body.detail === "string" && body.detail) detail = body.detail;
      else if (typeof body.error === "string" && body.error) detail = body.error;
    } catch {
      // keep status text
    }
    throw new Error(detail);
  }
  return (await response.json()) as T;
}

export async function getDramaHealth(signal?: AbortSignal): Promise<DramaHealth> {
  return dramaFetchJson<DramaHealth>("health", { signal });
}

export async function startDramaScriptAsync(
  payload: {
    premise: string;
    genre?: string;
    style?: string;
    episodes?: number;
    scenes_per_episode?: number;
  },
  signal?: AbortSignal,
): Promise<DramaAsyncTask> {
  return dramaFetchJson<DramaAsyncTask>("script/generate_async", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      premise: payload.premise,
      genre: payload.genre || "都市悬疑",
      style: payload.style || "写实电影感",
      episodes: payload.episodes ?? 1,
      scenes_per_episode: payload.scenes_per_episode ?? 5,
    }),
    signal,
  });
}

export async function startDramaStoryboardAsync(
  payload: {
    scene: DramaScene;
    characters: DramaCharacter[];
    style?: string;
    sketch_mode?: boolean;
  },
  signal?: AbortSignal,
): Promise<DramaAsyncTask> {
  return dramaFetchJson<DramaAsyncTask>("storyboard/generate_async", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      scene: payload.scene,
      characters: payload.characters ?? [],
      style: payload.style || "写实电影感",
      sketch_mode: payload.sketch_mode !== false,
    }),
    signal,
  });
}

export async function getDramaTaskStatus(
  taskId: string,
  signal?: AbortSignal,
): Promise<DramaTaskStatus> {
  const raw = await dramaFetchJson<{ success?: boolean; data?: DramaTaskStatus } & DramaTaskStatus>(
    `pipeline/status/${encodeURIComponent(taskId)}`,
    { signal },
  );
  if (raw && typeof raw === "object" && raw.data && typeof raw.data === "object") {
    return raw.data;
  }
  return raw;
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, ms);
  });
}

export async function waitDramaTask<T>(
  taskId: string,
  opts?: { intervalMs?: number; timeoutMs?: number; signal?: AbortSignal },
): Promise<T> {
  const interval = opts?.intervalMs ?? 3000;
  const timeout = opts?.timeoutMs ?? 1_200_000;
  const started = Date.now();
  for (;;) {
    if (opts?.signal?.aborted) throw new Error("drama task aborted");
    const status = await getDramaTaskStatus(taskId, opts?.signal);
    if (status.status === "completed") return status.result as T;
    if (status.status === "failed") {
      throw new Error(status.error || status.message || "drama task failed");
    }
    if (Date.now() - started >= timeout) {
      throw new Error(`drama task timeout (${Math.round(timeout / 1000)}s)`);
    }
    await sleep(interval);
  }
}

export async function generateDramaScript(
  payload: {
    premise: string;
    genre?: string;
    style?: string;
    episodes?: number;
    scenes_per_episode?: number;
  },
  opts?: { signal?: AbortSignal; intervalMs?: number; timeoutMs?: number },
): Promise<DramaScript> {
  const task = await startDramaScriptAsync(payload, opts?.signal);
  if (!task.task_id) throw new Error("短剧模块未返回 task_id");
  const result = await waitDramaTask<DramaScript>(task.task_id, opts);
  if (!result || !Array.isArray(result.scenes)) {
    throw new Error("短剧模块剧本结果无效");
  }
  return result;
}

export async function generateDramaStoryboard(
  payload: {
    scene: DramaScene;
    characters: DramaCharacter[];
    style?: string;
    sketch_mode?: boolean;
  },
  opts?: { signal?: AbortSignal; intervalMs?: number; timeoutMs?: number },
): Promise<DramaStoryboard> {
  const task = await startDramaStoryboardAsync(payload, opts?.signal);
  if (!task.task_id) throw new Error("短剧分镜未返回 task_id");
  const result = await waitDramaTask<DramaStoryboard>(task.task_id, {
    ...opts,
    timeoutMs: opts?.timeoutMs ?? 600_000,
  });
  if (!result || !result.image_url) {
    throw new Error("短剧分镜结果无效");
  }
  return { ...result, image_url: rewriteDramaAssetUrl(result.image_url) };
}

export async function startDramaAgentAsync(
  agent: "voice" | "video" | "edit",
  payload: unknown,
  signal?: AbortSignal,
): Promise<DramaAsyncTask> {
  return dramaFetchJson<DramaAsyncTask>(`${agent}/generate_async`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload ?? {}),
    signal,
  });
}

export async function generateDramaVoice(
  payload: { scene_id: number; dialogues: DramaVoiceLine[] },
  opts?: { signal?: AbortSignal; intervalMs?: number; timeoutMs?: number },
): Promise<DramaVoiceResult & { audio_url: string }> {
  const task = await startDramaAgentAsync("voice", payload, opts?.signal);
  if (!task.task_id) throw new Error("短剧配音未返回 task_id");
  const result = await waitDramaTask<DramaVoiceResult>(task.task_id, {
    ...opts,
    timeoutMs: opts?.timeoutMs ?? 180_000,
  });
  const audio_url = firstDramaAudioUrl(result);
  if (!audio_url) throw new Error("短剧配音结果无效");
  return { ...result, audio_url };
}

export async function generateDramaVideo(
  payload: {
    scene_id: number;
    image_url: string;
    prompt?: string;
    negative_prompt?: string;
    duration_seconds?: number;
    reference_images?: string[];
  },
  opts?: { signal?: AbortSignal; intervalMs?: number; timeoutMs?: number },
): Promise<DramaVideoResult> {
  const task = await startDramaAgentAsync(
    "video",
    {
      scene_id: payload.scene_id,
      image_url: toDramaBackendAssetUrl(payload.image_url),
      prompt: payload.prompt || "",
      negative_prompt: payload.negative_prompt || "",
      duration_seconds: Math.min(10, Math.max(1, payload.duration_seconds ?? 3)),
      reference_images: payload.reference_images ?? [],
    },
    opts?.signal,
  );
  if (!task.task_id) throw new Error("短剧出片未返回 task_id");
  const result = await waitDramaTask<DramaVideoResult>(task.task_id, {
    ...opts,
    timeoutMs: opts?.timeoutMs ?? 1_200_000,
  });
  if (!result || !result.video_url) throw new Error("短剧出片结果无效");
  return { ...result, video_url: rewriteDramaAssetUrl(result.video_url) };
}

export async function generateDramaCompose(
  payload: {
    project_id?: string;
    title?: string;
    segments: DramaEditSegment[];
    transition?: string;
  },
  opts?: { signal?: AbortSignal; intervalMs?: number; timeoutMs?: number },
): Promise<DramaEditResult> {
  const task = await startDramaAgentAsync(
    "edit",
    {
      project_id: payload.project_id || "",
      title: payload.title || "",
      transition: payload.transition || "none",
      segments: (payload.segments ?? []).map((seg) => ({
        scene_id: seg.scene_id,
        video_url: toDramaBackendAssetUrl(seg.video_url),
        audio_url: toDramaBackendAssetUrl(seg.audio_url || ""),
        subtitle_url: toDramaBackendAssetUrl(seg.subtitle_url || ""),
        duration_seconds: seg.duration_seconds ?? 5,
      })),
    },
    opts?.signal,
  );
  if (!task.task_id) throw new Error("短剧合成未返回 task_id");
  const result = await waitDramaTask<DramaEditResult>(task.task_id, {
    ...opts,
    timeoutMs: opts?.timeoutMs ?? 600_000,
  });
  if (!result || !result.final_video_url) throw new Error("短剧合成结果无效");
  return { ...result, final_video_url: rewriteDramaAssetUrl(result.final_video_url) };
}

/** Cheap ping: empty body must 422 without starting a generate job. */
export async function pingDramaScriptAsync(signal?: AbortSignal): Promise<number> {
  const response = await fetch(dramaApiPath("script/generate_async"), {
    method: "POST",
    credentials: "include",
    cache: "no-store",
    headers: { "Content-Type": "application/json" },
    body: "{}",
    signal,
  });
  return response.status;
}

/** Cheap ping: empty body must 422 without starting a video/H3 job. */
export async function pingDramaVideoAsync(signal?: AbortSignal): Promise<number> {
  const response = await fetch(dramaApiPath("video/generate_async"), {
    method: "POST",
    credentials: "include",
    cache: "no-store",
    headers: { "Content-Type": "application/json" },
    body: "{}",
    signal,
  });
  return response.status;
}

/** Cheap ping: empty body must 422 without starting TTS. */
export async function pingDramaVoiceAsync(signal?: AbortSignal): Promise<number> {
  const response = await fetch(dramaApiPath("voice/generate_async"), {
    method: "POST",
    credentials: "include",
    cache: "no-store",
    headers: { "Content-Type": "application/json" },
    body: "{}",
    signal,
  });
  return response.status;
}
