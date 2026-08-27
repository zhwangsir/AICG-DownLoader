// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  CANVAS_NODE_TYPES,
  type NSFWDramaStudioNodeData,
} from "@/features/canvas/domain/canvasNodes";
import { NSFWDramaStudioNode } from "@/features/canvas/nodes/NSFWDramaStudioNode";

const updateNodeData = vi.fn();
const setSelectedNode = vi.fn();
let nsfwEnabled = true;

vi.mock("@xyflow/react", async () => {
  const actual = await vi.importActual<typeof import("@xyflow/react")>("@xyflow/react");
  return {
    ...actual,
    Handle: ({ id, type }: { id?: string; type?: string }) => (
      <div data-testid={`handle-${type ?? "unknown"}-${id ?? "default"}`} />
    ),
    useUpdateNodeInternals: () => vi.fn(),
  };
});

vi.mock("@/stores/canvasStore", () => {
  const store = (selector: (state: unknown) => unknown) =>
    selector({ updateNodeData, setSelectedNode, nodes: [] });
  (store as unknown as { getState: () => unknown }).getState = () => ({
    nodes: [],
    updateNodeData,
    setSelectedNode,
  });
  return { useCanvasStore: store };
});

vi.mock("@/lib/queries/model-library", () => ({
  R18_TTS_VOICE_OPTIONS: [{ value: "human-zh-paimon", label: "派蒙" }],
  gatewayErrorMessage: (e: unknown, fallback: string) =>
    e instanceof Error ? e.message : fallback,
  useNsfwStatus: () => ({
    data: { ok: true, data: { nsfw_enabled: nsfwEnabled } },
    isLoading: false,
  }),
  useGenerateImage: () => ({ mutateAsync: vi.fn() }),
  useGenerateVideo: () => ({ mutateAsync: vi.fn() }),
  useR18Compose: () => ({ mutateAsync: vi.fn() }),
  useR18ScriptPlan: () => ({ mutateAsync: vi.fn() }),
  useR18Tts: () => ({ mutateAsync: vi.fn() }),
}));

vi.mock("@/features/canvas/application/useUpstreamGraph", () => ({
  useUpstreamImages: () => [],
}));

vi.mock("@/api/ops", () => ({ uploadFreezoneImage: vi.fn() }));
vi.mock("@/lib/url-params", () => ({
  readUrl: () => ({ project: "proj-1", canvas: "default" }),
}));
vi.mock("@/api/drama", async () => {
  const actual = await vi.importActual<typeof import("@/api/drama")>("@/api/drama");
  return {
    ...actual,
    getDramaHealth: vi.fn(async () => ({ status: "ok", version: "0.4.0" })),
    pingDramaScriptAsync: vi.fn(async () => 422),
  };
});
vi.mock("@/features/canvas/ui/NodeHeader", () => ({
  NODE_HEADER_FLOATING_POSITION_CLASS: "",
  NodeHeader: ({ titleText }: { titleText: string }) => <div>{titleText}</div>,
}));
vi.mock("@/components/settings/model-name-picker", () => ({
  ModelNamePicker: ({ value }: { value: string }) => (
    <div data-testid="model-picker">{value || "empty"}</div>
  ),
}));

function makeData(overrides: Partial<NSFWDramaStudioNodeData> = {}): NSFWDramaStudioNodeData {
  return {
    displayName: "短剧工厂",
    synopsis: "杯底的血",
    charactersText: "",
    styleHint: "",
    durationSec: 90,
    aspect: "9:16",
    checkpoint: "",
    size: "832x1216",
    voice: "human-zh-paimon",
    autoConfirm: false,
    pipelineEngine: "drama",
    dramaScript: null,
    planTitle: "",
    scenes: [],
    frameUrls: {},
    shotOutputs: {},
    composeUrl: null,
    pipeline: "idle",
    ...overrides,
  };
}

function renderNode(data: Partial<NSFWDramaStudioNodeData> = {}, selected = true) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <NSFWDramaStudioNode
        id="studio-1"
        type={CANVAS_NODE_TYPES.nsfwDramaStudio}
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

describe("NSFWDramaStudioNode drama wiring", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    nsfwEnabled = true;
  });

  it("shows 短剧模块 / R18 toggle and defaults to 短剧模块", async () => {
    renderNode();
    expect(await screen.findByText("短剧模块")).toBeTruthy();
    expect(screen.getByText("R18")).toBeTruthy();
    expect(await screen.findByText(/短剧模块流水线/)).toBeTruthy();
    expect(await screen.findByText(/generate_async 可达/)).toBeTruthy();
  });

  it("clicking R18 persists pipelineEngine", async () => {
    const user = userEvent.setup();
    renderNode();
    await user.click(await screen.findByText("R18"));
    expect(updateNodeData).toHaveBeenCalledWith("studio-1", { pipelineEngine: "r18" });
  });

  it("engine toggle covers tts/video/compose wiring copy", async () => {
    renderNode();
    const toggle = await screen.findByTitle("剧本/首帧/配音/出片/合成走短剧模块或 DashBox R18");
    expect(toggle).toBeTruthy();
  });
});
