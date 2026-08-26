import type { Node } from "reactflow";
import { describe, expect, it } from "vitest";
import { getLayoutedElements, NODE_WIDTH, nodeHeight, type DramaNodeData } from "./layout";

function makeNode(id: string, data: Partial<DramaNodeData> = {}): Node<DramaNodeData> {
  return {
    id,
    position: { x: 0, y: 0 },
    data: { label: id, type: "script", detail: "", ...data },
  } as Node<DramaNodeData>;
}

/** 非未来节点（已生成，无额外内容） */
const generated = { hasGenerated: true } as const;
/** 未来节点（未生成/未加载/非输入入口）即默认状态 */

describe("nodeHeight", () => {
  it("start 节点：isScriptInput/默认 → 380", () => {
    expect(nodeHeight(makeNode("start", { isScriptInput: true }))).toBe(380);
    expect(nodeHeight(makeNode("start"))).toBe(380);
  });

  it("start 节点：isEditInput → 110，但被非未来节点最小值钳制到 138", () => {
    expect(nodeHeight(makeNode("start", { isEditInput: true }))).toBe(138);
  });

  it("非 start 的 isScriptInput 节点 → 380", () => {
    expect(nodeHeight(makeNode("idea", { isScriptInput: true }))).toBe(380);
  });

  it("未来节点最小高度 120；非未来节点最小高度 138", () => {
    expect(nodeHeight(makeNode("scene-1", { type: "storyboard" }))).toBeGreaterThanOrEqual(120);
    // 非媒体、无内容的已生成节点 → 精确 138
    expect(nodeHeight(makeNode("quality-final", { type: "quality", ...generated }))).toBe(138);
  });

  it("preview：未来 +28 / 非未来 +42", () => {
    // 86+28=114 → 钳制 120
    expect(nodeHeight(makeNode("scene-1", { type: "quality", preview: "x" }))).toBe(120);
    expect(nodeHeight(makeNode("quality-final", { type: "quality", ...generated, preview: "x" }))).toBe(150);
  });

  it("meta：未来最多计 2 项（+26），非未来按行数（3 项 +48 / 5 项 +68）", () => {
    const meta = (n: number) => Array.from({ length: n }, (_, i) => ({ label: `l${i}`, value: `v${i}` }));
    // 86+26=112 → 120
    expect(nodeHeight(makeNode("scene-1", { type: "quality", meta: meta(5) }))).toBe(120);
    expect(nodeHeight(makeNode("q", { type: "quality", ...generated, meta: meta(3) }))).toBe(156);
    expect(nodeHeight(makeNode("q", { type: "quality", ...generated, meta: meta(5) }))).toBe(176);
  });

  it("tags：未来 +20 / 非未来 +24", () => {
    // 108+24=132 → 钳制 138
    expect(nodeHeight(makeNode("q", { type: "quality", ...generated, tags: ["a"] }))).toBe(138);
    // 加 preview 摆脱钳制：108+42+24=174
    expect(nodeHeight(makeNode("q", { type: "quality", ...generated, tags: ["a"], preview: "p" }))).toBe(174);
  });

  it("imageUrl +132；媒体类型无图时未来 +78 / 非未来 +110（占位块）", () => {
    expect(nodeHeight(makeNode("char-c1", { type: "character", ...generated, imageUrl: "u" }))).toBe(240);
    expect(nodeHeight(makeNode("char-c1", { type: "character" }))).toBe(164); // 86+78
    expect(nodeHeight(makeNode("scene-1", { type: "storyboard", ...generated }))).toBe(218); // 108+110
    // 非媒体类型无图不加占位高度
    expect(nodeHeight(makeNode("voice-1", { type: "voice", ...generated }))).toBe(138);
  });

  it("各内容块高度：videoUrl/audioUrl/subtitleText/qualitySummary/qualityIssues/editablePrompts", () => {
    expect(nodeHeight(makeNode("video-1", { type: "video", ...generated, videoUrl: "v" }))).toBe(108 + 110 + 108);
    expect(nodeHeight(makeNode("voice-1", { type: "voice", ...generated, audioUrl: "a" }))).toBe(108 + 44);
    expect(nodeHeight(makeNode("subtitle-1", { type: "subtitle", ...generated, subtitleText: "s" }))).toBe(108 + 64);
    expect(nodeHeight(makeNode("quality-final", { type: "quality", ...generated, qualitySummary: "q" }))).toBe(108 + 48);
    expect(nodeHeight(makeNode("quality-final", { type: "quality", ...generated, qualityIssues: "i" }))).toBe(138); // 108+30=138
    expect(
      nodeHeight(makeNode("char-c1", { type: "character", ...generated, imageUrl: "u", editablePrompts: { positive: "p", negative: "n" } }))
    ).toBe(240 + 30);
  });

  it("generateLabel：未来 +32 / 非未来 +42；loading 时不加按钮高度改加加载高度", () => {
    expect(nodeHeight(makeNode("scene-1", { type: "quality", generateLabel: "生成" }))).toBe(120); // 86+32=118→钳制
    expect(nodeHeight(makeNode("q", { type: "quality", ...generated, generateLabel: "生成" }))).toBe(150); // 108+42
    // loading 节点不算未来节点：基础 108，generateLabel 高度被跳过，改加 loading 高度后仍被钳制
    expect(nodeHeight(makeNode("scene-1", { type: "quality", generateLabel: "生成", loading: true }))).toBe(138); // 108+18→钳制
    expect(nodeHeight(makeNode("q", { type: "quality", ...generated, generateLabel: "生成", loading: true }))).toBe(138); // 108+18→钳制
  });

  it("组合高度：非未来全内容 = 720", () => {
    const data: Partial<DramaNodeData> = {
      type: "storyboard",
      hasGenerated: true,
      preview: "p",
      meta: [
        { label: "a", value: "1" },
        { label: "b", value: "2" },
        { label: "c", value: "3" },
        { label: "d", value: "4" },
      ],
      tags: ["t1", "t2"],
      imageUrl: "img",
      videoUrl: "v",
      audioUrl: "a",
      subtitleText: "s",
      qualitySummary: "qs",
      qualityIssues: "qi",
      editablePrompts: { positive: "p", negative: "n" },
      generateLabel: "重新生成",
    };
    // 108 + 42 + 48 + 24 + 132 + 108 + 44 + 64 + 48 + 30 + 30 + 42
    expect(nodeHeight(makeNode("scene-1", data))).toBe(720);
  });

  it("组合高度：未来全内容 = 594", () => {
    const data: Partial<DramaNodeData> = {
      type: "character",
      preview: "p",
      meta: [
        { label: "a", value: "1" },
        { label: "b", value: "2" },
        { label: "c", value: "3" },
      ],
      tags: ["t1"],
      videoUrl: "v",
      audioUrl: "a",
      subtitleText: "s",
      qualitySummary: "qs",
      qualityIssues: "qi",
      editablePrompts: { positive: "p", negative: "n" },
      generateLabel: "生成",
    };
    // 86 + 28 + 26 + 20 + 78 + 108 + 44 + 64 + 48 + 30 + 30 + 32
    expect(nodeHeight(makeNode("char-c1", data))).toBe(594);
  });
});

