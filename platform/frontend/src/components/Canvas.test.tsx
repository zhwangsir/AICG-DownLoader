import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { vi } from "vitest";
import Canvas from "./Canvas";
import { useDramaStore } from "../store/useDramaStore";
import type {
  CharacterCardData,
  CharacterData,
  EditData,
  QualityCheckData,
  QualityVisualData,
  SceneData,
  ScriptData,
  StoryboardData,
  SubtitleData,
  VideoData,
  VoiceData,
} from "../api/client";
import {
  checkQuality,
  checkVisualQuality,
  composeVideo,
  generateCharacter,
  generateScript,
  generateStoryboard,
  generateStoryboardBatch,
  generateSubtitle,
  generateVideoAsync,
  generateVideoBatch,
  generateVoice,
  pollVideoTask,
} from "../api/client";

/* ------------------------------------------------------------------ */
/* reactflow 桩件：jsdom 无真实测量/拖拽环境，用轻量 stub 暴露全部回调     */
/* ------------------------------------------------------------------ */
const rfMocks = vi.hoisted(() => ({
  updateNodeDimensions: vi.fn(),
  fitView: vi.fn(),
}));

vi.mock("reactflow", async (importOriginal) => {
  const actual = await importOriginal<typeof import("reactflow")>();
  const MockReactFlow = (props: any) => {
    (globalThis as any).__rfProps = props;
    const NodeComp = props.nodeTypes?.custom;
    return (
      <div data-testid="react-flow">
        {props.nodes.map((n: any) => (
          <div
            key={n.id}
            className="react-flow__node"
            data-id={n.id}
            onClick={(e) => props.onNodeClick?.(e, n)}
          >
            {NodeComp ? <NodeComp data={n.data} selected={n.selected} /> : n.id}
          </div>
        ))}
        {props.children}
      </div>
    );
  };
  return {
    ...actual,
    default: MockReactFlow,
    Background: () => <div data-testid="rf-background" />,
    Controls: () => <div data-testid="rf-controls" />,
    MiniMap: (props: any) => {
      const types = [
        "script", "character", "storyboard", "video", "voice",
        "subtitle", "edit", "quality", "visual_quality", "mystery",
      ];
      const colors = types.map((t) => props.nodeColor({ data: { type: t } }));
      colors.push(props.nodeColor({ data: {} }));
      return <div data-testid="rf-minimap" data-colors={JSON.stringify(colors)} />;
    },
    Handle: () => null,
    useStore: (selector: any) => selector({ updateNodeDimensions: rfMocks.updateNodeDimensions }),
    useReactFlow: () => ({ fitView: rfMocks.fitView }),
  };
});

/* ---------------- 面板桩件：捕获 props 以便直接驱动回调 ---------------- */
vi.mock("./CharacterPreviewPanel", () => ({
  default: (props: any) => {
    (globalThis as any).__cppProps = props;
    return (
      <div data-testid="character-preview-panel">
        <span data-testid="cpp-char-id">{props.characterId}</span>
        <button data-testid="cpp-close" onClick={props.onClose}>关闭预览</button>
      </div>
    );
  },
}));

vi.mock("./NodeDetailPanel", () => ({
  default: (props: any) => {
    (globalThis as any).__ndpProps = props;
    return (
      <div data-testid="node-detail-panel">
        <span data-testid="ndp-node-id">{props.nodeId}</span>
        <span data-testid="ndp-type">{props.type}</span>
        <button data-testid="ndp-close" onClick={props.onClose}>关闭详情</button>
      </div>
    );
  },
}));

vi.mock("../api/client", () => ({
  generateScript: vi.fn(),
  generateCharacter: vi.fn(),
  generateStoryboard: vi.fn(),
  generateStoryboardBatch: vi.fn(),
  generateVideoAsync: vi.fn(),
  generateVideoBatch: vi.fn(),
  generateVoice: vi.fn(),
  generateSubtitle: vi.fn(),
  composeVideo: vi.fn(),
  checkQuality: vi.fn(),
  checkVisualQuality: vi.fn(),
  pollVideoTask: vi.fn(),
}));

const mockGenerateScript = vi.mocked(generateScript);
const mockGenerateCharacter = vi.mocked(generateCharacter);
const mockGenerateStoryboard = vi.mocked(generateStoryboard);
const mockGenerateStoryboardBatch = vi.mocked(generateStoryboardBatch);
const mockGenerateVideoAsync = vi.mocked(generateVideoAsync);
const mockGenerateVideoBatch = vi.mocked(generateVideoBatch);
const mockGenerateVoice = vi.mocked(generateVoice);
const mockGenerateSubtitle = vi.mocked(generateSubtitle);
const mockComposeVideo = vi.mocked(composeVideo);
const mockCheckQuality = vi.mocked(checkQuality);
const mockCheckVisualQuality = vi.mocked(checkVisualQuality);
const mockPollVideoTask = vi.mocked(pollVideoTask);

/* ------------------------------- 数据工厂 ------------------------------- */
function makeCharacter(patch: Partial<CharacterData> = {}): CharacterData {
  return {
    character_id: "c1",
    name: "Alice",
    role: "主角",
    age: 26,
    description: "女主",
    personality: "冷静",
    ...patch,
  };
}

function makeScene(patch: Partial<SceneData> = {}): SceneData {
  return {
    scene_id: 1,
    episode: 1,
    shot_type: "中景",
    description: "开场",
    prompt: "p1",
    negative_prompt: "n1",
    character_actions: "走动",
    dialogue: "你好，世界。再见！谢谢",
    emotion: "neutral",
    duration_seconds: 5,
    camera_movement: "static",
    ...patch,
  };
}

function makeScript(patch: Partial<ScriptData> = {}): ScriptData {
  return {
    project_id: "p1",
    title: "测试短剧",
    genre: "都市悬疑",
    aspect_ratio: "9:16",
    total_episodes: 2,
    characters: [makeCharacter(), makeCharacter({ character_id: "c2", name: "Bob", role: "反派", age: null, description: "", personality: "" })],
    scenes: [makeScene()],
    ...patch,
  };
}

