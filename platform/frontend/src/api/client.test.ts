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
  getModelRegistry,
  pollVideoTask,
  runPipeline,
  cancelPipeline,
  resolveTaskUrl,
  resolveStaticUrl,
  extractScriptFromReport,
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

describe("M8 全链路 pipeline API", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  const PIPELINE_PARAMS = {
    premise: "都市悬疑，外卖员发现客户是凶手",
    genre: "都市悬疑",
    style: "写实电影感",
    episodes: 1,
    scenes_per_episode: 3,
    monetization_mode: "iaa" as const,
    generate_character_refs: false,
    run_quality_check: true,
    ai_label_enabled: true,
    license_number: "",
  };

  it("runPipeline POST /pipeline/run 并返回任务句柄", async () => {
    const fetchMock = vi.fn(async (url: string, opts?: RequestInit) => {
      expect(url).toBe("/api/drama/pipeline/run");
      expect(opts?.method).toBe("POST");
      const body = JSON.parse(String(opts?.body));
      expect(body.premise).toBe(PIPELINE_PARAMS.premise);
      expect(body.monetization_mode).toBe("iaa");
      expect(body.ai_label_enabled).toBe(true);
      return mockJsonResponse({
        task_id: "task-1",
        agent: "pipeline",
        status: "pending",
        poll_url: "http://localhost:8100/api/progress/task-1",
        stream_url: "http://localhost:8100/api/progress/task-1/stream",
      });
    });
    vi.stubGlobal("fetch", fetchMock);
    const resp = await runPipeline(PIPELINE_PARAMS);
    expect(resp.task_id).toBe("task-1");
    expect(resp.agent).toBe("pipeline");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("runPipeline 传递 run_visual_check 开关（M14 视觉漂移对照）", async () => {
    const fetchMock = vi.fn(async (_url: string, opts?: RequestInit) => {
      const body = JSON.parse(String(opts?.body));
      expect(body.run_visual_check).toBe(true);
      expect(body.run_quality_check).toBe(true);
      return mockJsonResponse({
        task_id: "task-vc",
        agent: "pipeline",
        status: "pending",
        poll_url: "http://localhost:8100/api/progress/task-vc",
        stream_url: "http://localhost:8100/api/progress/task-vc/stream",
      });
    });
    vi.stubGlobal("fetch", fetchMock);
    const resp = await runPipeline({ ...PIPELINE_PARAMS, run_visual_check: true });
    expect(resp.task_id).toBe("task-vc");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("runPipeline 非 2xx 时抛出含状态码的错误", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("bad request", { status: 422 }))
    );
    await expect(runPipeline(PIPELINE_PARAMS)).rejects.toThrow(
      /启动全链路任务失败: 422/
    );
  });

  it("cancelPipeline POST /pipeline/cancel/{task_id} 并对 id 编码", async () => {
    const fetchMock = vi.fn(async (url: string, opts?: RequestInit) => {
      expect(url).toBe("/api/drama/pipeline/cancel/task%2F1");
      expect(opts?.method).toBe("POST");
      return mockJsonResponse({ success: true, data: { cancel_requested: true } });
    });
    vi.stubGlobal("fetch", fetchMock);
    const resp = await cancelPipeline("task/1");
    expect(resp.success).toBe(true);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("cancelPipeline 404 时抛出错误", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("not found", { status: 404 }))
    );
    await expect(cancelPipeline("nope")).rejects.toThrow(/取消任务失败: 404/);
  });

  it("resolveTaskUrl 在相对 API_BASE 下剥离 localhost 源仅保留路径", () => {
    // 测试环境 API_BASE 为相对路径 "/api/drama"
    expect(resolveTaskUrl("http://localhost:8100/api/progress/t1/stream")).toBe(
      "/api/progress/t1/stream"
    );
  });

  it("resolveTaskUrl 对非法 URL 原样返回", () => {
    expect(resolveTaskUrl("not-a-url")).toBe("not-a-url");
  });

  it("resolveStaticUrl 对绝对 URL 原样返回、对相对路径在相对 API_BASE 下原样返回", () => {
    expect(resolveStaticUrl("http://x/f.mp4")).toBe("http://x/f.mp4");
    expect(resolveStaticUrl("/static/final/p.mp4")).toBe("/static/final/p.mp4");
    expect(resolveStaticUrl("")).toBe("");
  });
});

describe("M9 extractScriptFromReport 报告剧本提取", () => {
  const SCRIPT = {
    project_id: "p1",
    title: "外卖惊魂",
    genre: "都市悬疑",
    aspect_ratio: "9:16",
    total_episodes: 1,
    characters: [
      {
        character_id: "c1",
        name: "小李",
        role: "主角",
        age: 25,
        description: "外卖员",
        personality: "机敏",
      },
    ],
    scenes: [
      {
        scene_id: 1,
        episode: 1,
        shot_type: "close",
        description: "d",
        prompt: "p",
        negative_prompt: "",
        character_actions: "",
        dialogue: "台词",
        emotion: "紧张",
        duration_seconds: 3,
        camera_movement: "static",
      },
    ],
  };

  it("完整报告返回剧本数据", () => {
    const report = {
      project_id: "p1",
      premise: "x",
      started_at: 1,
      steps: { script: { title: "外卖惊魂", characters: 1, scenes: 1, data: SCRIPT } },
      passed: true,
    };
    const s = extractScriptFromReport(report);
    expect(s).not.toBeNull();
    expect(s!.title).toBe("外卖惊魂");
    expect(s!.characters).toHaveLength(1);
    expect(s!.scenes[0].dialogue).toBe("台词");
  });

  it("report 为 null/undefined 时返回 null", () => {
    expect(extractScriptFromReport(null)).toBeNull();
    expect(extractScriptFromReport(undefined)).toBeNull();
  });

  it("steps.script.data 缺失或结构不完整时返回 null", () => {
    expect(
      extractScriptFromReport({
        project_id: "p",
        premise: "x",
        started_at: 1,
        steps: {},
      })
    ).toBeNull();
    expect(
      extractScriptFromReport({
        project_id: "p",
        premise: "x",
        started_at: 1,
        steps: { script: { title: "t" } },
      })
    ).toBeNull();
    expect(
      extractScriptFromReport({
        project_id: "p",
        premise: "x",
        started_at: 1,
        steps: { script: { data: { title: "t" } } }, // 缺 project_id/characters/scenes
      })
    ).toBeNull();
  });
});

describe("模型注册表 API（下载器 ↔ 工作台打通）", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("getModelRegistry GET /models/registry 并解析返回结构", async () => {
    const registry = {
      loras: [
        {
          filename: "guofeng.safetensors",
          name: "国风 LoRA",
          style_key: "guofeng",
          trigger_words: ["guofeng"],
          weight: 0.8,
          sha256: "abc123",
          size_kb: 1024,
          downloaded: true,
          subdir: "loras",
          downloaded_at: 1700000000,
        },
      ],
      downloader_models: [{ id: "m1" }],
      stats: { total: 1 },
      sources: { civitai: true },
    };
    const fetchMock = vi.fn(async (url: string, opts?: RequestInit) => {
      expect(url).toBe("/api/drama/models/registry");
      expect(opts?.method ?? "GET").toBe("GET");
      return mockJsonResponse(registry);
    });
    vi.stubGlobal("fetch", fetchMock);
    const resp = await getModelRegistry();
    expect(resp.loras).toHaveLength(1);
    expect(resp.loras[0].filename).toBe("guofeng.safetensors");
    expect(resp.loras[0].trigger_words).toEqual(["guofeng"]);
    expect(resp.stats).toEqual({ total: 1 });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
