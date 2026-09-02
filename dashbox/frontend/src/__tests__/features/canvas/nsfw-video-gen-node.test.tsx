// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  CANVAS_NODE_TYPES,
  type NSFWVideoGenNodeData,
} from "@/features/canvas/domain/canvasNodes";
import { NSFWVideoGenNode } from "@/features/canvas/nodes/NSFWVideoGenNode";

const updateNodeData = vi.fn();
const setSelectedNode = vi.fn();

let nsfwEnabled = false;
const mutateAsync = vi.fn();

vi.mock("@xyflow/react", async () => {
  const actual =
    await vi.importActual<typeof import("@xyflow/react")>("@xyflow/react");
  return {
    ...actual,
    Handle: ({ id, type }: { id?: string; type?: string }) => (
      <div data-testid={`handle-${type ?? "unknown"}-${id ?? "default"}`} />
    ),
    useUpdateNodeInternals: () => vi.fn(),
  };
});

vi.mock("@/stores/canvasStore", () => ({
  useCanvasStore: (selector: (state: unknown) => unknown) =>
    selector({
      updateNodeData,
      setSelectedNode,
    }),
}));

vi.mock("@/lib/queries/model-library", () => ({
  useNsfwStatus: () => ({
    data: { ok: true, data: { nsfw_enabled: nsfwEnabled } },
    isLoading: false,
  }),
  useVideoPresets: () => ({
    data: {
      ok: true,
      data: {
        items: [
          { id: "wan22-missionary", label: "传教士（Wan 2.2 I2V）", trigger: "m15510n4ry", route: "wan" },
          { id: "h3-aio", label: "全能动作+音画（MiniMax H3）", trigger: "hmmotion", route: "h3" },
        ],
      },
    },
  }),
  useGenerateVideo: () => ({ mutateAsync }),
}));

let upstreamFrames: string[] = ["/static/projects/proj-1/freezone/x/frame.png"];

vi.mock("@/features/canvas/application/useUpstreamGraph", () => ({
  useUpstreamImages: () => upstreamFrames,
}));

vi.mock("@/api/ops", () => ({
  uploadFreezoneImage: vi.fn(),
}));

vi.mock("@/lib/url-params", () => ({
  readUrl: () => ({ project: "proj-1", canvas: "default" }),
}));

vi.mock("@/features/canvas/ui/NodeHeader", () => ({
  NODE_HEADER_FLOATING_POSITION_CLASS: "",
  NodeHeader: ({ titleText }: { titleText: string }) => (
    <div>{titleText}</div>
  ),
}));

vi.mock("@/features/canvas/ui/NodeGenerationOverlay", () => ({
  NodeGenerationOverlay: () => <div data-testid="gen-overlay" />,
}));

vi.mock("@/features/canvas/ui/RegenerateButton", () => ({
  RegenerateButton: ({ onClick }: { onClick: () => void }) => (
    <button type="button" onClick={onClick}>
      regenerate
    </button>
  ),
}));

function makeData(
  overrides: Partial<NSFWVideoGenNodeData> = {},
): NSFWVideoGenNodeData {
  return {
    displayName: "R18 视频",
    prompt: "",
    presetId: "",
    width: 768,
    height: 1344,
    length: 124,
    videoUrl: null,
    firstFrameUrl: null,
    isGenerating: false,
    generationStartedAt: null,
    generationDurationMs: 300000,
    ...overrides,
  };
}

function renderNode(data: Partial<NSFWVideoGenNodeData> = {}, selected = true) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <NSFWVideoGenNode
        id="node-1"
        type={CANVAS_NODE_TYPES.nsfwVideoGen}
        selected={selected}
        dragging={false}
        zIndex={0}
        positionAbsoluteX={0}
        positionAbsoluteY={0}
        isConnectable={false}
        draggable={false}
        selectable={false}
        deletable={false}
        data={makeData(data)}
      />
    </QueryClientProvider>,
  );
}