const sb1: StoryboardData = { scene_id: 1, image_url: "http://img/sb1.png", prompt_used: "used-prompt" };
const vd1: VideoData = { scene_id: 1, video_url: "http://v/1.mp4", duration_seconds: 3 };
const vc1: VoiceData = {
  scene_id: 1,
  audio_urls: [{ filename: "a.wav", voice: "v", text: "你好", audio_url: "http://a/1.wav" }],
  total_lines: 2,
};
const st1: SubtitleData = {
  scene_id: 1,
  srt_content: "1\n00:00:00,000 --> 00:00:01,000\n你好\n",
  segments: [
    { start: 0, end: 1, text: "你好" },
    { start: 1, end: 2, text: "世界" },
  ],
  language: "zh",
  srt_url: "http://s/1.srt",
};
const edit1: EditData = { project_id: "p1", title: "成片A", final_video_url: "http://v/final.mp4", duration_seconds: 3.25, segments_count: 1 };
const qc1: QualityCheckData = {
  project_id: "p1",
  title: "测试短剧",
  score: 88,
  summary: "ok",
  issues: [
    { category: "logic", severity: "critical", scene_id: 1, message: "台词穿帮", suggestion: "改" },
    { category: "style", severity: "info", scene_id: null, message: "风格提示", suggestion: "" },
  ],
  checked_at: 1,
};
const vq1: QualityVisualData = {
  project_id: "p1",
  title: "测试短剧",
  scene_id: 1,
  score: 92,
  summary: "ok",
  issues: [{ category: "face", severity: "warning", timestamp: 0.5, message: "面部漂移", suggestion: "" }],
  checked_at: 1,
};
const card1: CharacterCardData = {
  character_id: "c1",
  name: "Alice",
  reference_images: { front: "http://img/c1-front.png" },
  consistency_level: "L3",
  used_prompts: { positive_prompt: "pos", negative_prompt: "neg" },
};

/* ------------------------------- 访问辅助 ------------------------------- */
const rfProps = () => (globalThis as any).__rfProps as {
  nodes: any[];
  edges: any[];
  onConnect: (c: any) => void;
  onNodesChange: (c: any[]) => void;
  onEdgesChange: (c: any[]) => void;
};
const nodeById = (id: string) => rfProps().nodes.find((n) => n.id === id);
const edgeById = (id: string) => rfProps().edges.find((e) => e.id === id);
const ndpProps = () => (globalThis as any).__ndpProps as any;
const store = () => useDramaStore.getState();

beforeEach(() => {
  useDramaStore.getState().reset();
  vi.clearAllMocks();
  delete (globalThis as any).__rfProps;
  delete (globalThis as any).__ndpProps;
  delete (globalThis as any).__cppProps;
});

/* --------------------------------- 测试 --------------------------------- */
describe("Canvas 基础渲染", () => {
  it("初始：仅创意输入节点 + 空画布引导 + 批量按钮禁用", () => {
    render(<Canvas />);
    expect(screen.getByText("创意输入")).toBeInTheDocument();
    expect(document.querySelector(".canvas-onboarding")).toBeInTheDocument();
    expect(screen.getByText("批量生成分镜")).toBeDisabled();
    expect(screen.getByText("批量生成视频")).toBeDisabled();
    expect(screen.getByTestId("rf-background")).toBeInTheDocument();
    expect(screen.getByTestId("rf-controls")).toBeInTheDocument();
  });

  it("MiniMap nodeColor：全类型色映射 + 未知类型/缺 type 回退默认色", () => {
    render(<Canvas />);
    const colors = JSON.parse(screen.getByTestId("rf-minimap").dataset.colors!);
    expect(colors).toEqual([
      "#c9b896", "#e08ab8", "#09caf5", "#5eb8d4", "#7ec98f",
      "#b8a88a", "#f2664d", "#f2a93a", "#a8c46a", "#c9b896", "#c9b896",
    ]);
  });

  it("鼠标移动更新 --mx/--my 聚光变量", () => {
    const { container } = render(<Canvas />);
    const el = container.querySelector(".canvas-container") as HTMLElement;
    vi.spyOn(el, "getBoundingClientRect").mockReturnValue({ left: 10, top: 20, width: 200, height: 100 } as DOMRect);
    fireEvent.mouseMove(el, { clientX: 110, clientY: 70 });
    expect(el.style.getPropertyValue("--mx")).toBe("50%");
    expect(el.style.getPropertyValue("--my")).toBe("50%");
  });

  it("NodeInternalsUpdater：挂载后同步节点尺寸并 fitView；节点数变化再次 fitView", async () => {
    render(<Canvas />);
    await waitFor(() => expect(rfMocks.updateNodeDimensions).toHaveBeenCalled());
    const first = rfMocks.updateNodeDimensions.mock.calls[0][0];
    expect(first).toHaveLength(1);
    expect(first[0].id).toBe("start");
    expect(first[0].forceUpdate).toBe(true);
    await waitFor(() =>
      expect(rfMocks.fitView).toHaveBeenCalledWith({ padding: 0.06, maxZoom: 0.85, minZoom: 0.35, duration: 600 })
    );
    const fitCalls = rfMocks.fitView.mock.calls.length;
    act(() => store().setScriptData(makeScript()));
    await waitFor(() => expect(rfMocks.fitView.mock.calls.length).toBeGreaterThan(fitCalls));
  });

  it("节点全部移除后 nodesKey 为空，更新器提前返回不报错", async () => {
    render(<Canvas />);
    await waitFor(() => expect(rfMocks.fitView).toHaveBeenCalled());
    act(() => rfProps().onNodesChange([{ type: "remove", id: "start" }]));
    expect(rfProps().nodes).toHaveLength(0);
  });

  it("节点 DOM 缺失时尺寸更新为空（el ? … : null 回退），仍执行 fitView", async () => {
    render(<Canvas />);
    // 80ms 计时器触发前移除节点 DOM → querySelector 命中 null
    document.querySelector('.react-flow__node[data-id="start"]')?.remove();
    await waitFor(() => expect(rfMocks.fitView).toHaveBeenCalled());
    expect(rfMocks.updateNodeDimensions).not.toHaveBeenCalled();
  });

  it("start 节点 onOpenDetail：无剧本走「去详情页编辑」按钮；有剧本走 isEditInput 回调", () => {
    const { unmount } = render(<Canvas />);
    fireEvent.click(screen.getByText("去详情页编辑"));
    expect(screen.getByTestId("ndp-node-id")).toHaveTextContent("start");
    expect(screen.getByTestId("ndp-type")).toHaveTextContent("script");
    unmount();
    act(() => store().setScriptData(makeScript()));
    render(<Canvas />);
    act(() => nodeById("start").data.onOpenDetail());
    expect(screen.getByTestId("ndp-node-id")).toHaveTextContent("start");
  });
});

