// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  dramaApiPath,
  generateDramaScript,
  generateDramaStoryboard,
  mapDramaScenesToR18,
  pingDramaScriptAsync,
  rewriteDramaAssetUrl,
  scenesPerEpisodeForDuration,
  resolveStudioEngine,
} from "@/api/drama";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("drama api helpers", () => {
  it("builds same-origin /api/drama paths", () => {
    expect(dramaApiPath("health")).toBe("/api/drama/health");
    expect(dramaApiPath("/script/generate_async")).toBe("/api/drama/script/generate_async");
    expect(dramaApiPath("")).toBe("/api/drama");
  });

  it("defaults engine to 短剧模块", () => {
    expect(resolveStudioEngine(undefined)).toBe("drama");
    expect(resolveStudioEngine("drama")).toBe("drama");
    expect(resolveStudioEngine("r18")).toBe("r18");
  });

  it("rewrites platform /static assets onto the drama proxy", () => {
    expect(rewriteDramaAssetUrl("/static/storyboard/s1.png")).toBe(
      "/api/drama/static/storyboard/s1.png",
    );
    expect(rewriteDramaAssetUrl("http://127.0.0.1:8100/static/character/a.png")).toBe(
      "/api/drama/static/character/a.png",
    );
    expect(rewriteDramaAssetUrl("/api/drama/static/x.png")).toBe("/api/drama/static/x.png");
    expect(rewriteDramaAssetUrl("http://192.168.1.10:8188/view?filename=a.png")).toBe(
      "http://192.168.1.10:8188/view?filename=a.png",
    );
  });

  it("maps platform scenes onto R18 canvas scene rows", () => {
    const scenes = mapDramaScenesToR18([
      {
        scene_id: 2,
        description: "雨夜对峙",
        prompt: "cinematic rain, two figures",
        dialogue: "你回来了",
        emotion: "tension",
        duration_seconds: 6,
        shot_type: "中景",
        camera_movement: "push in",
        character_actions: "steps forward",
      },
    ]);
    expect(scenes).toHaveLength(1);
    expect(scenes[0]).toMatchObject({
      scene_no: 2,
      kind: "plot",
      title: "雨夜对峙",
      image_prompt: "cinematic rain, two figures",
      dialogue: "你回来了",
      audio: "tts",
      duration_sec: 6,
      emotion: "tension",
    });
  });

  it("clamps scenes_per_episode from duration", () => {
    expect(scenesPerEpisodeForDuration(90)).toBe(11);
    expect(scenesPerEpisodeForDuration(10)).toBe(3);
    expect(scenesPerEpisodeForDuration(400)).toBe(30);
  });
});

describe("drama async generate", () => {
  it("creates a script task then polls pipeline/status", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/drama/script/generate_async") && init?.method === "POST") {
        expect(JSON.parse(String(init.body))).toMatchObject({
          premise: "杯底的血",
          episodes: 1,
        });
        return new Response(JSON.stringify({ task_id: "script-1", agent: "script", status: "pending" }), {
          status: 200,
          headers: { "content-type": "application/json" },
        });
      }
      if (url.endsWith("/api/drama/pipeline/status/script-1")) {
        return new Response(
          JSON.stringify({
            success: true,
            data: {
              task_id: "script-1",
              status: "completed",
              percent: 100,
              message: "ok",
              error: null,
              result: {
                title: "杯底的血",
                characters: [],
                scenes: [{ scene_id: 1, description: "开场", prompt: "bar night" }],
              },
            },
          }),
          { status: 200, headers: { "content-type": "application/json" } },
        );
      }
      throw new Error(`unexpected fetch ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    const script = await generateDramaScript({ premise: "杯底的血" }, { intervalMs: 1, timeoutMs: 1000 });
    expect(script.title).toBe("杯底的血");
    expect(script.scenes[0].scene_id).toBe(1);
    expect(fetchMock).toHaveBeenCalled();
  });

  it("posts storyboard generate_async with sketch_mode and rewrites image url", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/drama/storyboard/generate_async") && init?.method === "POST") {
        expect(JSON.parse(String(init.body))).toMatchObject({
          sketch_mode: true,
          scene: { scene_id: 1 },
        });
        return new Response(JSON.stringify({ task_id: "sb-1", agent: "storyboard", status: "pending" }), {
          status: 200,
          headers: { "content-type": "application/json" },
        });
      }
      if (url.endsWith("/api/drama/pipeline/status/sb-1")) {
        return new Response(
          JSON.stringify({
            success: true,
            data: {
              task_id: "sb-1",
              status: "completed",
              percent: 100,
              message: "ok",
              error: null,
              result: { scene_id: 1, image_url: "/static/storyboard/s1.png", is_sketch: true },
            },
          }),
          { status: 200, headers: { "content-type": "application/json" } },
        );
      }
      throw new Error(`unexpected fetch ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    const board = await generateDramaStoryboard(
      { scene: { scene_id: 1, description: "开场" }, characters: [], sketch_mode: true },
      { intervalMs: 1, timeoutMs: 1000 },
    );
    expect(board.image_url).toBe("/api/drama/static/storyboard/s1.png");
    expect(board.is_sketch).toBe(true);
  });

  it("pings generate_async with empty body and returns status", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      expect(String(input)).toBe("/api/drama/script/generate_async");
      expect(init?.method).toBe("POST");
      expect(init?.body).toBe("{}");
      return new Response(JSON.stringify({ detail: "请求参数校验失败" }), { status: 422 });
    });
    vi.stubGlobal("fetch", fetchMock);
    await expect(pingDramaScriptAsync()).resolves.toBe(422);
  });
});
