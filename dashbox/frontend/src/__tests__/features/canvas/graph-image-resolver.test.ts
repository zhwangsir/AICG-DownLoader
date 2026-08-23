// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from "vitest";

import { CANVAS_NODE_TYPES, type CanvasNode } from "@/features/canvas/domain/canvasNodes";
import { extractUpstreamImages } from "@/features/canvas/application/graphImageResolver";

function node(type: string, data: Record<string, unknown>): CanvasNode {
  return {
    id: "n1",
    type,
    position: { x: 0, y: 0 },
    data,
  } as CanvasNode;
}

describe("extractUpstreamImages", () => {
  it("upload/imageEdit/exportImage 节点产出 imageUrl 可提取", () => {
    for (const type of [
      CANVAS_NODE_TYPES.upload,
      CANVAS_NODE_TYPES.imageEdit,
      CANVAS_NODE_TYPES.exportImage,
    ]) {
      expect(extractUpstreamImages(node(type, { imageUrl: "/static/a.png" }))).toEqual([
        "/static/a.png",
      ]);
    }
  });

  it("图片生成类节点（普通/R18）产出图同样可提取（回归：曾漏配致连线无效）", () => {
    for (const type of [CANVAS_NODE_TYPES.imageGen, CANVAS_NODE_TYPES.nsfwImageGen]) {
      expect(
        extractUpstreamImages(node(type, { imageUrl: "/static/gen.png" })),
      ).toEqual(["/static/gen.png"]);
    }
  });

  it("未出图的生成节点返回空（只有连线没有产出仍不解禁）", () => {
    expect(
      extractUpstreamImages(node(CANVAS_NODE_TYPES.nsfwImageGen, { imageUrl: null })),
    ).toEqual([]);
    expect(extractUpstreamImages(node(CANVAS_NODE_TYPES.imageGen, {}))).toEqual([]);
  });

  it("b64 data URI 产出（旧图片节点产物）也可提取", () => {
    expect(
      extractUpstreamImages(
        node(CANVAS_NODE_TYPES.nsfwImageGen, {
          imageUrl: "data:image/png;base64,QUJD",
        }),
      ),
    ).toEqual(["data:image/png;base64,QUJD"]);
  });

  it("无图节点类型（文本/视频等）不产出图片", () => {
    for (const type of [CANVAS_NODE_TYPES.textAnnotation, CANVAS_NODE_TYPES.video]) {
      expect(extractUpstreamImages(node(type, { imageUrl: "/x.png" }))).toEqual([]);
    }
  });

  it("undefined 节点安全返回空", () => {
    expect(extractUpstreamImages(undefined)).toEqual([]);
  });
});