describe("NSFWVideoGenNode", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    nsfwEnabled = false;
    upstreamFrames = ["/static/projects/proj-1/freezone/x/frame.png"];
  });

  it("R18 未开启时呈锁定态：提示可见、无生成入口", () => {
    renderNode();
    expect(screen.getByText(/R18 内容未开启/)).toBeTruthy();
    expect(screen.queryByTitle(/生成 R18 视频/)).toBeNull();
  });

  it("R18 开启后只显示 H3 预设，缺预设时生成禁用", async () => {
    nsfwEnabled = true;
    renderNode({ prompt: "hmmotion, a woman" });
    // mock 仍带 wan 项，UI 必须按 route===h3 过滤
    expect(await screen.findByText("全能动作+音画")).toBeTruthy();
    expect(screen.queryByText("传教士")).toBeNull();
    const button = screen.getByTitle("需先选择预设");
    expect(button.hasAttribute("disabled")).toBe(true);
  });

  it("缺首帧时面板出现主动引导条（I2V 必需首帧，不能只输提示词）", async () => {
    nsfwEnabled = true;
    // 无上游图 + 无自身首帧 → 引导条可见
    upstreamFrames = [];
    renderNode({ prompt: "x", presetId: "h3-aio" });
    expect(await screen.findByText(/图生视频需要一张首帧图/)).toBeTruthy();
    expect(screen.getByTitle("需先上传首帧图或连线上游图片节点")).toBeTruthy();
  });

  it("有首帧（上游连线产出）时不显示引导条", async () => {
    nsfwEnabled = true;
    renderNode({ prompt: "x", presetId: "h3-aio" });
    await screen.findByText("全能动作+音画");
    expect(screen.queryByText(/图生视频需要一张首帧图/)).toBeNull();
  });

  it("选预设后同步路线默认尺寸（h3 → 768×1344 / 124 帧）", async () => {
    nsfwEnabled = true;
    renderNode({ presetId: "h3-aio" });
    await waitFor(() => {
      expect(updateNodeData).toHaveBeenCalledWith(
        "node-1",
        expect.objectContaining({ width: 768, height: 1344, length: 124 }),
      );
    });
  });

  it("提交带全部参数并回填 videoUrl", async () => {
    nsfwEnabled = true;
    mutateAsync.mockResolvedValue({
      ok: true,
      data: {
        seed: 7,
        preset_id: "h3-aio",
        filename: "out.mp4",
        backend: "http://192.168.71.127:8195",
        url: "/static/projects/proj-1/freezone/_outputs/nsfw_studio/abc.mp4",
      },
    });
    // 模拟已选 h3 默认档后的节点状态（宽度/帧数由 store 闭环持有）
    renderNode({
      prompt: "hmmotion, scene",
      presetId: "h3-aio",
      width: 768,
      height: 1344,
      length: 124,
    });
    const button = await screen.findByTitle(/生成 R18 视频/);
    expect(button.hasAttribute("disabled")).toBe(false);
    await userEvent.click(button);
    await waitFor(() => {
      expect(mutateAsync).toHaveBeenCalledTimes(1);
    });
    const payload = mutateAsync.mock.calls[0][0] as Record<string, unknown>;
    expect(payload.preset_id).toBe("h3-aio");
    expect(payload.project_id).toBe("proj-1");
    expect(payload.first_frame_url).toMatch(/^http:\/\/localhost:\d+\//);
    expect(payload.width).toBe(768);
    expect(payload.length).toBe(124);
    await waitFor(() => {
      const patch = updateNodeData.mock.calls.find(
        ([, p]) => (p as Record<string, unknown>).videoUrl,
      );
      expect(patch?.[1]).toMatchObject({
        videoUrl: "/static/projects/proj-1/freezone/_outputs/nsfw_studio/abc.mp4",
      });
    });
  });

  it("生成失败时错误写入节点", async () => {
    nsfwEnabled = true;
    mutateAsync.mockRejectedValue(new Error("ComfyUI 执行失败"));
    renderNode({ prompt: "x", presetId: "h3-aio" });
    await userEvent.click(await screen.findByTitle(/生成 R18 视频/));
    await waitFor(() => {
      expect(
        updateNodeData.mock.calls.some(
          ([, p]) =>
            (p as Record<string, unknown>).generationError ===
            "ComfyUI 执行失败",
        ),
      ).toBe(true);
    });
  });
});