describe("Canvas 图构建（store → 节点/边）", () => {
  it("有剧本：剧本/角色/场景链节点与边全部生成，引导条消失", () => {
    act(() => store().setScriptData(makeScript()));
    render(<Canvas />);
    expect(document.querySelector(".canvas-onboarding")).not.toBeInTheDocument();
    expect(screen.getByText("剧本: 测试短剧")).toBeInTheDocument();
    expect(nodeById("script").data.detail).toBe("2 集 | 1 分镜 | 2 角色");
    expect(screen.getByText("角色: Alice")).toBeInTheDocument();
    // c2：description 为空 → detail 回退「身份 · 年龄」
    expect(nodeById("char-c2").data.detail).toBe("反派 · ");
    expect(screen.getByText("分镜 1: 中景")).toBeInTheDocument();
    expect(screen.getByText("视频 1")).toBeInTheDocument();
    expect(screen.getByText("配音 1")).toBeInTheDocument();
    expect(screen.getByText("字幕 1")).toBeInTheDocument();
    // 边
    expect(edgeById("e-start-script")).toMatchObject({ source: "start", target: "script", animated: false });
    expect(edgeById("e-script-char-c1")).toBeDefined();
    expect(edgeById("e-script-scene-1")).toBeDefined();
    expect(edgeById("e-scene-1-video-1")).toBeDefined();
    expect(edgeById("e-video-1-voice-1")).toBeDefined();
    expect(edgeById("e-voice-1-subtitle-1")).toBeDefined();
    // 锁定链：未生成定妆照 → 分镜不可生成
    expect(nodeById("scene-1").data.canGenerate).toBe(false);
    expect(nodeById("scene-1").data.lockReason).toBe("请先生成所有角色定妆照");
    // 视频/配音/字幕节点的前置锁定
    expect(nodeById("video-1").data.lockReason).toBe("请先生成该场景分镜图");
    expect(nodeById("voice-1").data.lockReason).toBe("请先生成该场景视频");
    expect(nodeById("subtitle-1").data.lockReason).toBe("请先生成该场景配音");
    // 批量分镜可用、批量视频仍禁用（无分镜）
    expect(screen.getByText("批量生成分镜")).not.toBeDisabled();
    expect(screen.getByText("批量生成视频")).toBeDisabled();
  });

  it("genre 为空时剧本 preview 回退到首场景描述；空场景数组不崩溃", () => {
    act(() => store().setScriptData(makeScript({ genre: "" })));
    const { unmount } = render(<Canvas />);
    expect(nodeById("script").data.preview).toBe("开场");
    expect(nodeById("script").data.tags).not.toContain("");
    unmount();
    act(() => store().setScriptData(makeScript({ scenes: [] })));
    render(<Canvas />);
    // 有题材但无场景：preview 为题材前缀，scenes[0]?.description 回退空串
    expect(nodeById("script").data.preview).toBe("题材：都市悬疑。");
    expect(nodeById("scene-1")).toBeUndefined();
  });

  it("定妆照卡片：imageUrl/hasGenerated/可编辑提示词注入节点", () => {
    act(() => {
      store().setScriptData(makeScript());
      store().addCharacterCard(card1);
    });
    render(<Canvas />);
    const c1 = nodeById("char-c1").data;
    expect(c1.imageUrl).toBe("http://img/c1-front.png");
    expect(c1.hasGenerated).toBe(true);
    expect(c1.generateLabel).toBe("重新生成定妆照");
    expect(c1.statusText).toBe("定妆照已生成");
    expect(c1.editablePrompts).toEqual({ positive: "pos", negative: "neg" });
    expect(typeof c1.onEditPrompts).toBe("function");
    // c2 无卡 → 待生成
    expect(nodeById("char-c2").data.statusText).toBe("待生成定妆照");
    // 分镜仍锁定（c2 无定妆照）
    expect(nodeById("scene-1").data.canGenerate).toBe(false);
  });

  it("reference_images 取图优先级：front > portrait > 任意值 > 空", () => {
    act(() => {
      store().setScriptData(makeScript({ characters: [makeCharacter(), makeCharacter({ character_id: "c2", name: "Bob" }), makeCharacter({ character_id: "c3", name: "Cid" })] }));
      store().addCharacterCard({ character_id: "c2", name: "Bob", reference_images: { portrait: "http://img/p.png" }, consistency_level: "L3" });
      store().addCharacterCard({ character_id: "c3", name: "Cid", reference_images: {}, consistency_level: "L3" });
    });
    render(<Canvas />);
    expect(nodeById("char-c2").data.imageUrl).toBe("http://img/p.png");
    expect(nodeById("char-c3").data.imageUrl).toBe("");
    expect(nodeById("char-c3").data.hasGenerated).toBe(false);
  });

  it("全部角色有定妆照后分镜解锁；长描述截断；对白/动作/描述 preview 回退链", () => {
    const longDesc = "很".repeat(80);
    const longDialogue = "长".repeat(90);
    act(() => {
      store().setScriptData(
        makeScript({
          scenes: [
            makeScene({ description: longDesc, dialogue: longDialogue }),
            makeScene({ scene_id: 2, dialogue: "", character_actions: "奔跑", description: "第二镜" }),
            makeScene({ scene_id: 3, dialogue: "", character_actions: "", description: "第三镜", emotion: "", camera_movement: "" }),
          ],
        })
      );
      store().addCharacterCard(card1);
      store().addCharacterCard({ ...card1, character_id: "c2", used_prompts: undefined });
    });
    render(<Canvas />);
    expect(nodeById("scene-1").data.canGenerate).toBe(true);
    expect(nodeById("scene-1").data.lockReason).toBeUndefined();
    expect(nodeById("scene-1").data.detail).toBe("很".repeat(60) + "…");
    // 对白 + 动作
    expect(nodeById("scene-1").data.preview).toBe(`「${longDialogue}」 · 走动`);
    // 无对白 → 动作
    expect(nodeById("scene-2").data.preview).toBe("奔跑");
    // 无对白无动作 → 描述
    expect(nodeById("scene-3").data.preview).toBe("第三镜");
    // 长对白在配音节点 preview 截断（>80）
    expect(nodeById("voice-1").data.preview).toBe(`待生成配音 · 对白：${"长".repeat(80)}…`);
    // 无对白 → 配音/字幕占位文案
    expect(nodeById("voice-3").data.preview).toBe("待生成后展示对白摘要");
    expect(nodeById("subtitle-3").data.preview).toBe("待生成后展示字幕片段");
    // 字幕节点 preview 用对白（>80 截断）
    expect(nodeById("subtitle-1").data.subtitleText).toBe("长".repeat(80) + "…");
    // tags 过滤空值（scene-3 emotion/camera_movement 为空）
    expect(nodeById("scene-3").data.tags).toEqual(["中景", "5s"]);
    // 无台词场景的视频节点 preview（有分镜/无分镜同一文案模板）
    expect(nodeById("video-2").data.preview).toContain("基于分镜图生成 5s 视频");
    // 只渲染前 3 个场景
    act(() => store().setScriptData({ ...makeScript(), scenes: [makeScene(), makeScene({ scene_id: 2 }), makeScene({ scene_id: 3 }), makeScene({ scene_id: 4 })] }));
    expect(nodeById("scene-4")).toBeUndefined();
  });

  it("分镜/视频/配音/字幕就绪后：节点状态与 edit/visual-quality 节点出现", () => {
    act(() => {
      store().setScriptData(makeScript());
      store().addCharacterCard(card1);
      store().addCharacterCard({ ...card1, character_id: "c2" });
      store().addStoryboard(sb1);
      store().addVideo(vd1);
      store().addVoice(vc1);
      store().addSubtitle(st1);
    });
    render(<Canvas />);
    expect(nodeById("scene-1").data.imageUrl).toBe("http://img/sb1.png");
    expect(nodeById("scene-1").data.generateLabel).toBe("重新生成分镜");
    const video = nodeById("video-1").data;
    expect(video.hasGenerated).toBe(true);
    expect(video.detail).toBe("已生成 (3s)");
    expect(video.videoUrl).toBe("http://v/1.mp4");
    const voice = nodeById("voice-1").data;
    expect(voice.detail).toBe("IndexTTS-2 · 2 条");
    expect(voice.audioUrl).toBe("http://a/1.wav");
    const sub = nodeById("subtitle-1").data;
    expect(sub.detail).toBe("faster-whisper (zh) · 2 段");
    expect(sub.subtitleText).toBe("你好 / 世界");
    // edit 节点：全链路未齐（差字幕? 已齐）→ 可合成
    const edit = nodeById("edit-final").data;
    expect(edit.label).toBe("合成成片");
    expect(edit.canGenerate).toBe(true);
    expect(edgeById("e-subtitle-1-edit")).toBeDefined();
    // 视觉质检节点随视频出现
    expect(nodeById("visual-quality-final").data.label).toBe("视觉质检");
    expect(edgeById("e-video-visual-quality")).toMatchObject({ source: "video-1", target: "visual-quality-final" });
    // 批量视频按钮可用
    expect(screen.getByText("批量生成视频")).not.toBeDisabled();
  });

  it("editData 就位：成片节点详情化 + 质检节点出现（带剧本→质检纵向边）", () => {
    act(() => {
      store().setScriptData(makeScript());
      store().setEditData(edit1);
    });
    render(<Canvas />);
    const edit = nodeById("edit-final").data;
    expect(edit.label).toBe("成片: 成片A");
    expect(edit.detail).toBe("1 场景 | 3.3s");
    expect(edit.hasGenerated).toBe(true);
    const quality = nodeById("quality-final").data;
    expect(quality.label).toBe("剧本质检");
    expect(quality.canGenerate).toBe(true);
    expect(edgeById("e-script-quality")).toMatchObject({ sourceHandle: "source-bottom", targetHandle: "target-top" });
  });

  it("qualityData/visualQualityData：质检节点摘要与问题预览", () => {
    act(() => {
      store().setScriptData(makeScript());
      store().setEditData(edit1);
      store().setQualityData(qc1);
      store().addVideo(vd1);
      store().setVisualQualityData(vq1);
    });
    render(<Canvas />);
    const q = nodeById("quality-final").data;
    expect(q.label).toBe("质检: 测试短剧");
    expect(q.qualitySummary).toBe("质量分 88 | 2 问题");
    expect(q.qualityIssues).toContain("[critical] 台词穿帮");
    expect(q.generateLabel).toBe("重新质检");
    const v = nodeById("visual-quality-final").data;
    expect(v.label).toBe("视觉质检: 场景 1");
    expect(v.qualitySummary).toBe("质量分 92 | 场景 1");
    expect(v.qualityIssues).toContain("[warning] 面部漂移");
  });

  it("仅有质检结果无成片：质检节点可见但锁定（请先合成成片）", () => {
    act(() => {
      store().setScriptData(makeScript());
      store().setQualityData(qc1);
    });
    render(<Canvas />);
    const q = nodeById("quality-final").data;
    expect(q.canGenerate).toBe(false);
    expect(q.lockReason).toBe("请先合成成片");
  });

  it("配音已生成 + 长对白：voice 节点 preview 对白截断（>60）", () => {
    act(() => {
      store().setScriptData(makeScript({ scenes: [makeScene({ dialogue: "对".repeat(70) })] }));
      store().addVoice(vc1);
    });
    render(<Canvas />);
    expect(nodeById("voice-1").data.preview).toBe(`已生成 2 条语音。对白：${"对".repeat(60)}…`);
  });

  it("globalLoading：所有带生成按钮的节点被全局锁定", () => {
    act(() => {
      store().setScriptData(makeScript());
      store().startGlobalLoading("忙碌中");
    });
    render(<Canvas />);
    expect(nodeById("scene-1").data.canGenerate).toBe(false);
    expect(nodeById("scene-1").data.lockReason).toBe("有其他生成任务进行中，请等待完成");
    expect(nodeById("char-c1").data.lockReason).toBe("有其他生成任务进行中，请等待完成");
  });
});

