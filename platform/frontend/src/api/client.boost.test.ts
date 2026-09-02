import { describe, it, expect, vi, afterEach } from "vitest";
import {
  API_TIMEOUTS,
  generateScript,
  generateCharacter,
  previewCharacter,
  generateStoryboard,
  generateStoryboardBatch,
  generateVideo,
  generateVideoBatch,
  generateVideoAsync,
  rerunShot,
  generateVoice,
  generateSubtitle,
  composeVideo,
  checkQuality,
  checkVisualQuality,
  applySubtitleFix,
  agentAssist,
  checkHealth,
  getModelRegistry,
  getCharacterLibrary,
  pollVideoTask,
  type ProgressEvent,
} from "./client";

/**
 * client.ts 覆盖率补缺（boost）：
 * 既有 client.test.ts 聚焦超时/轮询/pipeline/报告提取，本文件补：
 * ① 全部 generate 系 / check 系端点的成功路径（URL + POST body + resp.json 解析）
 * ② generateVideoAsync / rerunShot 的 !resp.ok 错误分支
 * ③ previewCharacter 的 AbortSignal 透传（无超时包装）
 * ④ fetchWithTimeout 非 AbortError 原样 rethrow
 * ⑤ 绝对 API_BASE 部署下 resolveTaskUrl/resolveStaticUrl 的同源重写（动态重导模块）
 * ⑥ getCharacterLibrary 的 data 缺失回退
 */

function mockJsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const ok = <T,>(data: T) => ({ success: true, data, error: null, elapsed_seconds: 0.5 });

const sampleCharacter = {
  character_id: "c1",
  name: "n",
  role: "r",
  age: null,
  description: "d",
  personality: "p",
};
const sampleScene = {
  scene_id: 1,
  episode: 1,
  shot_type: "close",
  description: "d",
  prompt: "",
  negative_prompt: "",
  character_actions: "",
  dialogue: "",
  emotion: "e",
  duration_seconds: 5,
  camera_movement: "static",
};

