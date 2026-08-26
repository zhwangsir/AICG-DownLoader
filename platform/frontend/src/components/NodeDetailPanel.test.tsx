import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { vi } from "vitest";
import NodeDetailPanel from "./NodeDetailPanel";
import { useDramaStore } from "../store/useDramaStore";
import type { CharacterData, SceneData, ScriptData, SubtitleData } from "../api/client";
import type { DramaNodeData } from "./canvas/layout";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    agentAssist: vi.fn(),
  };
});

import { agentAssist } from "../api/client";
const mockAssist = vi.mocked(agentAssist);

const character: CharacterData = {
  character_id: "c1",
  name: "林远",
  role: "主角",
  age: 26,
  description: "高大",
  personality: "冷静",
};

const scene: SceneData = {
  scene_id: 1,
  episode: 1,
  shot_type: "近景",
  description: "雨夜街头",
  prompt: "rainy night street",
  negative_prompt: "low quality",
  character_actions: "撑伞独行",
  dialogue: "你来了。",
  emotion: "压抑",
  duration_seconds: 5,
  camera_movement: "推镜",
};

const scene2: SceneData = { ...scene, scene_id: 2, description: "白天咖啡馆" };

const subtitle: SubtitleData = {
  scene_id: 1,
  srt_content: "",
  segments: [
    { start: 65.5, end: 70.25, text: "第一句字幕" },
    { start: 0, end: 2.5, text: "第二句字幕" },
  ],
  language: "zh",
  srt_url: "",
};

function makeScript(patch: Partial<ScriptData> = {}): ScriptData {
  return {
    project_id: "p1",
    title: "测试短剧",
    genre: "都市情感",
    aspect_ratio: "9:16",
    total_episodes: 2,
    characters: [character],
    scenes: [scene, scene2],
    ...patch,
  };
}

function renderPanel(props: Partial<Parameters<typeof NodeDetailPanel>[0]> = {}) {
  const onClose = vi.fn();
  const utils = render(
    <NodeDetailPanel nodeId="script" type="script" onClose={onClose} {...props} />
  );
  return { onClose, ...utils };
}