describe("getLayoutedElements", () => {
  const gapY = 44;
  const centerY = 420;
  const colStart = 40;
  const colScript = colStart + NODE_WIDTH + 56; // 376
  const colScene = colScript + NODE_WIDTH + 56; // 712
  const colVideo = colScene + NODE_WIDTH + 56; // 1048
  const colVoice = colVideo + NODE_WIDTH + 56; // 1384
  const colSubtitle = colVoice + NODE_WIDTH + 56; // 1720
  const colEdit = colSubtitle + NODE_WIDTH + 56; // 2056

  it("空输入返回空节点；未知节点落到 (0,0)；edges 原样透传", () => {
    expect(getLayoutedElements([], []).nodes).toEqual([]);
    const edge = { id: "e1", source: "a", target: "b" };
    const { nodes, edges } = getLayoutedElements([makeNode("mystery")], [edge]);
    expect(edges).toEqual([edge]);
    expect(nodes[0].position).toEqual({ x: 0, y: 0 });
    // 预置尺寸（React Flow 测量兜底）
    expect(nodes[0].width).toBe(NODE_WIDTH);
    expect(nodes[0].height).toBe(nodeHeight(makeNode("mystery")));
  });

  it("start/script 横向主流程定位（垂直居中于 centerY）", () => {
    const { nodes } = getLayoutedElements(
      [makeNode("start", { isScriptInput: true }), makeNode("script", { ...generated })],
      []
    );
    const byId = Object.fromEntries(nodes.map((n) => [n.id, n]));
    expect(byId["start"].position).toEqual({ x: colStart, y: centerY - 380 / 2 });
    expect(byId["script"].position).toEqual({ x: colScript, y: centerY - 138 / 2 });
  });

  it("角色节点：2 列网格水平居中于剧本上方，多行向上堆叠", () => {
    const chars = ["char-c1", "char-c2", "char-c3"].map((id) => makeNode(id, { type: "character", ...generated }));
    const { nodes } = getLayoutedElements(
      [makeNode("script", { ...generated }), ...chars],
      []
    );
    const byId = Object.fromEntries(nodes.map((n) => [n.id, n]));
    const scriptCenterX = colScript + NODE_WIDTH / 2; // 516
    const gridWidth = 2 * NODE_WIDTH + 56; // 616
    const gridStartX = scriptCenterX - gridWidth / 2; // 208
    const scriptTopY = centerY - 138 / 2; // 351
    const charTopY = scriptTopY - 110; // 241
    // 角色为媒体类型且无图：高 108+110=218；两行 row0=[c1,c2] row1=[c3]，总高 218+44+218=480
    expect(byId["char-c1"].position).toEqual({ x: gridStartX, y: charTopY - 480 });
    expect(byId["char-c2"].position).toEqual({ x: gridStartX + NODE_WIDTH + 56, y: charTopY - 480 });
    expect(byId["char-c3"].position).toEqual({ x: gridStartX, y: charTopY - 480 + 218 + gapY });
  });

  it("无剧本节点时角色不参与网格布局（落 0,0）", () => {
    const { nodes } = getLayoutedElements([makeNode("char-c1", { type: "character" })], []);
    expect(nodes[0].position).toEqual({ x: 0, y: 0 });
  });

  it("场景链按 scene_id 数值排序并垂直堆叠居中；非法 id 按 0 处理", () => {
    const scenes = ["scene-10", "scene-2", "scene-abc"].map((id) => makeNode(id, { type: "storyboard" }));
    const { nodes } = getLayoutedElements(scenes, []);
    const byId = Object.fromEntries(nodes.map((n) => [n.id, n]));
    // 未来分镜节点含媒体占位：高 86+78=164；总高 164*3+44*2=580，起点 420-290=130
    // 排序：scene-abc(0) → scene-2 → scene-10
    expect(byId["scene-abc"].position).toEqual({ x: colScene, y: 130 });
    expect(byId["scene-2"].position).toEqual({ x: colScene, y: 130 + 164 + gapY });
    expect(byId["scene-10"].position).toEqual({ x: colScene, y: 130 + 2 * (164 + gapY) });
  });

  it("视频/配音/字幕列与对应场景同 Y；未知场景回退 centerY", () => {
    const nodes = [
      makeNode("scene-1", { type: "storyboard" }),
      makeNode("video-1", { type: "video" }),
      makeNode("video-9", { type: "video" }), // 无对应场景
      makeNode("voice-1", { type: "voice" }),
      makeNode("subtitle-1", { type: "subtitle" }),
    ];
    const { nodes: out } = getLayoutedElements(nodes, []);
    const byId = Object.fromEntries(out.map((n) => [n.id, n]));
    const sceneY = centerY - 164 / 2; // 未来分镜高 164，单场景居中 → 338
    expect(byId["video-1"].position).toEqual({ x: colVideo, y: sceneY });
    expect(byId["voice-1"].position).toEqual({ x: colVoice, y: sceneY });
    expect(byId["subtitle-1"].position).toEqual({ x: colSubtitle, y: sceneY });
    expect(byId["video-9"].position).toEqual({ x: colVideo, y: centerY });
  });

  it("成片节点：有字幕时对齐字幕链中点，无字幕时垂直居中", () => {
    const edit = makeNode("edit-final", { type: "edit", ...generated }); // h=138
    const withSubs = getLayoutedElements(
      [
        makeNode("scene-1", { type: "storyboard" }),
        makeNode("scene-2", { type: "storyboard" }),
        makeNode("subtitle-1", { type: "subtitle" }),
        makeNode("subtitle-2", { type: "subtitle" }),
        edit,
      ],
      []
    );
    const byId = Object.fromEntries(withSubs.nodes.map((n) => [n.id, n]));
    // 未来分镜高 164：总高 372，s1.y=234，s2.y=442；字幕同 Y；字幕高 120
    const midY = (234 + 442) / 2 + 120 / 2 - 138 / 2; // 329
    expect(byId["edit-final"].position).toEqual({ x: colEdit, y: midY });

    const withoutSubs = getLayoutedElements([edit], []);
    expect(withoutSubs.nodes[0].position).toEqual({ x: colEdit, y: centerY - 138 / 2 });
  });

  it("质检节点在剧本下方；无剧本时落 (0,0)", () => {
    const withScript = getLayoutedElements(
      [makeNode("script", { ...generated }), makeNode("quality-final", { type: "quality", ...generated })],
      []
    );
    const q = withScript.nodes.find((n) => n.id === "quality-final")!;
    expect(q.position).toEqual({ x: colScript, y: centerY + 138 / 2 + 110 });

    const withoutScript = getLayoutedElements([makeNode("quality-final", { type: "quality" })], []);
    expect(withoutScript.nodes[0].position).toEqual({ x: 0, y: 0 });
  });

  it("视觉质检节点在最后一个视频（按 id 排序）下方；无视频时落 (0,0)", () => {
    const nodes = [
      makeNode("scene-2", { type: "storyboard" }),
      makeNode("scene-10", { type: "storyboard" }),
      makeNode("video-10", { type: "video" }),
      makeNode("video-2", { type: "video" }),
      makeNode("visual-quality-final", { type: "visual_quality" }),
    ];
    const { nodes: out } = getLayoutedElements(nodes, []);
    const byId = Object.fromEntries(out.map((n) => [n.id, n]));
    // 未来分镜高 164：总高 372，scene-2.y=234，scene-10.y=442；未来视频（无图）高 164
    expect(byId["visual-quality-final"].position).toEqual({ x: colVideo, y: 442 + 164 + 72 });

    const noVideo = getLayoutedElements([makeNode("visual-quality-final", { type: "visual_quality" })], []);
    expect(noVideo.nodes[0].position).toEqual({ x: 0, y: 0 });
  });

  it("分支回退：字幕/成片/视觉质检在场景 Y 缺失时回退 centerY", () => {
    // 字幕无对应场景 → y 回退 centerY
    const subOnly = getLayoutedElements([makeNode("subtitle-9", { type: "subtitle" })], []);
    expect(subOnly.nodes[0].position).toEqual({ x: 1720, y: 420 });

    // 有字幕但无场景 → 成片 midY 的首尾 Y 均回退 centerY
    const editNoScene = getLayoutedElements(
      [makeNode("subtitle-1", { type: "subtitle" }), makeNode("edit-final", { type: "edit", ...generated })],
      []
    );
    const editNode = editNoScene.nodes.find((n) => n.id === "edit-final")!;
    // (420+420)/2 + 120/2 - 138/2
    expect(editNode.position).toEqual({ x: 2056, y: 420 + 60 - 69 });

    // 有视频但无场景 → 视觉质检基准 Y 回退 centerY（视频为媒体类型无图，未来高 164）
    const vqNoScene = getLayoutedElements(
      [makeNode("video-5", { type: "video" }), makeNode("visual-quality-final", { type: "visual_quality" })],
      []
    );
    const vq = vqNoScene.nodes.find((n) => n.id === "visual-quality-final")!;
    expect(vq.position).toEqual({ x: 1048, y: 420 + 164 + 72 });
  });

  it("分支回退：非数字 id 后缀按 0 处理（voice/video/subtitle/vq 排序）", () => {
    const { nodes } = getLayoutedElements(
      [
        makeNode("voice-abc", { type: "voice" }),
        makeNode("video-abc", { type: "video" }),
        makeNode("video-def", { type: "video" }),
        makeNode("subtitle-xyz", { type: "subtitle" }),
        makeNode("edit-final", { type: "edit", ...generated }),
        makeNode("visual-quality-final", { type: "visual_quality" }),
      ],
      []
    );
    const byId = Object.fromEntries(nodes.map((n) => [n.id, n]));
    // sid=0，场景 Y 表为空 → 全部回退 centerY；两个非数字视频触发排序比较器 || 0 分支
    expect(byId["voice-abc"].position.y).toBe(420);
    expect(byId["video-abc"].position.y).toBe(420);
    expect(byId["subtitle-xyz"].position.y).toBe(420);
    // edit：(420+420)/2 + 120/2 - 138/2
    expect(byId["edit-final"].position.y).toBe(411);
    // vq：末视频基准 420 + 视频高 164 + 72
    expect(byId["visual-quality-final"].position.y).toBe(420 + 164 + 72);
  });
});