describe("Canvas 节点点击与面板", () => {
  it("点击角色节点 → 角色预览面板；关闭后消失", () => {
    act(() => store().setScriptData(makeScript()));
    const { container } = render(<Canvas />);
    fireEvent.click(container.querySelector('.react-flow__node[data-id="char-c1"]')!);
    expect(screen.getByTestId("character-preview-panel")).toBeInTheDocument();
    expect(screen.getByTestId("cpp-char-id")).toHaveTextContent("c1");
    fireEvent.click(screen.getByTestId("cpp-close"));
    expect(screen.queryByTestId("character-preview-panel")).not.toBeInTheDocument();
  });

  it("点击剧本/场景节点 → 详情面板携带 nodeId/type/data；关闭消失", () => {
    act(() => store().setScriptData(makeScript()));
    const { container } = render(<Canvas />);
    fireEvent.click(container.querySelector('.react-flow__node[data-id="script"]')!);
    expect(screen.getByTestId("ndp-node-id")).toHaveTextContent("script");
    expect(screen.getByTestId("ndp-type")).toHaveTextContent("script");
    expect(ndpProps().data.label).toBe("剧本: 测试短剧");
    fireEvent.click(screen.getByTestId("ndp-close"));
    expect(screen.queryByTestId("node-detail-panel")).not.toBeInTheDocument();
    // 场景节点
    fireEvent.click(container.querySelector('.react-flow__node[data-id="scene-1"]')!);
    expect(screen.getByTestId("ndp-type")).toHaveTextContent("storyboard");
  });

  it("onConnect / onNodesChange / onEdgesChange 回调驱动图状态", () => {
    act(() => store().setScriptData(makeScript()));
    render(<Canvas />);
    // 位置变更
    act(() => rfProps().onNodesChange([{ type: "position", id: "start", position: { x: 5, y: 6 } }]));
    expect(nodeById("start").position).toEqual({ x: 5, y: 6 });
    // 手动连线（须避开已存在的 script→char-c1 边，addEdge 会去重）
    const before = rfProps().edges.length;
    act(() => rfProps().onConnect({ source: "start", target: "char-c1" }));
    expect(rfProps().edges.length).toBe(before + 1);
    // 边选择变更
    const target = rfProps().edges[before];
    act(() => rfProps().onEdgesChange([{ type: "select", id: target.id, selected: true }]));
    expect(rfProps().edges[before].selected).toBe(true);
  });
});

