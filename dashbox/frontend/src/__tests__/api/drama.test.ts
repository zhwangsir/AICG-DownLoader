// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  dramaApiPath,
  firstDramaAudioUrl,
  generateDramaCompose,
  generateDramaScript,
  generateDramaStoryboard,
  generateDramaVideo,
  generateDramaVoice,
  mapDramaScenesToR18,
  pingDramaScriptAsync,
  pingDramaVideoAsync,
  pingDramaVoiceAsync,
  rewriteDramaAssetUrl,
  scenesPerEpisodeForDuration,
  resolveStudioEngine,
  toDramaBackendAssetUrl,
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
    expect(toDramaBackendAssetUrl("/api/drama/static/storyboard/s1.png")).toBe(
      "http://127.0.0.1:8100/static/storyboard/s1.png",
    );
    expect(toDramaBackendAssetUrl("http://192.168.71.127:8188/view?filename=a.png")).toBe(
      "http://192.168.71.127:8188/view?filename=a.png",
    );
    expect(
      firstDramaAudioUrl({
        audio_urls: [{ audio_url: "http://127.0.0.1:8100/static/audio/a.mp3" }],
      }),
    ).toBe("/api/drama/static/audio/a.mp3");
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

  it("creates a voice task then polls pipeline/status", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/drama/voice/generate_async") && init?.method === "POST") {
        expect(JSON.parse(String(init.body))).toMatchObject({
          scene_id: 1,
          dialogues: [{ text: "你回来了" }],
        });
        return new Response(JSON.stringify({ task_id: "voice-1", agent: "voice", status: "pending" }), {
          status: 200,
          headers: { "content-type": "application/json" },
        });
      }
      if (url.endsWith("/api/drama/pipeline/status/voice-1")) {
        return new Response(
          JSON.stringify({
            success: true,
            data: {
              task_id: "voice-1",
              status: "completed",
              percent: 100,
              message: "ok",
              error: null,
              result: {
                scene_id: 1,
                audio_urls: [{ audio_url: "http://127.0.0.1:8100/static/audio/a.mp3" }],
              },
            },
          }),
          { status: 200, headers: { "content-type": "application/json" } },
        );
      }
      throw new Error(`unexpected fetch ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    const voice = await generateDramaVoice(
      { scene_id: 1, dialogues: [{ text: "你回来了" }] },
      { intervalMs: 1, timeoutMs: 1000 },
    );
    expect(voice.audio_url).toBe("/api/drama/static/audio/a.mp3");
  });

  it("posts video generate_async with backend image url and rewrites video url", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/drama/video/generate_async") && init?.method === "POST") {
        expect(JSON.parse(String(init.body))).toMatchObject({
          scene_id: 1,
          image_url: "http://127.0.0.1:8100/static/storyboard/s1.png",
          duration_seconds: 5,
        });
        return new Response(JSON.stringify({ task_id: "vid-1", agent: "video", status: "pending" }), {
          status: 200,
          headers: { "content-type": "application/json" },
        });
      }
      if (url.endsWith("/api/drama/pipeline/status/vid-1")) {
        return new Response(
          JSON.stringify({
            success: true,
            data: {
              task_id: "vid-1",
              status: "completed",
              percent: 100,
              message: "ok",
              error: null,
              result: { scene_id: 1, video_url: "/static/video/s1.mp4", duration_seconds: 5 },
            },
          }),
          { status: 200, headers: { "content-type": "application/json" } },
        );
      }
      throw new Error(`unexpected fetch ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    const video = await generateDramaVideo(
      {
        scene_id: 1,
        image_url: "/api/drama/static/storyboard/s1.png",
        prompt: "rain night",
        duration_seconds: 5,
      },
      { intervalMs: 1, timeoutMs: 1000 },
    );
    expect(video.video_url).toBe("/api/drama/static/video/s1.mp4");
  });

  it("posts edit generate_async and omits empty subtitle_url", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/drama/edit/generate_async") && init?.method === "POST") {
        const body = JSON.parse(String(init.body));
        expect(body).toMatchObject({
          title: "杯底的血",
          segments: [{ scene_id: 1, video_url: "http://127.0.0.1:8100/static/video/s1.mp4" }],
        });
        expect(body.segments[0]).not.toHaveProperty("subtitle_url");
        return new Response(JSON.stringify({ task_id: "edit-1", agent: "edit", status: "pending" }), {
          status: 200,
          headers: { "content-type": "application/json" },
        });
      }
      if (url.endsWith("/api/drama/pipeline/status/edit-1")) {
        return new Response(
          JSON.stringify({
            success: true,
            data: {
              task_id: "edit-1",
              status: "completed",
              percent: 100,
              message: "ok",
              error: null,
              result: { final_video_url: "http://127.0.0.1:8100/static/video/final.mp4", duration_seconds: 6 },
            },
          }),
          { status: 200, headers: { "content-type": "application/json" } },
        );
      }
      throw new Error(`unexpected fetch ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    const composed = await generateDramaCompose(
      {
        title: "杯底的血",
        segments: [{ scene_id: 1, video_url: "/api/drama/static/video/s1.mp4" }],
      },
      { intervalMs: 1, timeoutMs: 1000 },
    );
    expect(composed.final_video_url).toBe("/api/drama/static/video/final.mp4");
  });

  it("forwards subtitle_url when compose segment has one", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/drama/edit/generate_async") && init?.method === "POST") {
        expect(JSON.parse(String(init.body)).segments[0].subtitle_url).toBe(
          "http://127.0.0.1:8100/static/subtitle/s1.srt",
        );
        return new Response(JSON.stringify({ task_id: "edit-2", agent: "edit", status: "pending" }), {
          status: 200,
          headers: { "content-type": "application/json" },
        });
      }
      if (url.endsWith("/api/drama/pipeline/status/edit-2")) {
        return new Response(
          JSON.stringify({
            success: true,
            data: {
              task_id: "edit-2",
              status: "completed",
              percent: 100,
              message: "ok",
              error: null,
              result: { final_video_url: "http://127.0.0.1:8100/static/video/final.mp4" },
            },
          }),
          { status: 200, headers: { "content-type": "application/json" } },
        );
      }
      throw new Error(`unexpected fetch ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    await generateDramaCompose(
      {
        title: "杯底的血",
        segments: [
          {
            scene_id: 1,
            video_url: "/api/drama/static/video/s1.mp4",
            subtitle_url: "/api/drama/static/subtitle/s1.srt",
          },
        ],
      },
      { intervalMs: 1, timeoutMs: 1000 },
    );
    expect(fetchMock).toHaveBeenCalled();
  });

  it("pings voice/video generate_async with empty body and does not start work", async () => {
    const seen: string[] = [];
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      seen.push(String(input));
      expect(init?.method).toBe("POST");
      expect(init?.body).toBe("{}");
      return new Response(JSON.stringify({ detail: "请求参数校验失败" }), { status: 422 });
    });
    vi.stubGlobal("fetch", fetchMock);
    await expect(pingDramaVoiceAsync()).resolves.toBe(422);
    await expect(pingDramaVideoAsync()).resolves.toBe(422);
    expect(seen).toEqual(["/api/drama/voice/generate_async", "/api/drama/video/generate_async"]);
  });
});