function deferred<T>() {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

describe("NodeDetailPanel — 头部与数据卡", () => {
  beforeEach(() => {
    useDramaStore.getState().reset();
    vi.clearAllMocks();
  });

  it("未知类型回退标题「节点详情」；已知类型显示映射标题", () => {
    const { unmount } = renderPanel({ type: "unknown" });
    expect(screen.getByText("节点详情")).toBeInTheDocument();
    unmount();
    renderPanel({ type: "video", nodeId: "video-1" });
    expect(screen.getByText("视频生成详情")).toBeInTheDocument();
  });

  it("收起与取消按钮均触发 onClose；面板点击不冒泡", () => {
    const onClose = vi.fn();
    const { container } = render(
      <NodeDetailPanel nodeId="script" type="script" onClose={onClose} />
    );
    fireEvent.click(screen.getByText("收起"));
    fireEvent.click(screen.getByText("取消"));
    expect(onClose).toHaveBeenCalledTimes(2);
    // 面板根节点 stopPropagation：点击内容区不向外冒泡
    const panel = container.querySelector(".side-panel")!;
    const stopSpy = vi.spyOn(Event.prototype, "stopPropagation");
    fireEvent.click(panel);
    expect(stopSpy).toHaveBeenCalled();
    stopSpy.mockRestore();
  });

  it("data 卡片：状态点/自定义状态文本/tags/meta/preview 全部渲染", () => {
    const data: DramaNodeData = {
      label: "分镜1",
      type: "storyboard",
      detail: "",
      statusText: "自定义状态",
      tags: ["2K", "H3"],
      meta: [
        { label: "时长", value: "5s" },
        { label: "画风", value: "写实" },
      ],
      preview: "雨夜街头的预览文本",
    };
    renderPanel({ type: "storyboard", nodeId: "storyboard-1", data });
    expect(screen.getByText("自定义状态")).toBeInTheDocument();
    expect(screen.getByText("2K")).toBeInTheDocument();
    expect(screen.getByText("H3")).toBeInTheDocument();
    expect(screen.getByText("时长")).toBeInTheDocument();
    expect(screen.getByText("5s")).toBeInTheDocument();
    expect(screen.getByText("雨夜街头的预览文本")).toBeInTheDocument();
  });

  it("data 卡片默认状态文本：hasGenerated→已完成 / loading→生成中 / 其他→待开始", () => {
    const base: DramaNodeData = { label: "n", type: "video", detail: "" };
    const { unmount: u1 } = renderPanel({ type: "video", nodeId: "video-1", data: { ...base, hasGenerated: true } });
    expect(screen.getByText("已完成")).toBeInTheDocument();
    u1();
    const { unmount: u2 } = renderPanel({ type: "video", nodeId: "video-1", data: { ...base, loading: true } });
    expect(screen.getByText("生成中")).toBeInTheDocument();
    u2();
    renderPanel({ type: "video", nodeId: "video-1", data: base });
    expect(screen.getByText("待开始")).toBeInTheDocument();
  });

  it("data 卡片：有 meta 无 preview 时正常渲染（无底部间距分支）", () => {
    const data: DramaNodeData = {
      label: "n",
      type: "video",
      detail: "",
      meta: [{ label: "时长", value: "5s" }],
    };
    renderPanel({ type: "video", nodeId: "video-1", data });
    expect(screen.getByText("时长")).toBeInTheDocument();
  });

  it("edit / quality / visual_quality 类型：展示只读说明文本", () => {
    const { unmount: u1 } = renderPanel({ type: "edit", nodeId: "edit" });
    expect(screen.getByText(/成片节点仅展示最终合成结果/)).toBeInTheDocument();
    u1();
    const { unmount: u2 } = renderPanel({ type: "quality", nodeId: "quality" });
    expect(screen.getByText(/质检节点展示剧本与字幕/)).toBeInTheDocument();
    u2();
    renderPanel({ type: "visual_quality", nodeId: "vq" });
    expect(screen.getByText(/视觉质检节点展示视频画面/)).toBeInTheDocument();
  });
});

describe("NodeDetailPanel — 剧本节点（已有剧本）", () => {
  beforeEach(() => {
    useDramaStore.getState().reset();
    vi.clearAllMocks();
    useDramaStore.getState().setScriptData(makeScript());
  });

  it("渲染标题/题材并可编辑保存；集数与每集分镜数回显", () => {
    const { onClose } = renderPanel();
    expect(screen.getByText("剧本详情")).toBeInTheDocument();
    // 回显：total_episodes=2，scenes=2 → 每集 1
    expect(screen.getByDisplayValue("2")).toBeInTheDocument();
    expect(screen.getByDisplayValue("1")).toBeInTheDocument();
    expect(screen.getByText(/修改后点击「保存并重新生成」生效/)).toBeInTheDocument();

    fireEvent.change(screen.getByDisplayValue("测试短剧"), { target: { value: "新标题" } });
    fireEvent.change(screen.getByDisplayValue("都市情感"), { target: { value: "悬疑" } });
    fireEvent.click(screen.getByText("保存修改"));
    const sd = useDramaStore.getState().scriptData!;
    expect(sd.title).toBe("新标题");
    expect(sd.genre).toBe("悬疑");
    expect(onClose).toHaveBeenCalled();
  });

  it("scriptData 变化时表单同步更新", () => {
    renderPanel();
    act(() => {
      useDramaStore.getState().setScriptData(makeScript({ title: "全新剧名", total_episodes: 4 }));
    });
    expect(screen.getByDisplayValue("全新剧名")).toBeInTheDocument();
    expect(screen.getByDisplayValue("4")).toBeInTheDocument();
  });

  it("无场景时每集分镜数保留原值", () => {
    act(() => {
      useDramaStore.getState().setScriptData(makeScript({ scenes: [], total_episodes: 3 }));
    });
    renderPanel();
    expect(screen.getByDisplayValue("3")).toBeInTheDocument();
  });

  it("集数/分镜数输入：空字符串保留、越界钳制、0 回退 1", () => {
    renderPanel();
    const [episodesInput, scenesInput] = screen.getAllByPlaceholderText("请输入") as HTMLInputElement[];
    expect(episodesInput.value).toBe("2");
    expect(scenesInput.value).toBe("1");
    fireEvent.change(episodesInput, { target: { value: "" } });
    expect(episodesInput.value).toBe("");
    fireEvent.change(episodesInput, { target: { value: "500" } });
    expect(episodesInput.value).toBe("100");
    fireEvent.change(episodesInput, { target: { value: "0" } });
    expect(episodesInput.value).toBe("1");

    fireEvent.change(scenesInput, { target: { value: "99" } });
    expect(scenesInput.value).toBe("30");
    fireEvent.change(scenesInput, { target: { value: "0" } });
    expect(scenesInput.value).toBe("1");
  });

  it("视觉风格与画幅下拉可选择", () => {
    renderPanel();
    const selects = screen.getAllByRole("combobox");
    fireEvent.change(selects[0], { target: { value: "赛博朋克" } });
    fireEvent.change(selects[1], { target: { value: "16:9" } });
    expect((selects[0] as HTMLSelectElement).value).toBe("赛博朋克");
    expect((selects[1] as HTMLSelectElement).value).toBe("16:9");
  });

  it("无 onGenerate 时不渲染「保存并重新生成」", () => {
    renderPanel();
    expect(screen.getByText("保存修改")).toBeInTheDocument();
    expect(screen.queryByText("保存并重新生成")).not.toBeInTheDocument();
  });

  it("globalLoading 时「保存并重新生成」显示「生成中...」且禁用", () => {
    useDramaStore.getState().startGlobalLoading("跑管线");
    renderPanel({ onGenerate: vi.fn() });
    expect(screen.getByText("生成中...")).toBeDisabled();
  });

  it("每集分镜数可清空为空字符串", () => {
    renderPanel();
    const scenesInput = screen.getAllByPlaceholderText("请输入")[1] as HTMLInputElement;
    fireEvent.change(scenesInput, { target: { value: "" } });
    expect(scenesInput.value).toBe("");
  });

  it("scriptData.genre/total_episodes 为空时 ideaForm 保留原值", () => {
    act(() => {
      useDramaStore.getState().setScriptData(makeScript({ genre: "", total_episodes: 0 }));
    });
    renderPanel();
    const [episodesInput] = screen.getAllByPlaceholderText("请输入") as HTMLInputElement[];
    // total_episodes=0 为 falsy → 保留 prev（初始 ""）
    expect(episodesInput.value).toBe("");
  });

  it("「保存并重新生成」：已有剧本态跳过 premise 校验，表单合法时保存并触发重新生成（bug 修复回归）", () => {
    const onGenerate = vi.fn();
    renderPanel({ onGenerate });
    // 补齐 style/aspect_ratio（genre/episodes/scenes_per_episode 由 scriptData 回显注入）
    const selects = screen.getAllByRole("combobox") as HTMLSelectElement[];
    fireEvent.change(selects[0], { target: { value: "赛博朋克" } });
    fireEvent.change(selects[1], { target: { value: "16:9" } });
    // 改标题验证 handleSave 同步生效
    fireEvent.change(screen.getByDisplayValue("测试短剧"), { target: { value: "重生之都市传说" } });

    fireEvent.click(screen.getByText("保存并重新生成"));

    expect(screen.queryByText("请输入一句话创意")).not.toBeInTheDocument();
    expect(useDramaStore.getState().scriptData!.title).toBe("重生之都市传说");
    expect(onGenerate).toHaveBeenCalledTimes(1);
    expect(onGenerate).toHaveBeenCalledWith(
      expect.objectContaining({
        genre: "都市情感",
        episodes: 2,
        scenes_per_episode: 1,
        style: "赛博朋克",
        aspect_ratio: "16:9",
      })
    );
  });

  it("「保存并重新生成」：skipPremise 仅豁免创意，集数为空仍报错且不调用 onGenerate", () => {
    const onGenerate = vi.fn();
    renderPanel({ onGenerate });
    const selects = screen.getAllByRole("combobox") as HTMLSelectElement[];
    fireEvent.change(selects[0], { target: { value: "赛博朋克" } });
    fireEvent.change(selects[1], { target: { value: "16:9" } });
    // 清空集数（ScriptGlobalControls 直写 ideaForm.episodes）
    const [episodesInput] = screen.getAllByPlaceholderText("请输入") as HTMLInputElement[];
    fireEvent.change(episodesInput, { target: { value: "" } });

    fireEvent.click(screen.getByText("保存并重新生成"));

    expect(screen.getByText("请设置集数")).toBeInTheDocument();
    expect(onGenerate).not.toHaveBeenCalled();
  });
});

describe("NodeDetailPanel — 剧本节点（无剧本，创意表单）", () => {
  beforeEach(() => {
    useDramaStore.getState().reset();
    vi.clearAllMocks();
  });

  it("渲染创意表单；逐项校验错误依次提示", () => {
    const onGenerate = vi.fn();
    renderPanel({ onGenerate });
    expect(screen.getByText("一句话创意")).toBeInTheDocument();
    const genBtn = screen.getByText("生成剧本");

    // 1. premise 空
    fireEvent.click(genBtn);
    expect(screen.getByText("请输入一句话创意")).toBeInTheDocument();

    const premise = screen.getByText("一句话创意").nextElementSibling as HTMLTextAreaElement;
    fireEvent.change(premise, { target: { value: "落魄拳王重出江湖" } });

    // 2. genre 空
    fireEvent.click(genBtn);
    expect(screen.getByText("请输入题材")).toBeInTheDocument();
    const genre = screen.getByText("题材").nextElementSibling as HTMLInputElement;
    fireEvent.change(genre, { target: { value: "励志" } });

    // 3. episodes 空
    fireEvent.click(genBtn);
    expect(screen.getByText("请设置集数")).toBeInTheDocument();
    const nums = screen.getAllByPlaceholderText("请输入");
    fireEvent.change(nums[0], { target: { value: "3" } });

    // 4. scenes_per_episode 空
    fireEvent.click(genBtn);
    expect(screen.getByText("请设置每集分镜数")).toBeInTheDocument();
    fireEvent.change(nums[1], { target: { value: "5" } });

    // 5. style 未选（错误文案与下拉占位 option 同文，需用 getAllByText 区分）
    fireEvent.click(genBtn);
    expect(screen.getAllByText("请选择视觉风格").length).toBe(2);
    const selects = screen.getAllByRole("combobox");
    fireEvent.change(selects[0], { target: { value: "写实电影感" } });

    // 6. aspect_ratio 未选
    fireEvent.click(genBtn);
    expect(screen.getAllByText("请选择画幅比例").length).toBe(2);
    fireEvent.change(selects[1], { target: { value: "9:16" } });

    // 全部通过 → onGenerate + onClose
    fireEvent.click(genBtn);
    expect(onGenerate).toHaveBeenCalledWith({
      premise: "落魄拳王重出江湖",
      genre: "励志",
      episodes: 3,
      scenes_per_episode: 5,
      style: "写实电影感",
      aspect_ratio: "9:16",
    });
    expect(screen.queryByText("请输入一句话创意")).not.toBeInTheDocument();
  });

  it("globalLoading 时生成按钮禁用并显示「生成中...」", () => {
    useDramaStore.getState().startGlobalLoading("跑管线");
    renderPanel({ onGenerate: vi.fn() });
    const btn = screen.getByText("生成中...");
    expect(btn).toBeDisabled();
  });

  it("无 onGenerate 时也不报错（可选回调）", () => {
    renderPanel();
    expect(screen.getByText("生成剧本")).toBeInTheDocument();
  });

  it("创意表单字段编辑", () => {
    renderPanel();
    const premise = screen.getByText("一句话创意").nextElementSibling as HTMLTextAreaElement;
    fireEvent.change(premise, { target: { value: "创意内容" } });
    expect(premise.value).toBe("创意内容");
    const genre = screen.getByText("题材").nextElementSibling as HTMLInputElement;
    fireEvent.change(genre, { target: { value: "古装" } });
    expect(genre.value).toBe("古装");
  });
});

describe("NodeDetailPanel — 角色节点", () => {
  beforeEach(() => {
    useDramaStore.getState().reset();
    vi.clearAllMocks();
    useDramaStore.getState().setScriptData(makeScript());
  });

  it("渲染角色表单，编辑并保存写回 store", () => {
    const { onClose } = renderPanel({ type: "character", nodeId: "char-c1" });
    expect(screen.getByText("角色详情")).toBeInTheDocument();

    fireEvent.change(screen.getByDisplayValue("林远"), { target: { value: "林远山" } });
    fireEvent.change(screen.getByDisplayValue("主角"), { target: { value: "卧底" } });
    const ageInput = screen.getByDisplayValue("26");
    fireEvent.change(ageInput, { target: { value: "" } });
    expect((ageInput as HTMLInputElement).value).toBe("");
    fireEvent.change(ageInput, { target: { value: "33" } });
    fireEvent.change(screen.getByDisplayValue("高大"), { target: { value: "瘦削" } });
    fireEvent.change(screen.getByDisplayValue("冷静"), { target: { value: "暴烈" } });

    fireEvent.click(screen.getByText("保存修改"));
    const c = useDramaStore.getState().scriptData!.characters[0];
    expect(c).toMatchObject({ name: "林远山", role: "卧底", age: 33, description: "瘦削", personality: "暴烈" });
    expect(onClose).toHaveBeenCalled();
  });

  it("角色不存在时不渲染角色表单区", () => {
    renderPanel({ type: "character", nodeId: "char-nope" });
    expect(screen.queryByText("角色名")).not.toBeInTheDocument();
    // 保存按钮仍在，点击仅关闭
    fireEvent.click(screen.getByText("保存修改"));
  });
});

describe("NodeDetailPanel — 场景类节点（storyboard/video/voice）", () => {
  beforeEach(() => {
    useDramaStore.getState().reset();
    vi.clearAllMocks();
    useDramaStore.getState().setScriptData(makeScript());
  });

  it("storyboard：全字段编辑并保存", () => {
    renderPanel({ type: "storyboard", nodeId: "storyboard-1" });
    expect(screen.getByText("分镜详情")).toBeInTheDocument();
    expect(screen.getByText("场景 1")).toBeInTheDocument();

    const shotSelect = screen.getByDisplayValue("近景");
    fireEvent.change(shotSelect, { target: { value: "特写" } });
    fireEvent.change(screen.getByDisplayValue("雨夜街头"), { target: { value: "新画面" } });
    fireEvent.change(screen.getByDisplayValue("撑伞独行"), { target: { value: "奔跑" } });
    fireEvent.change(screen.getByDisplayValue("压抑"), { target: { value: "释然" } });
    fireEvent.change(screen.getByDisplayValue("推镜"), { target: { value: "摇镜" } });
    fireEvent.change(screen.getByDisplayValue("rainy night street"), { target: { value: "new prompt" } });
    fireEvent.change(screen.getByDisplayValue("low quality"), { target: { value: "new negative" } });
    const duration = screen.getByDisplayValue("5");
    fireEvent.change(duration, { target: { value: "8" } });

    fireEvent.click(screen.getByText("保存修改"));
    const s = useDramaStore.getState().scriptData!.scenes[0];
    expect(s).toMatchObject({
      shot_type: "特写",
      description: "新画面",
      character_actions: "奔跑",
      emotion: "释然",
      camera_movement: "摇镜",
      prompt: "new prompt",
      negative_prompt: "new negative",
      duration_seconds: 8,
    });
  });

  it("storyboard：时长非法输入回退 0", () => {
    renderPanel({ type: "storyboard", nodeId: "storyboard-1" });
    const duration = screen.getByDisplayValue("5");
    fireEvent.change(duration, { target: { value: "abc" } });
    fireEvent.click(screen.getByText("保存修改"));
    expect(useDramaStore.getState().scriptData!.scenes[0].duration_seconds).toBe(0);
  });

  it("video：无 storyboard 专属字段，有公共提示词字段", () => {
    renderPanel({ type: "video", nodeId: "video-1" });
    expect(screen.getByText("视频生成详情")).toBeInTheDocument();
    expect(screen.queryByText("镜头类型")).not.toBeInTheDocument();
    expect(screen.getByText("正面提示词")).toBeInTheDocument();
    expect(screen.queryByText("场景台词")).not.toBeInTheDocument();
  });

  it("voice：含场景台词编辑，保存写回 dialogue", () => {
    renderPanel({ type: "voice", nodeId: "voice-1" });
    expect(screen.getByText("配音详情")).toBeInTheDocument();
    fireEvent.change(screen.getByDisplayValue("你来了。"), { target: { value: "新台词" } });
    fireEvent.click(screen.getByText("保存修改"));
    expect(useDramaStore.getState().scriptData!.scenes[0].dialogue).toBe("新台词");
  });

  it("场景不存在时不渲染场景表单区", () => {
    renderPanel({ type: "storyboard", nodeId: "storyboard-99" });
    expect(screen.queryByText("场景编号")).not.toBeInTheDocument();
  });
});

describe("NodeDetailPanel — 字幕节点", () => {
  beforeEach(() => {
    useDramaStore.getState().reset();
    vi.clearAllMocks();
    useDramaStore.getState().setScriptData(makeScript());
    useDramaStore.getState().addSubtitle(subtitle);
  });

  it("渲染字幕段（formatTime 时间轴），编辑并保存逐段写回", () => {
    renderPanel({ type: "subtitle", nodeId: "subtitle-1" });
    expect(screen.getByText("字幕详情")).toBeInTheDocument();
    expect(screen.getByText("字幕段数：2")).toBeInTheDocument();
    // formatTime: 65.5 → 01:05.500；0→1.001 → 00:01.001
    expect(screen.getByText(/#1 01:05\.500 → 01:10\.250/)).toBeInTheDocument();
    expect(screen.getByText(/#2 00:00\.000 → 00:02\.500/)).toBeInTheDocument();

    fireEvent.change(screen.getByDisplayValue("第一句字幕"), { target: { value: "改后字幕" } });
    fireEvent.click(screen.getByText("保存修改"));
    const segs = useDramaStore.getState().subtitles[0].segments;
    expect(segs[0].text).toBe("改后字幕");
    expect(segs[1].text).toBe("第二句字幕");
  });

  it("字幕不存在时不渲染字幕区", () => {
    useDramaStore.getState().reset();
    useDramaStore.getState().setScriptData(makeScript());
    renderPanel({ type: "subtitle", nodeId: "subtitle-1" });
    expect(screen.queryByText(/字幕段数/)).not.toBeInTheDocument();
  });
});

describe("NodeDetailPanel — AgentAssistToolbar 智能体辅助", () => {
  beforeEach(() => {
    useDramaStore.getState().reset();
    vi.clearAllMocks();
    useDramaStore.getState().setScriptData(makeScript());
  });

  it("点击「润色」调用 agentAssist 并应用返回文本", async () => {
    mockAssist.mockResolvedValue({ success: true, data: { text: "AI润色后的名字", action: "polish", context: "character" } });
    renderPanel({ type: "character", nodeId: "char-c1" });
    const polishBtns = screen.getAllByText("润色");
    fireEvent.click(polishBtns[0]);
    await waitFor(() =>
      expect(mockAssist).toHaveBeenCalledWith({
        text: "林远",
        context: "character",
        action: "polish",
        extra_instruction: "角色名更贴合题材与性格",
      })
    );
    await waitFor(() => expect(screen.getByDisplayValue("AI润色后的名字")).toBeInTheDocument());
  });

  it("扩写/精简/改写按钮均触发对应 action", async () => {
    mockAssist.mockResolvedValue({ success: true, data: { text: "结果", action: "expand", context: "character" } });
    renderPanel({ type: "character", nodeId: "char-c1" });
    fireEvent.click(screen.getAllByText("扩写")[0]);
    await waitFor(() => expect(mockAssist).toHaveBeenCalledWith(expect.objectContaining({ action: "expand" })));
    fireEvent.click(screen.getAllByText("精简")[0]);
    await waitFor(() => expect(mockAssist).toHaveBeenCalledWith(expect.objectContaining({ action: "shorten" })));
    fireEvent.click(screen.getAllByText("改写")[0]);
    await waitFor(() => expect(mockAssist).toHaveBeenCalledWith(expect.objectContaining({ action: "rewrite" })));
  });

  it("assist 失败（success:false）时不修改原文本", async () => {
    mockAssist.mockResolvedValue({ success: false, error: "LLM 离线" });
    renderPanel({ type: "character", nodeId: "char-c1" });
    fireEvent.click(screen.getAllByText("润色")[0]);
    await waitFor(() => expect(mockAssist).toHaveBeenCalled());
    expect(screen.getByDisplayValue("林远")).toBeInTheDocument();
  });

  it("assist 进行中：当前工具条按钮全部禁用并显示加载图标", async () => {
    const d = deferred<{ success: boolean; data: { text: string; action: string; context: string } }>();
    mockAssist.mockReturnValue(d.promise);
    renderPanel({ type: "character", nodeId: "char-c1" });
    const firstToolbar = screen.getAllByText("润色")[0].closest("div")!;
    fireEvent.click(screen.getAllByText("润色")[0]);
    await waitFor(() => {
      const btns = Array.from(firstToolbar.querySelectorAll("button"));
      expect(btns).toHaveLength(4);
      expect(btns.every((b) => b.disabled)).toBe(true);
    });
    // 加载态渲染 Loader2 图标（spin 动画）
    expect(firstToolbar.querySelector("svg.lucide-loader-circle, svg.lucide-loader")).toBeInTheDocument();
    await act(async () => d.resolve({ success: true, data: { text: "x", action: "polish", context: "character" } }));
  });

  it("悬停启用按钮切换高亮样式，移出还原", () => {
    renderPanel({ type: "character", nodeId: "char-c1" });
    const btn = screen.getAllByText("润色")[0].closest("button")!;
    fireEvent.mouseEnter(btn);
    expect(btn.style.color).toBe("var(--primary)");
    fireEvent.mouseLeave(btn);
    expect(btn.style.borderColor).toBe("var(--border-light)");
  });

  it("字幕工具条应用结果写回对应段", async () => {
    useDramaStore.getState().addSubtitle(subtitle);
    mockAssist.mockResolvedValue({ success: true, data: { text: "精简后字幕", action: "shorten", context: "subtitle" } });
    renderPanel({ type: "subtitle", nodeId: "subtitle-1" });
    fireEvent.click(screen.getAllByText("精简")[0]);
    await waitFor(() => expect(screen.getByDisplayValue("精简后字幕")).toBeInTheDocument());
  });

  it("全部字段工具条 onApply 覆盖（script 两态 / character / storyboard / video / voice）", async () => {
    let n = 0;
    mockAssist.mockImplementation(async () => ({
      success: true,
      data: { text: `AI-${n++}`, action: "polish", context: "x" },
    }));
    const clickAndExpect = async (btnIdx: number, expected: string) => {
      fireEvent.click(screen.getAllByText("润色")[btnIdx]);
      await waitFor(() => expect(screen.getByDisplayValue(expected)).toBeInTheDocument());
    };

    // 剧本（已有剧本）：标题(0)、题材(1)
    const u1 = renderPanel().unmount;
    await clickAndExpect(0, "AI-0");
    await clickAndExpect(1, "AI-1");
    u1();

    // 剧本（无剧本创意表单）：premise(0) 初始为空 → 按钮禁用；先填值再点
    act(() => useDramaStore.getState().reset());
    const u2 = renderPanel().unmount;
    const premise = screen.getByText("一句话创意").nextElementSibling as HTMLTextAreaElement;
    fireEvent.change(premise, { target: { value: "原始创意" } });
    const genre = screen.getByText("题材").nextElementSibling as HTMLInputElement;
    fireEvent.change(genre, { target: { value: "原始题材" } });
    await clickAndExpect(0, "AI-2");
    await clickAndExpect(1, "AI-3");
    u2();

    // 角色：名称(0)、身份(1)、外貌(2)、性格(3)
    act(() => {
      useDramaStore.getState().reset();
      useDramaStore.getState().setScriptData(makeScript());
    });
    const u3 = renderPanel({ type: "character", nodeId: "char-c1" }).unmount;
    await clickAndExpect(0, "AI-4");
    await clickAndExpect(1, "AI-5");
    await clickAndExpect(2, "AI-6");
    await clickAndExpect(3, "AI-7");
    u3();

    // 分镜：画面描述(0)、角色动作(1)、情绪(2)、运镜(3)、正面(4)、反向(5)
    const u4 = renderPanel({ type: "storyboard", nodeId: "storyboard-1" }).unmount;
    await clickAndExpect(0, "AI-8");
    await clickAndExpect(1, "AI-9");
    await clickAndExpect(2, "AI-10");
    await clickAndExpect(3, "AI-11");
    await clickAndExpect(4, "AI-12");
    await clickAndExpect(5, "AI-13");
    u4();

    // 视频：正面(0)、反向(1)
    const u5 = renderPanel({ type: "video", nodeId: "video-1" }).unmount;
    await clickAndExpect(0, "AI-14");
    await clickAndExpect(1, "AI-15");
    u5();

    // 配音：正面(0)、反向(1)、台词(2)
    const u6 = renderPanel({ type: "voice", nodeId: "voice-1" }).unmount;
    await clickAndExpect(0, "AI-16");
    await clickAndExpect(1, "AI-17");
    await clickAndExpect(2, "AI-18");
    u6();
  });

  it("文本为空的工具条按钮禁用且不发起请求", () => {
    useDramaStore.getState().reset();
    renderPanel(); // 无剧本创意表单，premise 为空
    const btns = screen.getAllByText("润色").map((el) => el.closest("button")!);
    expect(btns[0].disabled).toBe(true);
    expect(mockAssist).not.toHaveBeenCalled();
  });
});