describe("Canvas 剧本生成（start 节点 onGenerate）", () => {
  const valid = { premise: "创意", genre: "悬疑", episodes: 2, scenes_per_episode: 3, style: "写实电影感", aspect_ratio: "9:16" };

  const openStart = (container: HTMLElement) => {
    fireEvent.click(container.querySelector('.react-flow__node[data-id="start"]')!);
    expect(screen.getByTestId("node-detail-panel")).toBeInTheDocument();
  };

  it("参数校验：缺 options / 空创意 / 空题材 / 空集数 / 空风格 / 空画幅", async () => {
    const { container } = render(<Canvas />);
    openStart(container);
    await act(async () => { await ndpProps().onGenerate(undefined); });
    expect(store().statusInfo).toBe("生成参数缺失");
    await act(async () => { await ndpProps().onGenerate({ ...valid, premise: " " }); });
    expect(store().statusInfo).toBe("请输入创意");
    await act(async () => { await ndpProps().onGenerate({ ...valid, genre: " " }); });
    expect(store().statusInfo).toBe("请输入题材");
    await act(async () => { await ndpProps().onGenerate({ ...valid, episodes: "" }); });
    expect(store().statusInfo).toBe("请设置集数与每集分镜数");
    await act(async () => { await ndpProps().onGenerate({ ...valid, style: "" }); });
    expect(store().statusInfo).toBe("请选择视觉风格");
    await act(async () => { await ndpProps().onGenerate({ ...valid, aspect_ratio: "" }); });
    expect(store().statusInfo).toBe("请选择画幅比例");
    expect(mockGenerateScript).not.toHaveBeenCalled();
  });

  it("成功：写入剧本并同步项目风格；失败与异常路径", async () => {
    mockGenerateScript.mockResolvedValueOnce({ success: true, data: makeScript() });
    const { container } = render(<Canvas />);
    openStart(container);
    await act(async () => { await ndpProps().onGenerate(valid); });
    expect(mockGenerateScript).toHaveBeenCalledWith({ premise: "创意", genre: "悬疑", episodes: 2, scenes_per_episode: 3 });
    expect(store().scriptData?.title).toBe("测试短剧");
    expect(store().projectStyle).toBe("写实电影感");
    expect(store().statusInfo).toContain("剧本已生成: 测试短剧");
    expect(store().globalLoading).toBe(false);
    // 失败
    mockGenerateScript.mockResolvedValueOnce({ success: false, error: "LLM 离线" });
    await act(async () => { await ndpProps().onGenerate(valid); });
    expect(store().statusInfo).toBe("剧本生成失败: LLM 离线");
    // 异常
    mockGenerateScript.mockRejectedValueOnce(new Error("boom"));
    await act(async () => { await ndpProps().onGenerate(valid); });
    expect(store().statusInfo).toBe("剧本生成出错: Error: boom");
  });

  it("globalLoading 时直接返回不调用 API", async () => {
    act(() => store().startGlobalLoading("忙"));
    const { container } = render(<Canvas />);
    openStart(container); // 点击读取的是最新闭包（globalLoading=true）
    await act(async () => { await ndpProps().onGenerate(valid); });
    expect(mockGenerateScript).not.toHaveBeenCalled();
  });

  it("有剧本后 start 变为「重新创作」：onGenerate 重置剧本", async () => {
    act(() => store().setScriptData(makeScript()));
    const { container } = render(<Canvas />);
    expect(screen.getByText("重新创作")).toBeInTheDocument();
    openStart(container);
    await act(async () => { await ndpProps().onGenerate(); });
    expect(store().scriptData).toBeNull();
    expect(store().statusInfo).toBe("已重置，请输入新创意");
  });
});

describe("Canvas 角色定妆照生成", () => {
  it("节点 onGenerate（无自定义提示词）→ 打开角色预览面板", async () => {
    act(() => store().setScriptData(makeScript()));
    render(<Canvas />);
    await act(async () => { await nodeById("char-c1").data.onGenerate(); });
    expect(screen.getByTestId("cpp-char-id")).toHaveTextContent("c1");
  });

  it("onEditPrompts 空白提示词 → 回退打开预览面板（不直接生成）", async () => {
    act(() => {
      store().setScriptData(makeScript());
      store().addCharacterCard(card1);
    });
    render(<Canvas />);
    await act(async () => { await nodeById("char-c1").data.onEditPrompts("   ", ""); });
    expect(screen.getByTestId("character-preview-panel")).toBeInTheDocument();
    expect(mockGenerateCharacter).not.toHaveBeenCalled();
  });

  it("onEditPrompts 有效提示词 → 直接生成：成功/失败/异常/全局锁", async () => {
    act(() => {
      store().setScriptData(makeScript());
      store().addCharacterCard(card1);
    });
    render(<Canvas />);
    // 成功
    mockGenerateCharacter.mockResolvedValueOnce({ success: true, data: { ...card1 } });
    await act(async () => { await nodeById("char-c1").data.onEditPrompts("新正面", "新负面"); });
    expect(mockGenerateCharacter).toHaveBeenCalledWith({
      character: expect.objectContaining({ character_id: "c1" }),
      style: "写实电影感",
      consistency_level: "L3",
      custom_positive_prompt: "新正面",
      custom_negative_prompt: "新负面",
    });
    expect(store().statusInfo).toBe("角色定妆照已生成: Alice");
    expect(store().globalLoading).toBe(false);
    // 失败
    mockGenerateCharacter.mockResolvedValueOnce({ success: false, error: "GPU 忙" });
    await act(async () => { await nodeById("char-c1").data.onEditPrompts("p", "n"); });
    expect(store().statusInfo).toBe("定妆照生成失败: GPU 忙");
    // 异常
    mockGenerateCharacter.mockRejectedValueOnce(new Error("net"));
    await act(async () => { await nodeById("char-c1").data.onEditPrompts("p", "n"); });
    expect(store().statusInfo).toBe("定妆照生成出错: Error: net");
    // 全局锁
    act(() => store().startGlobalLoading("忙"));
    await act(async () => { await nodeById("char-c1").data.onEditPrompts("p", "n"); });
    expect(mockGenerateCharacter).toHaveBeenCalledTimes(3);
  });
});

