// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  CANVAS_NODE_TYPES,
  type NSFWImageGenNodeData,
} from "@/features/canvas/domain/canvasNodes";
import { NSFWImageGenNode } from "@/features/canvas/nodes/NSFWImageGenNode";

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
  useGenerateImage: () => ({ mutateAsync }),
}));

let upstreamRefs: string[] = ["/static/projects/proj-1/freezone/x/ref.png"];

vi.mock("@/features/canvas/application/useUpstreamGraph", () => ({
  useUpstreamImages: () => upstreamRefs,
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

vi.mock("@/features/canvas/ui/CanvasNodeImage", () => ({
  CanvasNodeImage: ({ src }: { src: string }) => <img src={src} alt="" />,
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

vi.mock("@/components/settings/model-name-picker", () => ({
  ModelNamePicker: ({ value }: { value: string }) => (
    <div data-testid="model-picker">{value || "empty"}</div>
  ),
}));

function makeData(
  overrides: Partial<NSFWImageGenNodeData> = {},
): NSFWImageGenNodeData {
  return {
    displayName: "R18 图片",
    imageUrl: null,
    previewImageUrl: null,
    aspectRatio: "16:9",
    isSizeManuallyAdjusted: false,
    prompt: "",
    negativePrompt: "",
    checkpoint: "",
    size: "1216x832",
    referenceImageUrl: null,
    isGenerating: false,
    generationStartedAt: null,
    generationDurationMs: 60000,
    ...overrides,
  };
}

function renderNode(data: Partial<NSFWImageGenNodeData> = {}, selected = true) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <NSFWImageGenNode
        id="node-1"
        type={CANVAS_NODE_TYPES.nsfwImageGen}
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

describe("NSFWImageGenNode", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    nsfwEnabled = false;
    upstreamRefs = ["/static/projects/proj-1/freezone/x/ref.png"];
  });

  it("R18 未开启时呈锁定态：提示文案可见、无生成入口", () => {
    renderNode();
    expect(screen.getByText(/R18 内容未开启/)).toBeTruthy();
    expect(screen.queryByTitle("生成 R18 图片")).toBeNull();
  });

  it("R18 开启后显示操作面板，缺 checkpoint 时生成按钮禁用", async () => {
    nsfwEnabled = true;
    // 上游 mock 提供 1 张参考图（锚定满足），但 checkpoint 为空 → 禁用。
    renderNode({ prompt: "a woman", checkpoint: "" });
    const button = await screen.findByTitle("先选择底模");
    expect(button.hasAttribute("disabled")).toBe(true);
  });

  it("参考图可选：无参考图时提示词+底模即可生成，payload 不含 reference_url", async () => {
    nsfwEnabled = true;
    upstreamRefs = [];
    mutateAsync.mockResolvedValue({
      ok: true,
      data: {
        data: [{ b64_json: "" }],
        url: "/static/projects/proj-1/freezone/_outputs/nsfw_studio/opt.png",
      },
    });
    renderNode({ prompt: "test prompt", checkpoint: "hmnsfw.safetensors" });
    const button = await screen.findByTitle(/纯文生图/);
    expect(button.hasAttribute("disabled")).toBe(false);
    await userEvent.click(button);
    await waitFor(() => {
      expect(mutateAsync).toHaveBeenCalledTimes(1);
    });
    const payload = mutateAsync.mock.calls[0][0] as Record<string, unknown>;
    expect("reference_url" in payload).toBe(false);
    await waitFor(() => {
      const patch = updateNodeData.mock.calls.find(
        ([, p]) => (p as Record<string, unknown>).imageUrl,
      );
      expect(patch?.[1]).toMatchObject({
        imageUrl: "/static/projects/proj-1/freezone/_outputs/nsfw_studio/opt.png",
      });
    });
  });

  it("提交时带 project_id 与绝对化 reference_url，成功后回填项目媒体 url", async () => {
    nsfwEnabled = true;
    mutateAsync.mockResolvedValue({
      ok: true,
      data: {
        data: [{ b64_json: "" }],
        url: "/static/projects/proj-1/freezone/_outputs/nsfw_studio/abc.png",
      },
    });
    renderNode({ prompt: "test prompt", checkpoint: "hmnsfw.safetensors" });
    const button = await screen.findByTitle(/IPAdapter 锚定参考图/);
    expect(button.hasAttribute("disabled")).toBe(false);
    await userEvent.click(button);
    await waitFor(() => {
      expect(mutateAsync).toHaveBeenCalledTimes(1);
    });
    const payload = mutateAsync.mock.calls[0][0] as Record<string, string>;
    expect(payload.project_id).toBe("proj-1");
    expect(payload.checkpoint).toBe("hmnsfw.safetensors");
    // 相对项目媒体 URL 被绝对化，供 local_gateway 下载参考图
    expect(payload.reference_url).toMatch(
      /^http:\/\/localhost:\d+\/static\/projects\/proj-1\//,
    );
    await waitFor(() => {
      const patch = updateNodeData.mock.calls.find(
        ([, patch]) => (patch as Record<string, unknown>).imageUrl,
      );
      expect(patch?.[1]).toMatchObject({
        imageUrl: "/static/projects/proj-1/freezone/_outputs/nsfw_studio/abc.png",
      });
    });
  });

  it("生成失败时错误信息写入节点", async () => {
    nsfwEnabled = true;
    mutateAsync.mockRejectedValue(new Error("boom"));
    renderNode({ prompt: "x", checkpoint: "hmnsfw.safetensors" });
    await userEvent.click(await screen.findByTitle(/锚定参考图/));
    await waitFor(() => {
      expect(
        updateNodeData.mock.calls.some(
          ([, patch]) => (patch as Record<string, unknown>).generationError === "boom",
        ),
      ).toBe(true);
    });
  });
});
