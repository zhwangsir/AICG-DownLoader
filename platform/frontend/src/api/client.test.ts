import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  API_TIMEOUTS,
  generateScript,
  generateCharacter,
  generateStoryboard,
  generateStoryboardBatch,
  generateVideo,
  generateVideoBatch,
  generateVideoAsync,
  generateVoice,
  generateSubtitle,
  composeVideo,
  checkQuality,
  checkVisualQuality,
  generateLipSync,
  generatePostprocess,
  pollVideoTask,
  type ProgressEvent,
} from "./client";

/** mock 一个永不响应的 fetch，直到调用方 abort（模拟后端阻塞场景） */
function mockHangingFetch() {
  return vi.fn((_url: string, opts?: RequestInit) =>
    new Promise<Response>((_resolve, reject) => {
      opts?.signal?.addEventListener("abort", () => {
        reject(new DOMException("The operation was aborted.", "AbortError"));
      });
    })
  );
}

function mockJsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const RUNNING_EVT: ProgressEvent = {
  task_id: "t1",
  agent: "video",
  status: "running",
  percent: 50,
  message: "生成中",
  result: null,
  error: null,
  updated_at: 1,
};

describe("API 端点超时保护（F2 同类缺陷防回归）", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  // [名称, 调用, 预期超时毫秒]
  const cases: Array<[string, () => Promise<unknown>, number]> = [
    [
      "generateScript",
      () =>
        generateScript({
          premise: "p",
          genre: "g",
          episodes: 1,
          scenes_per_episode: 1,
        }),
      API_TIMEOUTS.script,
    ],
    [
      "generateCharacter",
      () =>
        generateCharacter({
          character: {
            character_id: "c1",
            name: "n",
            role: "r",
            age: null,
            description: "d",
            personality: "p",
          },
          style: "s",
          consistency_level: "high",
        }),
      API_TIMEOUTS.character,
    ],
    [
      "generateStoryboard",
      () =>
        generateStoryboard({
          scene: {
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
          },
          characters: [],
          style: "s",
        }),
      API_TIMEOUTS.storyboard,
    ],
    [
      "generateStoryboardBatch",
      () =>
        generateStoryboardBatch({ scenes: [], characters: [], style: "s" }),
      API_TIMEOUTS.storyboardBatch,
    ],
    [
      "generateVideo",
      () =>
        generateVideo({
          scene_id: 1,
          image_url: "u",
          prompt: "p",
          negative_prompt: "n",
          duration_seconds: 5,
        }),
      API_TIMEOUTS.video,
    ],
    [
      "generateVideoBatch",
      () => generateVideoBatch({ items: [] }),
      API_TIMEOUTS.videoBatch,
    ],
    [
      "generateVideoAsync",
      () =>
        generateVideoAsync({
          scene_id: 1,
          image_url: "u",
          prompt: "p",
          negative_prompt: "n",
          duration_seconds: 5,
        }),
      API_TIMEOUTS.taskCreate,
    ],
    [
      "generateVoice",
      () => generateVoice({ scene_id: 1, dialogues: [] }),
      API_TIMEOUTS.voice,
    ],
    [
      "generateSubtitle",
      () => generateSubtitle({ scene_id: 1, audio_url: "u", language: "zh" }),
      API_TIMEOUTS.subtitle,
    ],
    [
      "composeVideo",
      () => composeVideo({ project_id: "p", title: "t", segments: [] }),
      API_TIMEOUTS.compose,
    ],
    [
      "checkQuality",
      () =>
        checkQuality({
          project_id: "p",
          title: "t",
          characters: [],
          scenes: [],
          subtitles: [],
        }),
      API_TIMEOUTS.quality,
    ],
    [
      "checkVisualQuality",
      () =>
        checkVisualQuality({
          project_id: "p",
          title: "t",
          scene_id: 1,
          video_url: "u",
        }),
      API_TIMEOUTS.visualQuality,
    ],
    [
      "generateLipSync",
      () => generateLipSync({ scene_id: 1, video_url: "v", audio_url: "a" }),
      API_TIMEOUTS.lipSync,
    ],
    [
      "generatePostprocess",
      () => generatePostprocess({ scene_id: 1, video_url: "v" }),
      API_TIMEOUTS.postprocess,
    ],
  ];

  it.each(cases)(
    "%s 后端阻塞时在预期超时后抛出友好错误而非永久等待",
    async (_name, call, timeoutMs) => {
      vi.stubGlobal("fetch", mockHangingFetch());
      const promise = call();
      const assertion = expect(promise).rejects.toThrow(/请求超时/);
      await vi.advanceTimersByTimeAsync(timeoutMs);
      await assertion;
    }
  );
});

describe("pollVideoTask 轮询截止期限", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("任务完成时返回结果", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        mockJsonResponse({ ...RUNNING_EVT, status: "completed", percent: 100 })
      )
    );
    const promise = pollVideoTask("/api/drama/progress/t1");
    await vi.advanceTimersByTimeAsync(API_TIMEOUTS.pollInterval);
    const evt = await promise;
    expect(evt.status).toBe("completed");
  });

  it("任务失败时返回失败事件", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        mockJsonResponse({ ...RUNNING_EVT, status: "failed", error: "oom" })
      )
    );
    const promise = pollVideoTask("/api/drama/progress/t1");
    await vi.advanceTimersByTimeAsync(API_TIMEOUTS.pollInterval);
    const evt = await promise;
    expect(evt.status).toBe("failed");
  });

  it("任务长期 running 超过最大等待时间时抛出超时错误", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => mockJsonResponse(RUNNING_EVT))
    );
    const promise = pollVideoTask("/api/drama/progress/t1", 10_000);
    const assertion = expect(promise).rejects.toThrow(/轮询超时/);
    await vi.advanceTimersByTimeAsync(60_000);
    await assertion;
  });

  it("轮询请求返回错误状态码时抛出错误", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("not found", { status: 404 }))
    );
    const promise = pollVideoTask("/api/drama/progress/t1");
    const assertion = expect(promise).rejects.toThrow(/轮询失败: 404/);
    await vi.advanceTimersByTimeAsync(API_TIMEOUTS.pollInterval);
    await assertion;
  });

  it("单次轮询请求阻塞时在 30 秒内中止", async () => {
    vi.stubGlobal("fetch", mockHangingFetch());
    const promise = pollVideoTask("/api/drama/progress/t1");
    const assertion = expect(promise).rejects.toThrow(/请求超时/);
    await vi.advanceTimersByTimeAsync(
      API_TIMEOUTS.pollInterval + API_TIMEOUTS.taskCreate
    );
    await assertion;
  });
});
