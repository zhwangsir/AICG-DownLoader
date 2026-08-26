import { useDramaStore, type CharacterPreviewData } from "./useDramaStore";
import type { ScriptData, SubtitleData, CharacterCardData } from "../api/client";

/**
 * useDramaStore.ts 覆盖率补缺（boost）：
 * 既有 useDramaStore.test.ts 未覆盖局部更新器（updateScriptField/updateCharacter/
 * updateScene/updateSubtitleSegment）、角色预览切片（setCharacterPreview/
 * updateCharacterPreview/updateCharacterPreviewPrompt）、addCharacterCard 与
 * formatSrtTime。本文件逐一补齐，含空剧本/缺键等回退分支。
 */

const sampleScript: ScriptData = {
  project_id: "p1",
  title: "测试短剧",
  genre: "都市悬疑",
  aspect_ratio: "9:16",
  total_episodes: 1,
  characters: [
    { character_id: "c1", name: "Alice", role: "主角", age: 26, description: "主角", personality: "" },
    { character_id: "c2", name: "Bob", role: "反派", age: 40, description: "反派", personality: "冷酷" },
  ],
  scenes: [
    { scene_id: 1, episode: 1, shot_type: "中景", description: "开场", prompt: "", negative_prompt: "", character_actions: "", dialogue: "", emotion: "neutral", duration_seconds: 5, camera_movement: "static" },
    { scene_id: 2, episode: 1, shot_type: "特写", description: "对峙", prompt: "", negative_prompt: "", character_actions: "", dialogue: "", emotion: "tense", duration_seconds: 3, camera_movement: "push" },
  ],
};

const samplePreview = (charId: string): CharacterPreviewData => ({
  character_id: charId,
  character: { character_id: charId, name: "A", role: "r", age: null, description: "d", personality: "p" },
  style: "写实电影感",
  searchReference: "ref",
  generatedPrompts: { front_view_prompt: "gf", side_view_prompt: "gs", closeup_prompt: "gc", negative_prompt: "gn" },
  editedPrompts: { front_view_prompt: "ef", side_view_prompt: "es", closeup_prompt: "ec", negative_prompt: "en" },
  stage: "editing",
});

const sampleCard = (id: string, name: string): CharacterCardData => ({
  character_id: id,
  name,
  reference_images: { front: `http://x/${id}.png` },
  consistency_level: "high",
});

describe("useDramaStore boost — 剧本局部更新器", () => {
  beforeEach(() => {
    useDramaStore.getState().reset();
  });

  it("updateScriptField 局部合并且不清空下游数据", () => {
    const s = useDramaStore.getState();
    s.setScriptData(sampleScript);
    s.addStoryboard({ scene_id: 1, image_url: "u", prompt_used: "" });
    useDramaStore.getState().updateScriptField({ title: "新标题", genre: "科幻" });
    const state = useDramaStore.getState();
    expect(state.scriptData?.title).toBe("新标题");
    expect(state.scriptData?.genre).toBe("科幻");
    expect(state.scriptData?.project_id).toBe("p1"); // 未触碰字段保留
    expect(state.storyboards).toHaveLength(1); // 下游不被清空（与 setScriptData 区分）
  });

  it("updateScriptField 空剧本时 scriptData 保持 null", () => {
    useDramaStore.getState().updateScriptField({ title: "x" });
    expect(useDramaStore.getState().scriptData).toBeNull();
  });

  it("updateCharacter 仅更新指定角色，其余角色不变", () => {
    useDramaStore.getState().setScriptData(sampleScript);
    useDramaStore.getState().updateCharacter("c1", { name: "Alice2", personality: "机敏" });
    const chars = useDramaStore.getState().scriptData!.characters;
    expect(chars[0].name).toBe("Alice2");
    expect(chars[0].personality).toBe("机敏");
    expect(chars[0].role).toBe("主角");
    expect(chars[1].name).toBe("Bob");
  });

  it("updateCharacter 空剧本时 scriptData 保持 null", () => {
    useDramaStore.getState().updateCharacter("c1", { name: "x" });
    expect(useDramaStore.getState().scriptData).toBeNull();
  });

  it("updateScene 仅更新指定场景，其余场景不变", () => {
    useDramaStore.getState().setScriptData(sampleScript);
    useDramaStore.getState().updateScene(2, { description: "高潮对峙", duration_seconds: 8 });
    const scenes = useDramaStore.getState().scriptData!.scenes;
    expect(scenes[1].description).toBe("高潮对峙");
    expect(scenes[1].duration_seconds).toBe(8);
    expect(scenes[1].shot_type).toBe("特写");
    expect(scenes[0].description).toBe("开场");
  });

  it("updateScene 空剧本时 scriptData 保持 null", () => {
    useDramaStore.getState().updateScene(1, { description: "x" });
    expect(useDramaStore.getState().scriptData).toBeNull();
  });
});