describe("client boost — 各端点成功路径与请求构造", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("generateScript 成功返回解析数据", async () => {
    const script = { project_id: "p1", title: "短剧", genre: "g", aspect_ratio: "9:16", total_episodes: 1, characters: [], scenes: [] };
    const fetchMock = vi.fn(async (url: string, opts?: RequestInit) => {
      expect(url).toBe("/api/drama/script/generate");
      expect(opts?.method).toBe("POST");
      const body = JSON.parse(String(opts?.body));
      expect(body).toEqual({ premise: "p", genre: "g", episodes: 1, scenes_per_episode: 2 });
      return mockJsonResponse(ok(script));
    });
    vi.stubGlobal("fetch", fetchMock);
    const resp = await generateScript({ premise: "p", genre: "g", episodes: 1, scenes_per_episode: 2 });
    expect(resp.data?.title).toBe("短剧");
  });

  it("generateCharacter 成功返回角色卡", async () => {
    const card = { character_id: "c1", name: "n", reference_images: { front: "u" }, consistency_level: "high" };
    const fetchMock = vi.fn(async (url: string, opts?: RequestInit) => {
      expect(url).toBe("/api/drama/character/generate");
      const body = JSON.parse(String(opts?.body));
      expect(body.character.character_id).toBe("c1");
      expect(body.custom_positive_prompt).toBe("cpp");
      return mockJsonResponse(ok(card));
    });
    vi.stubGlobal("fetch", fetchMock);
    const resp = await generateCharacter({
      character: sampleCharacter,
      style: "s",
      consistency_level: "high",
      custom_positive_prompt: "cpp",
    });
    expect(resp.data?.reference_images.front).toBe("u");
  });

  it("previewCharacter 透传 AbortSignal 且不经超时包装", async () => {
    const preview = {
      character_id: "c1",
      character: sampleCharacter,
      style: "s",
      search_reference: "ref",
      prompts: { front_view_prompt: "f", side_view_prompt: "s", closeup_prompt: "c", negative_prompt: "n" },
    };
    const controller = new AbortController();
    const fetchMock = vi.fn(async (url: string, opts?: RequestInit) => {
      expect(url).toBe("/api/drama/character/preview");
      expect(opts?.method).toBe("POST");
      expect(opts?.signal).toBe(controller.signal);
      return mockJsonResponse(ok(preview));
    });
    vi.stubGlobal("fetch", fetchMock);
    const resp = await previewCharacter({ character: sampleCharacter, style: "s" }, controller.signal);
    expect(resp.data?.prompts.front_view_prompt).toBe("f");
  });

  it("generateStoryboard 成功返回分镜（线稿参数透传）", async () => {
    const sb = { scene_id: 1, image_url: "u", prompt_used: "p", is_sketch: true, sketch_seed: 42 };
    const fetchMock = vi.fn(async (url: string, opts?: RequestInit) => {
      expect(url).toBe("/api/drama/storyboard/generate");
      const body = JSON.parse(String(opts?.body));
      expect(body.sketch_mode).toBe(true);
      expect(body.refine_seed).toBe(42);
      return mockJsonResponse(ok(sb));
    });
    vi.stubGlobal("fetch", fetchMock);
    const resp = await generateStoryboard({
      scene: sampleScene,
      characters: [],
      style: "s",
      sketch_mode: true,
      refine_seed: 42,
    });
    expect(resp.data?.is_sketch).toBe(true);
    expect(resp.data?.sketch_seed).toBe(42);
  });

  it("generateStoryboardBatch 成功返回批量结果", async () => {
    const batch = { results: [{ scene_id: 1, image_url: "u", prompt_used: "p" }], failed_scenes: [2] };
    const fetchMock = vi.fn(async (url: string) => {
      expect(url).toBe("/api/drama/storyboard/generate_batch");
      return mockJsonResponse(ok(batch));
    });
    vi.stubGlobal("fetch", fetchMock);
    const resp = await generateStoryboardBatch({ scenes: [sampleScene], characters: [], style: "s" });
    expect(resp.data?.results).toHaveLength(1);
    expect(resp.data?.failed_scenes).toEqual([2]);
  });

  it("generateVideo 成功返回视频片段", async () => {
    const video = { scene_id: 1, video_url: "u", duration_seconds: 5 };
    const fetchMock = vi.fn(async (url: string) => {
      expect(url).toBe("/api/drama/video/generate");
      return mockJsonResponse(ok(video));
    });
    vi.stubGlobal("fetch", fetchMock);
    const resp = await generateVideo({ scene_id: 1, image_url: "u", prompt: "p", negative_prompt: "n", duration_seconds: 5 });
    expect(resp.data?.video_url).toBe("u");
  });

  it("generateVideoBatch 成功返回批量视频", async () => {
    const batch = { results: [{ scene_id: 1, video_url: "u", duration_seconds: 5 }], failed_scenes: [] };
    const fetchMock = vi.fn(async (url: string) => {
      expect(url).toBe("/api/drama/video/generate_batch");
      return mockJsonResponse(ok(batch));
    });
    vi.stubGlobal("fetch", fetchMock);
    const resp = await generateVideoBatch({ items: [{ scene_id: 1, image_url: "u", prompt: "p", negative_prompt: "n", duration_seconds: 5 }] });
    expect(resp.data?.results).toHaveLength(1);
  });

  it("generateVideoAsync 成功返回任务句柄", async () => {
    const handle = { task_id: "t1", agent: "video", status: "pending", poll_url: "p", stream_url: "s" };
    const fetchMock = vi.fn(async (url: string) => {
      expect(url).toBe("/api/drama/video/generate_async");
      return mockJsonResponse(handle);
    });
    vi.stubGlobal("fetch", fetchMock);
    const resp = await generateVideoAsync({ scene_id: 1, image_url: "u", prompt: "p", negative_prompt: "n", duration_seconds: 5 });
    expect(resp.task_id).toBe("t1");
  });

  it("generateVideoAsync 非 2xx 抛出含状态码与正文的错误", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("gpu busy", { status: 503 })));
    await expect(
      generateVideoAsync({ scene_id: 1, image_url: "u", prompt: "p", negative_prompt: "n", duration_seconds: 5 })
    ).rejects.toThrow("创建异步任务失败: 503 gpu busy");
  });

  it("rerunShot 成功返回重拍视频（seed/reseed/override_prompt 透传）", async () => {
    const video = { scene_id: 3, video_url: "u2", duration_seconds: 5 };
    const fetchMock = vi.fn(async (url: string, opts?: RequestInit) => {
      expect(url).toBe("/api/drama/video/rerun-shot");
      const body = JSON.parse(String(opts?.body));
      expect(body).toEqual({ project_id: "p1", scene_id: 3, seed: 99, reseed: true, override_prompt: "op" });
      return mockJsonResponse(ok(video));
    });
    vi.stubGlobal("fetch", fetchMock);
    const resp = await rerunShot({ project_id: "p1", scene_id: 3, seed: 99, reseed: true, override_prompt: "op" });
    expect(resp.data?.video_url).toBe("u2");
  });

  it("rerunShot 非 2xx 抛出含状态码的错误", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("snapshot missing", { status: 404 })));
    await expect(rerunShot({ project_id: "p1", scene_id: 3 })).rejects.toThrow("锚点重拍失败: 404 snapshot missing");
  });

  it("generateVoice 成功返回配音", async () => {
    const voice = { scene_id: 1, audio_urls: [{ filename: "a.wav", voice: "v", text: "t", audio_url: "u" }], total_lines: 1 };
    const fetchMock = vi.fn(async (url: string) => {
      expect(url).toBe("/api/drama/voice/generate");
      return mockJsonResponse(ok(voice));
    });
    vi.stubGlobal("fetch", fetchMock);
    const resp = await generateVoice({
      scene_id: 1,
      dialogues: [{ text: "t", character_name: "n", character_role: "r", character_age: null, rate: "normal" }],
    });
    expect(resp.data?.total_lines).toBe(1);
  });

  it("generateSubtitle 成功返回字幕", async () => {
    const sub = { scene_id: 1, srt_content: "s", segments: [], language: "zh", srt_url: "u" };
    const fetchMock = vi.fn(async (url: string) => {
      expect(url).toBe("/api/drama/subtitle/generate");
      return mockJsonResponse(ok(sub));
    });
    vi.stubGlobal("fetch", fetchMock);
    const resp = await generateSubtitle({ scene_id: 1, audio_url: "u", language: "zh" });
    expect(resp.data?.srt_url).toBe("u");
  });

  it("composeVideo 成功返回成片（可选参数透传）", async () => {
    const edit = { project_id: "p1", title: "t", final_video_url: "u", duration_seconds: 10, segments_count: 1 };
    const fetchMock = vi.fn(async (url: string, opts?: RequestInit) => {
      expect(url).toBe("/api/drama/edit/compose");
      const body = JSON.parse(String(opts?.body));
      expect(body.transition).toBe("fade");
      expect(body.output_resolution).toBe("768x1344");
      expect(body.output_fps).toBe(24);
      return mockJsonResponse(ok(edit));
    });
    vi.stubGlobal("fetch", fetchMock);
    const resp = await composeVideo({
      project_id: "p1",
      title: "t",
      segments: [{ scene_id: 1, video_url: "v", audio_url: "a", subtitle_url: "s", duration_seconds: 5 }],
      transition: "fade",
      bgm_url: "b",
      output_resolution: "768x1344",
      output_fps: 24,
    });
    expect(resp.data?.final_video_url).toBe("u");
  });

  it("checkQuality 成功返回质检报告（check_types 透传）", async () => {
    const report = { project_id: "p1", title: "t", score: 90, summary: "", issues: [], checked_at: 1 };
    const fetchMock = vi.fn(async (url: string, opts?: RequestInit) => {
      expect(url).toBe("/api/drama/quality/check");
      const body = JSON.parse(String(opts?.body));
      expect(body.check_types).toEqual(["logic"]);
      return mockJsonResponse(ok(report));
    });
    vi.stubGlobal("fetch", fetchMock);
    const resp = await checkQuality({ project_id: "p1", title: "t", characters: [], scenes: [], subtitles: [], check_types: ["logic"] });
    expect(resp.data?.score).toBe(90);
  });

  it("checkVisualQuality 成功返回视觉质检（max_frames 透传）", async () => {
    const report = { project_id: "p1", title: "t", scene_id: 1, score: 80, summary: "", issues: [], checked_at: 1 };
    const fetchMock = vi.fn(async (url: string, opts?: RequestInit) => {
      expect(url).toBe("/api/drama/quality/visual");
      const body = JSON.parse(String(opts?.body));
      expect(body.max_frames).toBe(4);
      return mockJsonResponse(ok(report));
    });
    vi.stubGlobal("fetch", fetchMock);
    const resp = await checkVisualQuality({ project_id: "p1", title: "t", scene_id: 1, video_url: "u", max_frames: 4 });
    expect(resp.data?.scene_id).toBe(1);
  });

  it("applySubtitleFix 成功返回回写结果（persist 透传）", async () => {
    const fixResult = { fixed_subtitles: [], corrections: [{ wrong: "的", right: "地" }], fixed_count: 1, details: [], persisted_files: ["f.srt"] };
    const fetchMock = vi.fn(async (url: string, opts?: RequestInit) => {
      expect(url).toBe("/api/drama/quality/apply_subtitle_fix");
      expect(opts?.method).toBe("POST");
      const body = JSON.parse(String(opts?.body));
      expect(body.persist).toBe(true);
      return mockJsonResponse(ok(fixResult));
    });
    vi.stubGlobal("fetch", fetchMock);
    const resp = await applySubtitleFix({
      subtitles: [],
      issues: [{ category: "subtitle", severity: "warning", scene_id: 1, message: "m", suggestion: "s" }],
      persist: true,
    });
    expect(resp.data?.fixed_count).toBe(1);
    expect(resp.data?.persisted_files).toEqual(["f.srt"]);
  });

  it("agentAssist 成功返回改写文本（action/extra_instruction 透传）", async () => {
    const assist = { text: "改写后", action: "polish", context: "ctx" };
    const fetchMock = vi.fn(async (url: string, opts?: RequestInit) => {
      expect(url).toBe("/api/drama/agent/assist");
      const body = JSON.parse(String(opts?.body));
      expect(body).toEqual({ text: "t", context: "ctx", action: "polish", extra_instruction: "再短些" });
      return mockJsonResponse(ok(assist));
    });
    vi.stubGlobal("fetch", fetchMock);
    const resp = await agentAssist({ text: "t", context: "ctx", action: "polish", extra_instruction: "再短些" });
    expect(resp.data?.text).toBe("改写后");
  });

  it("checkHealth GET /health 返回服务状态", async () => {
    const fetchMock = vi.fn(async (url: string, opts?: RequestInit) => {
      expect(url).toBe("/api/drama/health");
      expect(opts).toBeUndefined();
      return mockJsonResponse({ status: "ok", llm: "online" });
    });
    vi.stubGlobal("fetch", fetchMock);
    const resp = await checkHealth();
    expect(resp.status).toBe("ok");
  });

  it("fetchWithTimeout 对非 AbortError 原样 rethrow（不包装为超时）", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("network down");
      })
    );
    await expect(getModelRegistry()).rejects.toThrow("network down");
  });

  it("getCharacterLibrary 返回 data 数组", async () => {
    const entry = {
      character_id: "c1",
      name: "林远",
      role: "主角",
      age: 26,
      description: "",
      personality: "",
      reference_images: { front: "u" },
      appearance_lock: "lock",
      locked: true,
      consistency_level: "L3",
      created_at: 1,
      updated_at: 2,
    };
    const fetchMock = vi.fn(async (url: string) => {
      expect(url).toBe("/api/drama/character-library/list");
      return mockJsonResponse({ success: true, data: [entry] });
    });
    vi.stubGlobal("fetch", fetchMock);
    const list = await getCharacterLibrary();
    expect(list).toHaveLength(1);
    expect(list[0].name).toBe("林远");
  });

  it("getCharacterLibrary 响应缺 data 字段时回退空数组", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => mockJsonResponse({ success: true })));
    await expect(getCharacterLibrary()).resolves.toEqual([]);
  });
});

