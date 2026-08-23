// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// 工序④ 数字资产：分镜行 → 待生成资产清单提取（角色描述回退链 + 场景去重）。
import { describe, expect, it } from "vitest";

import type {
  NSFWFactoryScriptNodeData,
  NSFWFactoryStoryboardRow,
} from "@/features/canvas/domain/canvasNodes";
import { buildPendingAssets } from "@/features/canvas/nodes/NSFWFactoryAssetNode";

function row(shotNo: number, patch: Partial<NSFWFactoryStoryboardRow> = {}): NSFWFactoryStoryboardRow {
  return {
    shotNo,
    episodeNo: 1,
    kind: "plot",
    shotSize: "中景",
    cameraMove: "固定",
    imagePrompt: "1girl, masterpiece",
    videoPrompt: "",
    dialogue: "",
    narration: "",
    emotion: "平静",
    durationSec: 5,
    presetId: "",
    audio: "tts",
    actionDesc: "",
    expression: "",
    sceneDesc: "",
    ...patch,
  };
}

function script(charactersText: string): NSFWFactoryScriptNodeData {
  return {
    displayName: "剧本",
    synopsis: "",
    charactersText,
    episodeCount: 1,
    planTitle: "",
    episodes: [],
  } as NSFWFactoryScriptNodeData;
}

describe("buildPendingAssets（分镜驱动的资产提取）", () => {
  it("角色按对白「名字：」前缀出场序去重，描述精确回填角色卡", () => {
    const out = buildPendingAssets(
      [
        row(1, { dialogue: "林薇：你来了。" }),
        row(2, { dialogue: "陈默：嗯。" }),
        row(3, { dialogue: "林薇：坐吧。" }),
      ],
      script("林薇：28岁女性，黑色长直发\n陈默：30岁男性，短发"),
    );
    const characters = out.filter((it) => it.kind === "character");
    expect(characters.map((c) => c.name)).toEqual(["林薇", "陈默"]);
    expect(characters[0].desc).toBe("28岁女性，黑色长直发");
    expect(characters[1].desc).toBe("30岁男性，短发");
  });

  it("角色卡精确匹配失败时按包含匹配（对白「林薇姐」↔ 卡「林薇」）", () => {
    const out = buildPendingAssets(
      [row(1, { dialogue: "林薇姐：请坐。" })],
      script("林薇：28岁女性，黑色长直发"),
    );
    expect(out[0].name).toBe("林薇姐");
    expect(out[0].desc).toBe("28岁女性，黑色长直发");
  });

  it("角色卡缺失时回退出场行 image_prompt（英文锚点）", () => {
    const out = buildPendingAssets(
      [row(1, { dialogue: "小美：你好。", imagePrompt: "1girl, ponytail, school uniform" })],
      script(""),
    );
    expect(out[0].desc).toBe("1girl, ponytail, school uniform");
  });

  it("场景按 scene_desc 去重，名称取前 12 字", () => {
    const longDesc = "酒店卧室·夜·暖光台灯与落地窗";
    const out = buildPendingAssets(
      [row(1, { sceneDesc: longDesc }), row(2, { sceneDesc: longDesc }), row(3, { sceneDesc: "浴室·晨" })],
      null,
    );
    const scenes = out.filter((it) => it.kind === "scene");
    expect(scenes).toHaveLength(2);
    expect(scenes[0].name).toBe(longDesc.slice(0, 12));
    expect(scenes[1].desc).toBe("浴室·晨");
  });

  it("无对白前缀且无场景描述的行不产出资产", () => {
    const out = buildPendingAssets(
      [row(1, { dialogue: "没有名字前缀的台词", sceneDesc: "" })],
      script(""),
    );
    expect(out).toHaveLength(0);
  });
});