describe("useDramaStore boost — updateSubtitleSegment 与 formatSrtTime", () => {
  beforeEach(() => {
    useDramaStore.getState().reset();
  });

  const subtitle: SubtitleData = {
    scene_id: 1,
    srt_content: "旧内容",
    segments: [
      { start: 0, end: 1.5, text: "第一句" },
      { start: 3661.5, end: 3722.25, text: "第二句" },
    ],
    language: "zh",
    srt_url: "u",
  };

  it("更新指定段文本并按 formatSrtTime 重建 srt_content（含小时/毫秒进位）", () => {
    useDramaStore.getState().addSubtitle(subtitle);
    useDramaStore.getState().updateSubtitleSegment(1, 1, "第二句改");
    const sub = useDramaStore.getState().subtitles[0];
    expect(sub.segments[1].text).toBe("第二句改");
    expect(sub.segments[0].text).toBe("第一句");
    // srt_content 由 segments 全量重建（不再保留"旧内容"）
    expect(sub.srt_content).toBe(
      "1\n00:00:00,000 --> 00:00:01,500\n第一句\n\n2\n01:01:01,500 --> 01:02:02,250\n第二句改\n"
    );
  });

  it("scene_id 不匹配时字幕条目原样保留", () => {
    useDramaStore.getState().addSubtitle(subtitle);
    const before = useDramaStore.getState().subtitles[0];
    useDramaStore.getState().updateSubtitleSegment(999, 0, "无效");
    const after = useDramaStore.getState().subtitles[0];
    expect(after).toBe(before); // 未命中条目返回原引用
    expect(after.srt_content).toBe("旧内容");
  });
});

describe("useDramaStore boost — 角色预览切片", () => {
  beforeEach(() => {
    useDramaStore.getState().reset();
  });

  it("setCharacterPreview 写入；置 null 删除该键", () => {
    const s = useDramaStore.getState();
    s.setCharacterPreview("c1", samplePreview("c1"));
    s.setCharacterPreview("c2", samplePreview("c2"));
    expect(Object.keys(useDramaStore.getState().characterPreviews)).toEqual(["c1", "c2"]);
    useDramaStore.getState().setCharacterPreview("c1", null);
    const previews = useDramaStore.getState().characterPreviews;
    expect(previews["c1"]).toBeUndefined();
    expect(previews["c2"]).toBeDefined();
  });

  it("updateCharacterPreview 已存在键：合并补丁", () => {
    useDramaStore.getState().setCharacterPreview("c1", samplePreview("c1"));
    useDramaStore.getState().updateCharacterPreview("c1", { stage: "generating", error: "x" });
    const p = useDramaStore.getState().characterPreviews["c1"];
    expect(p.stage).toBe("generating");
    expect(p.error).toBe("x");
    expect(p.searchReference).toBe("ref"); // 未触碰字段保留
  });

  it("updateCharacterPreview 不存在键：以补丁创建并补 character_id", () => {
    useDramaStore.getState().updateCharacterPreview("ghost", { stage: "searching" });
    const p = useDramaStore.getState().characterPreviews["ghost"];
    expect(p.character_id).toBe("ghost");
    expect(p.stage).toBe("searching");
  });

  it("updateCharacterPreviewPrompt 更新指定视图的编辑提示词", () => {
    useDramaStore.getState().setCharacterPreview("c1", samplePreview("c1"));
    useDramaStore.getState().updateCharacterPreviewPrompt("c1", "front_view_prompt", "新正面提示词");
    const p = useDramaStore.getState().characterPreviews["c1"];
    expect(p.editedPrompts.front_view_prompt).toBe("新正面提示词");
    expect(p.editedPrompts.side_view_prompt).toBe("es"); // 其他视图不动
    expect(p.generatedPrompts.front_view_prompt).toBe("gf"); // 生成稿不动
  });

  it("updateCharacterPreviewPrompt 预览不存在时状态不变", () => {
    const before = useDramaStore.getState().characterPreviews;
    useDramaStore.getState().updateCharacterPreviewPrompt("ghost", "front_view_prompt", "x");
    expect(useDramaStore.getState().characterPreviews).toBe(before); // 提前返回原 state
  });
});

describe("useDramaStore boost — 角色定妆照卡片", () => {
  beforeEach(() => {
    useDramaStore.getState().reset();
  });

  it("addCharacterCard 新增；同 character_id 替换不重复", () => {
    const s = useDramaStore.getState();
    s.addCharacterCard(sampleCard("c1", "Alice"));
    s.addCharacterCard(sampleCard("c2", "Bob"));
    expect(useDramaStore.getState().characterCards).toHaveLength(2);
    useDramaStore.getState().addCharacterCard(sampleCard("c1", "Alice-v2"));
    const cards = useDramaStore.getState().characterCards;
    expect(cards).toHaveLength(2);
    expect(cards.find((c) => c.character_id === "c1")?.name).toBe("Alice-v2");
    expect(cards.find((c) => c.character_id === "c1")?.reference_images.front).toBe("http://x/c1.png");
  });
});
