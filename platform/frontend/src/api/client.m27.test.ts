import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  cancelDownloadTask,
  changeNsfwPin,
  getDownloadTasks,
  getNasLibrary,
  getNsfwStatus,
  searchCivitaiModels,
  setNsfwEnabled,
  startModelDownload,
} from "./client";

function mockJsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const nasEntry = {
  name: "majicMIX_v7.safetensors",
  rel_path: "checkpoints/majicMIX_v7.safetensors",
  root: "models",
  type: "checkpoints",
  size: 100,
  mtime: 1700000000,
  nsfw: false,
};

const task = {
  task_id: "t1",
  filename: "m.safetensors",
  subdir: "checkpoints",
  dest: "/nas/checkpoints/m.safetensors",
  source_url: "https://x/dl",
  sha256: null,
  nsfw: false,
  status: "running",
  downloaded: 50,
  total: 100,
  speed_bps: 10,
  error: null,
  created_at: 1,
};

describe("M27 client — NAS 模型库", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("getNasLibrary 无参数 → 纯路径", async () => {
    const spy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(
        mockJsonResponse({ items: [nasEntry], total: 1, types: ["checkpoints"], scanned_at: 1, cache_hit: false })
      );
    const res = await getNasLibrary({});
    expect(res.items[0].name).toBe("majicMIX_v7.safetensors");
    expect(spy.mock.calls[0][0]).toBe("/api/models/library");
  });

  it("getNasLibrary 全参数 → query string 组装", async () => {
    const spy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(
        mockJsonResponse({ items: [], total: 0, types: [], scanned_at: 1, cache_hit: true })
      );
    await getNasLibrary({ type: "loras", q: "style", include_nsfw: true, refresh: true });
    const url = spy.mock.calls[0][0] as string;
    expect(url).toContain("type=loras");
    expect(url).toContain("q=style");
    expect(url).toContain("include_nsfw=true");
    expect(url).toContain("refresh=true");
  });

  it("getNasLibrary 非 200 → 抛 detail 消息", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      mockJsonResponse({ detail: "NAS 挂载失效" }, 500)
    );
    await expect(getNasLibrary({})).rejects.toThrow("NAS 挂载失效");
  });
});

describe("M27 client — Civitai 搜索", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("searchCivitaiModels 参数组装", async () => {
    const spy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(mockJsonResponse({ items: [], total: 0 }));
    await searchCivitaiModels({ q: "test", type: "LORA", limit: 5, include_nsfw: true });
    const url = spy.mock.calls[0][0] as string;
    expect(url).toContain("/api/models/search?");
    expect(url).toContain("q=test");
    expect(url).toContain("type=LORA");
    expect(url).toContain("limit=5");
    expect(url).toContain("include_nsfw=true");
  });

  it("searchCivitaiModels 502 → 抛 detail", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      mockJsonResponse({ detail: "Civitai 搜索失败: timeout" }, 502)
    );
    await expect(searchCivitaiModels({ q: "x" })).rejects.toThrow("Civitai 搜索失败");
  });
});

describe("M27 client — 下载任务", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("startModelDownload POST body 透传", async () => {
    const spy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(mockJsonResponse(task, 201));
    const res = await startModelDownload({
      download_url: "https://x/dl",
      filename: "m.safetensors",
      subdir: "checkpoints",
      sha256: "abc",
      nsfw: true,
    });
    expect(res.task_id).toBe("t1");
    const [url, opts] = spy.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/models/download");
    expect(JSON.parse(opts.body as string)).toMatchObject({
      filename: "m.safetensors",
      sha256: "abc",
      nsfw: true,
    });
  });

  it("startModelDownload 403 → 抛 NSFW detail", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      mockJsonResponse({ detail: "NSFW 内容未开启：请先在模型库面板输入 PIN 解锁" }, 403)
    );
    await expect(
      startModelDownload({ download_url: "u", filename: "f", subdir: "loras" })
    ).rejects.toThrow(/NSFW 内容未开启/);
  });

  it("getDownloadTasks 返回 items", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      mockJsonResponse({ items: [task] })
    );
    const tasks = await getDownloadTasks();
    expect(tasks[0].status).toBe("running");
  });

  it("cancelDownloadTask DELETE", async () => {
    const spy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(mockJsonResponse({ task_id: "t1", status: "cancel_requested" }));
    await cancelDownloadTask("t1");
    const [url, opts] = spy.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/models/downloads/t1");
    expect(opts.method).toBe("DELETE");
  });

  it("cancelDownloadTask 409 → 抛 detail", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      mockJsonResponse({ detail: "任务不存在或已结束，无法取消" }, 409)
    );
    await expect(cancelDownloadTask("t9")).rejects.toThrow(/无法取消/);
  });
});

describe("M27 client — NSFW 设置", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("getNsfwStatus", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      mockJsonResponse({ nsfw_enabled: true, has_pin: true })
    );
    const s = await getNsfwStatus();
    expect(s).toEqual({ nsfw_enabled: true, has_pin: true });
  });

  it("setNsfwEnabled 首次（new_pin 透传）", async () => {
    const spy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(mockJsonResponse({ nsfw_enabled: true, has_pin: true }));
    await setNsfwEnabled(true, "", "1234");
    const [, opts] = spy.mock.calls[0] as [string, RequestInit];
    expect(JSON.parse(opts.body as string)).toEqual({
      enabled: true,
      pin: "",
      new_pin: "1234",
    });
  });

  it("setNsfwEnabled 非首次（new_pin 为 null）", async () => {
    const spy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(mockJsonResponse({ nsfw_enabled: false, has_pin: true }));
    await setNsfwEnabled(false, "1234");
    const [, opts] = spy.mock.calls[0] as [string, RequestInit];
    expect(JSON.parse(opts.body as string)).toEqual({
      enabled: false,
      pin: "1234",
      new_pin: null,
    });
  });

  it("setNsfwEnabled 403 → 抛 PIN 错误 detail", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      mockJsonResponse({ detail: "PIN 错误" }, 403)
    );
    await expect(setNsfwEnabled(true, "0000")).rejects.toThrow("PIN 错误");
  });

  it("changeNsfwPin", async () => {
    const spy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(mockJsonResponse({ nsfw_enabled: true, has_pin: true }));
    await changeNsfwPin("1234", "5678");
    const [url, opts] = spy.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/settings/nsfw/pin");
    expect(JSON.parse(opts.body as string)).toEqual({ pin: "1234", new_pin: "5678" });
  });

  it("错误响应非 JSON → 回退文本", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response("网关错误", { status: 502 })
    );
    await expect(getNsfwStatus()).rejects.toThrow(/读取 NSFW 状态失败: 502/);
  });
});