describe("Canvas 分镜生成", () => {
  it("单镜生成：成功写入 store，loading 期间目标边动画化", async () => {
    let resolveSb!: (v: any) => void;
    mockGenerateStoryboard.mockImplementation(() => new Promise((r) => { resolveSb = r; }));
    act(() => {
      store().setScriptData(makeScript());
      store().addCharacterCard(card1);
      store().addCharacterCard({ ...card1, character_id: "c2" });
    });
    render(<Canvas />);
    act(() => { nodeById("scene-1").data.onGenerate(); });
    // loading 中间态：节点 loading + 入边 animated
    expect(nodeById("scene-1").data.loading).toBe(true);
    expect(nodeById("scene-1").data.loadingText).toBe("生成分镜中...");
    expect(edgeById("e-script-scene-1").animated).toBe(true);
    await act(async () => { resolveSb({ success: true, data: sb1 }); });
    expect(store().storyboards).toHaveLength(1);
    expect(store().statusInfo).toBe("分镜关键帧已生成: 场景 1");
    expect(nodeById("scene-1").data.loading).toBe(false);
    expect(edgeById("e-script-scene-1").animated).toBe(false);
  });

  it("失败 / 异常 / 全局锁路径", async () => {
    act(() => store().setScriptData(makeScript()));
    render(<Canvas />);
    mockGenerateStoryboard.mockResolvedValueOnce({ success: false, error: "超时" });
    await act(async () => { await nodeById("scene-1").data.onGenerate(); });
    expect(store().statusInfo).toBe("分镜生成失败: 超时");
    mockGenerateStoryboard.mockRejectedValueOnce(new Error("x"));
    await act(async () => { await nodeById("scene-1").data.onGenerate(); });
    expect(store().statusInfo).toBe("分镜生成出错: Error: x");
    act(() => store().startGlobalLoading("忙"));
    await act(async () => { await nodeById("scene-1").data.onGenerate(); });
    expect(mockGenerateStoryboard).toHaveBeenCalledTimes(2);
  });

  it("批量分镜：成功（部分失败）→ 任务 completed；全失败 → failed", async () => {
    act(() => store().setScriptData(makeScript({ scenes: [makeScene(), makeScene({ scene_id: 2 })] })));
    render(<Canvas />);
    mockGenerateStoryboardBatch.mockResolvedValueOnce({
      success: true,
      data: { results: [sb1], failed_scenes: [2] },
    });
    fireEvent.click(screen.getByText("批量生成分镜"));
    await waitFor(() => expect(store().statusInfo).toBe("分镜批量生成完成: 1 成功, 1 失败"));
    expect(store().storyboards).toHaveLength(1);
    const task = store().tasks.find((t) => t.id.startsWith("batch-storyboard-"))!;
    expect(task.status).toBe("completed");
    expect(task.message).toBe("1 成功，1 失败");
    // 全失败 → failed
    mockGenerateStoryboardBatch.mockResolvedValueOnce({
      success: true,
      data: { results: [], failed_scenes: [2] },
    });
    fireEvent.click(screen.getByText("批量生成分镜"));
    await waitFor(() => expect(store().tasks.find((t) => t.status === "failed" && t.id.startsWith("batch-storyboard-"))).toBeDefined());
  });

  it("批量分镜：无待生成场景 / API 失败 / 异常 / 禁用态不触发", async () => {
    act(() => {
      store().setScriptData(makeScript());
      store().addStoryboard(sb1);
    });
    render(<Canvas />);
    fireEvent.click(screen.getByText("批量生成分镜"));
    await waitFor(() => expect(store().statusInfo).toBe("所有分镜已生成"));
    expect(mockGenerateStoryboardBatch).not.toHaveBeenCalled();
    // API 失败
    act(() => store().reset());
    act(() => store().setScriptData(makeScript()));
    mockGenerateStoryboardBatch.mockResolvedValueOnce({ success: false, error: "集群离线" });
    fireEvent.click(screen.getByText("批量生成分镜"));
    await waitFor(() => expect(store().statusInfo).toBe("分镜批量生成失败: 集群离线"));
    // 异常
    mockGenerateStoryboardBatch.mockRejectedValueOnce(new Error("net"));
    fireEvent.click(screen.getByText("批量生成分镜"));
    await waitFor(() => expect(store().statusInfo).toBe("分镜批量生成出错: Error: net"));
    // 无剧本禁用
    act(() => store().reset());
    fireEvent.click(screen.getByText("批量生成分镜"));
    expect(mockGenerateStoryboardBatch).toHaveBeenCalledTimes(2);
  });
});