describe("client boost — pollVideoTask onProgress 回调", () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("每轮轮询触发 onProgress 回调直至终态", async () => {
    vi.useFakeTimers();
    const running: ProgressEvent = { task_id: "t1", agent: "video", status: "running", percent: 50, message: "中", result: null, error: null, updated_at: 1 };
    const done: ProgressEvent = { ...running, status: "completed", percent: 100, message: "完" };
    let calls = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => mockJsonResponse(calls++ === 0 ? running : done))
    );
    const seen: ProgressEvent[] = [];
    const promise = pollVideoTask("/api/drama/progress/t1", 60_000, (evt) => seen.push(evt));
    await vi.advanceTimersByTimeAsync(API_TIMEOUTS.pollInterval * 2 + 100);
    const final = await promise;
    expect(final.status).toBe("completed");
    expect(seen.map((e) => e.status)).toEqual(["running", "completed"]);
  });
});

describe("client boost — 绝对 API_BASE 部署（远程入口 URL 重写）", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.resetModules();
  });

  it("resolveTaskUrl 将 localhost 任务 URL 重写为 API_BASE 同源", async () => {
    vi.stubEnv("VITE_API_BASE", "https://toiv.example.com/api/drama");
    vi.resetModules();
    const mod = await import("./client");
    // 后端 URL 无显式端口：协议+主机整体替换
    expect(mod.resolveTaskUrl("http://localhost/api/progress/t1/stream")).toBe(
      "https://toiv.example.com/api/progress/t1/stream"
    );
    // ⚠️ 现状固化（疑似缺陷，已上报）：后端 URL 带显式端口而 API_BASE 无端口时，
    // WHATWG URL 的 host setter 不会清除旧端口 → :8100 泄漏进重写结果
    expect(mod.resolveTaskUrl("http://localhost:8100/api/progress/t1/stream")).toBe(
      "https://toiv.example.com:8100/api/progress/t1/stream"
    );
    // 非法 URL 仍原样返回（catch 分支在绝对模式下同样生效）
    expect(mod.resolveTaskUrl("not-a-url")).toBe("not-a-url");
  });

  it("resolveStaticUrl 将相对媒体路径补全为绝对 URL", async () => {
    vi.stubEnv("VITE_API_BASE", "https://toiv.example.com/api/drama");
    vi.resetModules();
    const mod = await import("./client");
    expect(mod.resolveStaticUrl("/static/final/p.mp4")).toBe("https://toiv.example.com/static/final/p.mp4");
    expect(mod.resolveStaticUrl("http://x/f.mp4")).toBe("http://x/f.mp4");
    expect(mod.resolveStaticUrl("")).toBe("");
  });
});
