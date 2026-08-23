// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// R18 制作工厂：一键插入 8 工序流水线（左到右自动连线）。
import { beforeEach, describe, expect, it } from "vitest";

import { CANVAS_NODE_TYPES } from "@/features/canvas/domain/canvasNodes";
import { useCanvasStore } from "@/stores/canvasStore";

// 工序序（2026-08-19 调整：分镜表提前到③，数字资产在分镜确认后按镜头生成）：
// ①立项 → ②剧本 → ③分镜表 → ④数字资产 → ⑤镜头 → ⑥音频 → ⑦后期 → ⑧质检
const EXPECTED_CHAIN = [
  CANVAS_NODE_TYPES.nsfwFactoryInit,
  CANVAS_NODE_TYPES.nsfwFactoryScript,
  CANVAS_NODE_TYPES.nsfwFactoryStoryboard,
  CANVAS_NODE_TYPES.nsfwFactoryAsset,
  CANVAS_NODE_TYPES.nsfwFactoryShot,
  CANVAS_NODE_TYPES.nsfwFactoryAudio,
  CANVAS_NODE_TYPES.nsfwFactoryCompose,
  CANVAS_NODE_TYPES.nsfwFactoryQc,
];

describe("spawnR18FactoryPipeline", () => {
  beforeEach(() => {
    useCanvasStore.setState({ nodes: [], edges: [], pendingFitViewNodeIds: null });
  });

  it("creates 8 chained nodes left-to-right with 7 edges", () => {
    const headId = useCanvasStore.getState().spawnR18FactoryPipeline();

    expect(headId).toBeTruthy();
    const { nodes, edges } = useCanvasStore.getState();
    expect(nodes).toHaveLength(8);
    expect(edges).toHaveLength(7);

    const byId = new Map(nodes.map((n) => [n.id, n]));
    // 沿边从链首走到底，顺序必须是 8 工序设计序
    let currentId: string | null = headId;
    const walked: string[] = [];
    while (currentId) {
      walked.push(currentId);
      const edge = edges.find((e) => e.source === currentId);
      currentId = edge ? edge.target : null;
    }
    expect(walked).toHaveLength(8);
    expect(walked.map((id) => byId.get(id)?.type)).toEqual(EXPECTED_CHAIN);
  });

  it("places nodes strictly left-to-right by x position", () => {
    useCanvasStore.getState().spawnR18FactoryPipeline({ x: 100, y: 50 });
    const { nodes } = useCanvasStore.getState();
    const xs = EXPECTED_CHAIN.map(
      (type) => nodes.find((n) => n.type === type)?.position.x ?? -1,
    );
    for (let i = 1; i < xs.length; i += 1) {
      expect(xs[i]).toBeGreaterThan(xs[i - 1]);
    }
  });

  it("falls back to the right of existing nodes bbox", () => {
    useCanvasStore.setState({
      nodes: [
        {
          id: "existing",
          type: CANVAS_NODE_TYPES.textAnnotation,
          position: { x: 500, y: 40 },
          data: {},
        } as never,
      ],
      edges: [],
    });
    useCanvasStore.getState().spawnR18FactoryPipeline();
    const init = useCanvasStore
      .getState()
      .nodes.find((n) => n.type === CANVAS_NODE_TYPES.nsfwFactoryInit);
    expect(init?.position.x).toBeGreaterThanOrEqual(580);
  });

  it("requests fitView to the new chain after spawn (插入即所见)", () => {
    useCanvasStore.getState().spawnR18FactoryPipeline();
    const { nodes, pendingFitViewNodeIds } = useCanvasStore.getState();
    const chainIds = nodes
      .filter((n) => n.type.startsWith?.("nsfwFactory"))
      .map((n) => n.id);
    expect(pendingFitViewNodeIds).toEqual(chainIds);
  });

  it("consecutive spawns never stack: each lands right of the previous full bbox", () => {
    // 首次插入后 maxX 必然超 4000（链宽 4240px）——此前阈值判定错误导致
    // 后续插入全部叠死在同一位置（2026-08-20 浏览器实测 P1）。现在全量
    // bbox 右侧落位：第二次插入必须比第一次更靠右，绝不重叠。
    useCanvasStore.getState().spawnR18FactoryPipeline();
    const first = useCanvasStore
      .getState()
      .nodes.find((n) => n.type === CANVAS_NODE_TYPES.nsfwFactoryInit);
    expect(first?.position.x).toBe(0); // 空画布 → 原点

    useCanvasStore.getState().spawnR18FactoryPipeline();
    const state = useCanvasStore.getState();
    const inits = state.nodes.filter((n) => n.type === CANVAS_NODE_TYPES.nsfwFactoryInit);
    expect(inits).toHaveLength(2);
    // 第二条链起点 = 第一条链最右节点右缘 + 80（链宽 7×540+460=4240）
    expect(inits[1].position.x).toBeGreaterThanOrEqual(4240 + 80 - 1);
    // 两条链任意节点不得同位
    const xs = state.nodes
      .filter((n) => n.type.startsWith("nsfwFactory"))
      .map((n) => n.position.x);
    expect(new Set(xs).size).toBe(xs.length);
  });

  it("residue at far right: chain lands right of it, fitView keeps it reachable", () => {
    // 离屏残留 x=22000：新链落在残留右侧（可能远），但插入后自动 fitView
    // 会把视口带到新链，「排到视口外找不到」的历史问题已由 fitView 解决。
    useCanvasStore.setState({
      nodes: [
        {
          id: "sane",
          type: CANVAS_NODE_TYPES.textAnnotation,
          position: { x: 500, y: 40 },
          data: {},
        } as never,
        {
          id: "residue",
          type: CANVAS_NODE_TYPES.textAnnotation,
          position: { x: 22000, y: 40 },
          data: {},
        } as never,
      ],
      edges: [],
    });
    useCanvasStore.getState().spawnR18FactoryPipeline();
    const init = useCanvasStore
      .getState()
      .nodes.find((n) => n.type === CANVAS_NODE_TYPES.nsfwFactoryInit);
    expect(init?.position.x).toBeGreaterThanOrEqual(22000 + 80);
    // fitView 请求已发出（视口会自动带到新链）
    expect(useCanvasStore.getState().pendingFitViewNodeIds).toBeTruthy();
  });
});