describe("Canvas 视频生成", () => {
  const setupStoryboarded = () => {
    act(() => {
      store().setScriptData(makeScript());
      store().addCharacterCard(card1);
      store().addCharacterCard({ ...card1, character_id: "c2" });
      store().addStoryboard(sb1);
    });
  };

  it("异步轮询成功：进度回写任务，完成后入库", async () => {
    setupStoryboarded();
    render(<Canvas />);
    mockGenerateVideoAsync.mockResolvedValueOnce({ success: true, data: { poll_url: "http://poll/1" } });
    mockPollVideoTask.mockImplementationOnce(async (_url, _opts, onProgress) => {
      onProgress?.({ percent: 40, message: "排队" } as any);
      return { status: "completed", result: { ...vd1 } } as any;
    });
    await act(async () => { await nodeById("video-1").data.onGenerate(); });
    expect(mockGenerateVideoAsync).toHaveBeenCalledWith({
      scene_id: 1,
      image_url: "http://img/sb1.png",
      prompt: "p1",
      negative_prompt: "n1",
      duration_seconds: 3,
      preview: false,
      quality: "final",
    });
    expect(store().videos).toHaveLength(1);
    expect(store().statusInfo).toBe("视频已生成: 场景 1 (3s)");
    const task = store().tasks.find((t) => t.kind === "video")!;
    expect(task.status).toBe("completed");
    expect(task.percent).toBe(100);
  });

  it("轮询失败 / 异常 / 全局锁；prompt 空时回退分镜 prompt", async () => {
    act(() => {
      store().setScriptData(makeScript({ scenes: [makeScene({ prompt: "", negative_prompt: "" })] }));
      store().addStoryboard(sb1);
    });
    render(<Canvas />);
    mockGenerateVideoAsync.mockResolvedValueOnce({ success: true, data: { poll_url: "u" } });
    mockPollVideoTask.mockResolvedValueOnce({ status: "failed", error: "OOM" } as any);
    await act(async () => { await nodeById("video-1").data.onGenerate(); });
    // prompt 空 → sb.prompt_used；negative 空 → 默认负面词
    expect(mockGenerateVideoAsync).toHaveBeenCalledWith(expect.objectContaining({
      prompt: "used-prompt",
      negative_prompt: "blurry, low quality, deformed, ugly, watermark, static",
    }));
    expect(store().statusInfo).toBe("视频生成失败: OOM");
    expect(store().tasks.find((t) => t.kind === "video")?.status).toBe("failed");
    // 异常
    mockGenerateVideoAsync.mockRejectedValueOnce(new Error("refused"));
    await act(async () => { await nodeById("video-1").data.onGenerate(); });
    expect(store().statusInfo).toBe("视频生成出错: Error: refused");
    // 全局锁
    act(() => store().startGlobalLoading("忙"));
    await act(async () => { await nodeById("video-1").data.onGenerate(); });
    expect(mockGenerateVideoAsync).toHaveBeenCalledTimes(2);
  });

  it("批量视频：成功入库；无待生成 / 失败 / 异常路径", async () => {
    // negative_prompt 为空 → 批量请求回退默认负面词
    act(() => {
      store().setScriptData(makeScript({ scenes: [makeScene({ prompt: "", negative_prompt: "" })] }));
      store().addStoryboard(sb1);
    });
    render(<Canvas />);
    mockGenerateVideoBatch.mockResolvedValueOnce({
      success: true,
      data: { results: [vd1], failed_scenes: [] },
    });
    fireEvent.click(screen.getByText("批量生成视频"));
    await waitFor(() => expect(store().statusInfo).toBe("视频批量生成完成: 1 成功"));
    expect(mockGenerateVideoBatch.mock.calls[0][0].items[0]).toMatchObject({
      scene_id: 1,
      prompt: "used-prompt",
      negative_prompt: "blurry, low quality, deformed, ugly, watermark, static",
    });
    expect(store().videos).toHaveLength(1);
    // 无待生成（视频已存在）
    fireEvent.click(screen.getByText("批量生成视频"));
    await waitFor(() => expect(store().statusInfo).toBe("所有视频已生成"));
    expect(mockGenerateVideoBatch).toHaveBeenCalledTimes(1);
    // 失败
    act(() => store().reset());
    setupStoryboarded();
    mockGenerateVideoBatch.mockResolvedValueOnce({ success: false, error: "队列满" });
    fireEvent.click(screen.getByText("批量生成视频"));
    await waitFor(() => expect(store().statusInfo).toBe("视频批量生成失败: 队列满"));
    // 异常
    mockGenerateVideoBatch.mockRejectedValueOnce(new Error("x"));
    fireEvent.click(screen.getByText("批量生成视频"));
    await waitFor(() => expect(store().statusInfo).toBe("视频批量生成出错: Error: x"));
  });
});

describe("Canvas 配音 / 字幕生成", () => {
  it("配音：台词按标点拆句并轮换说话人；成功入库", async () => {
    act(() => store().setScriptData(makeScript()));
    render(<Canvas />);
    mockGenerateVoice.mockResolvedValueOnce({ success: true, data: vc1 });
    await act(async () => { await nodeById("voice-1").data.onGenerate(); });
    const req = mockGenerateVoice.mock.calls[0][0];
    expect(req.scene_id).toBe(1);
    // "你好，世界。再见！谢谢" → 4 行（均 >1 字），2 角色轮换
    expect(req.dialogues).toHaveLength(4);
    expect(req.dialogues[0]).toMatchObject({ text: "你好", character_name: "Alice", character_role: "主角", character_age: 26 });
    expect(req.dialogues[1]).toMatchObject({ text: "世界", character_name: "Bob" });
    expect(store().voices).toHaveLength(1);
    expect(store().statusInfo).toBe("配音已生成: 场景 1 (2 条语音)");
  });

  it("配音：无角色时说话人回退「角色N」；无台词场景早退；失败/异常/全局锁", async () => {
    act(() => store().setScriptData(makeScript({ characters: [] })));
    render(<Canvas />);
    mockGenerateVoice.mockResolvedValueOnce({ success: true, data: vc1 });
    await act(async () => { await nodeById("voice-1").data.onGenerate(); });
    expect(mockGenerateVoice.mock.calls[0][0].dialogues[0]).toMatchObject({ character_name: "角色1", character_role: "", character_age: null });
    // 无台词
    act(() => store().setScriptData(makeScript({ scenes: [makeScene({ dialogue: "" })] })));
    await act(async () => { await nodeById("voice-1").data.onGenerate(); });
    expect(store().statusInfo).toBe("场景 1 没有台词，无法生成配音");
    expect(mockGenerateVoice).toHaveBeenCalledTimes(1);
    // 恢复台词
    act(() => store().setScriptData(makeScript()));
    // 失败
    mockGenerateVoice.mockResolvedValueOnce({ success: false, error: "TTS 离线" });
    await act(async () => { await nodeById("voice-1").data.onGenerate(); });
    expect(store().statusInfo).toBe("配音生成失败: TTS 离线");
    // 异常
    mockGenerateVoice.mockRejectedValueOnce(new Error("x"));
    await act(async () => { await nodeById("voice-1").data.onGenerate(); });
    expect(store().statusInfo).toBe("配音生成出错: Error: x");
    // 全局锁
    act(() => store().startGlobalLoading("忙"));
    await act(async () => { await nodeById("voice-1").data.onGenerate(); });
    expect(mockGenerateVoice).toHaveBeenCalledTimes(3);
  });

  it("字幕：成功/失败/异常/全局锁", async () => {
    act(() => {
      store().setScriptData(makeScript());
      store().addVoice(vc1);
    });
    render(<Canvas />);
    mockGenerateSubtitle.mockResolvedValueOnce({ success: true, data: st1 });
    await act(async () => { await nodeById("subtitle-1").data.onGenerate(); });
    expect(mockGenerateSubtitle).toHaveBeenCalledWith({ scene_id: 1, audio_url: "http://a/1.wav", language: "zh" });
    expect(store().subtitles).toHaveLength(1);
    expect(store().statusInfo).toBe("字幕已生成: 场景 1 (2 段)");
    mockGenerateSubtitle.mockResolvedValueOnce({ success: false, error: "ASR 挂死" });
    await act(async () => { await nodeById("subtitle-1").data.onGenerate(); });
    expect(store().statusInfo).toBe("字幕生成失败: ASR 挂死");
    mockGenerateSubtitle.mockRejectedValueOnce(new Error("x"));
    await act(async () => { await nodeById("subtitle-1").data.onGenerate(); });
    expect(store().statusInfo).toBe("字幕生成出错: Error: x");
    act(() => store().startGlobalLoading("忙"));
    await act(async () => { await nodeById("subtitle-1").data.onGenerate(); });
    expect(mockGenerateSubtitle).toHaveBeenCalledTimes(3);
  });

  it("配音无音频条目时字幕节点不可生成（onGenerate 未挂载）", () => {
    act(() => {
      store().setScriptData(makeScript());
      store().addVoice({ scene_id: 1, audio_urls: [], total_lines: 0 });
    });
    render(<Canvas />);
    expect(nodeById("subtitle-1").data.onGenerate).toBeUndefined();
    expect(nodeById("subtitle-1").data.canGenerate).toBe(false);
  });
});

describe("Canvas 合成与质检", () => {
  const setupFull = () => {
    act(() => {
      store().setScriptData(makeScript());
      store().addVideo(vd1);
      store().addVoice(vc1);
      store().addSubtitle(st1);
    });
  };

  it("合成成片：素材齐 → 成功；素材缺 → 提示；失败/异常/全局锁", async () => {
    setupFull();
    render(<Canvas />);
    mockComposeVideo.mockResolvedValueOnce({ success: true, data: edit1 });
    await act(async () => { await nodeById("edit-final").data.onGenerate(); });
    expect(mockComposeVideo).toHaveBeenCalledWith(expect.objectContaining({
      project_id: "p1",
      title: "测试短剧",
      segments: [{ scene_id: 1, video_url: "http://v/1.mp4", audio_url: "http://a/1.wav", subtitle_url: "http://s/1.srt" }],
    }));
    expect(store().editData?.title).toBe("成片A");
    expect(store().statusInfo).toBe("成片已合成: 成片A | 1 场景 | 3.3s");
    // 失败
    mockComposeVideo.mockResolvedValueOnce({ success: false, error: "ffmpeg 崩" });
    await act(async () => { await nodeById("edit-final").data.onGenerate(); });
    expect(store().statusInfo).toBe("合成失败: ffmpeg 崩");
    // 异常
    mockComposeVideo.mockRejectedValueOnce(new Error("x"));
    await act(async () => { await nodeById("edit-final").data.onGenerate(); });
    expect(store().statusInfo).toBe("合成出错: Error: x");
    // 全局锁
    act(() => store().startGlobalLoading("忙"));
    await act(async () => { await nodeById("edit-final").data.onGenerate(); });
    expect(mockComposeVideo).toHaveBeenCalledTimes(3);
  });

  it("合成成片：缺配音/字幕素材时提示并早退", async () => {
    act(() => {
      store().setScriptData(makeScript());
      store().addVideo(vd1); // 只有视频
    });
    render(<Canvas />);
    await act(async () => { await nodeById("edit-final").data.onGenerate(); });
    expect(store().statusInfo).toBe("没有完整素材的场景（需视频+配音+字幕）");
    expect(mockComposeVideo).not.toHaveBeenCalled();
  });

  it("剧本质检：成功（含 critical 计数）/失败/异常/全局锁", async () => {
    act(() => {
      store().setScriptData(makeScript());
      store().setEditData(edit1);
    });
    render(<Canvas />);
    mockCheckQuality.mockResolvedValueOnce({ success: true, data: qc1 });
    await act(async () => { await nodeById("quality-final").data.onGenerate(); });
    expect(mockCheckQuality).toHaveBeenCalledWith(expect.objectContaining({ project_id: "p1", title: "测试短剧" }));
    expect(store().qualityData?.score).toBe(88);
    expect(store().statusInfo).toBe("质检完成: 质量分 88 | 2 问题 | 1 严重");
    mockCheckQuality.mockResolvedValueOnce({ success: false, error: "LLM 超时" });
    await act(async () => { await nodeById("quality-final").data.onGenerate(); });
    expect(store().statusInfo).toBe("质检失败: LLM 超时");
    mockCheckQuality.mockRejectedValueOnce(new Error("x"));
    await act(async () => { await nodeById("quality-final").data.onGenerate(); });
    expect(store().statusInfo).toBe("质检出错: Error: x");
    act(() => store().startGlobalLoading("忙"));
    await act(async () => { await nodeById("quality-final").data.onGenerate(); });
    expect(mockCheckQuality).toHaveBeenCalledTimes(3);
  });

  it("视觉质检：成功/失败/异常/全局锁", async () => {
    act(() => {
      store().setScriptData(makeScript());
      store().addVideo(vd1);
    });
    render(<Canvas />);
    mockCheckVisualQuality.mockResolvedValueOnce({ success: true, data: vq1 });
    await act(async () => { await nodeById("visual-quality-final").data.onGenerate(); });
    expect(mockCheckVisualQuality).toHaveBeenCalledWith(expect.objectContaining({
      project_id: "p1",
      scene_id: 1,
      video_url: "http://v/1.mp4",
      max_frames: 6,
    }));
    expect(store().visualQualityData?.score).toBe(92);
    expect(store().statusInfo).toBe("视觉质检完成: 场景 1 | 质量分 92 | 0 严重");
    mockCheckVisualQuality.mockResolvedValueOnce({ success: false, error: "VLM 离线" });
    await act(async () => { await nodeById("visual-quality-final").data.onGenerate(); });
    expect(store().statusInfo).toBe("视觉质检失败: VLM 离线");
    mockCheckVisualQuality.mockRejectedValueOnce(new Error("x"));
    await act(async () => { await nodeById("visual-quality-final").data.onGenerate(); });
    expect(store().statusInfo).toBe("视觉质检出错: Error: x");
    act(() => store().startGlobalLoading("忙"));
    await act(async () => { await nodeById("visual-quality-final").data.onGenerate(); });
    expect(mockCheckVisualQuality).toHaveBeenCalledTimes(3);
  });
});
