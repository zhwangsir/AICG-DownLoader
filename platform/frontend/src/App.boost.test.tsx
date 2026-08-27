import { fireEvent, render, screen, within } from "@testing-library/react";
import App from "./App";
import { useDramaStore } from "./store/useDramaStore";
import type { ScriptData } from "./api/client";

/**
 * App.tsx 覆盖率补缺（boost）：
 * 既有 App.test.tsx 未覆盖 9 个 onSuccess/onClose/onUpdate 回调与 9 个模态渲染块。
 * 本文件将 modals 入口替换为轻量 stub（暴露 props 与回调触发按钮），
 * 专注验证 App 层的回调接线、状态流转与 props 透传回退分支。
 */
vi.mock("./components/modals", () => {
  const scriptFixture = {
    project_id: "p-boost",
    title: "Boost剧本",
    genre: "都市",
    aspect_ratio: "9:16",
    total_episodes: 1,
    characters: [
      { character_id: "cb1", name: "阿升", role: "主角", age: 25, description: "d", personality: "p" },
    ],
    scenes: [
      { scene_id: 7, episode: 1, shot_type: "中景", description: "d", prompt: "", negative_prompt: "", character_actions: "", dialogue: "", emotion: "e", duration_seconds: 4, camera_movement: "static" },
    ],
  };
  const storyboardFixture = { scene_id: 7, image_url: "http://x/sb7.jpg", prompt_used: "p" };
  const videoFixture = { scene_id: 7, video_url: "http://x/v7.mp4", duration_seconds: 4 };
  const voiceFixture = {
    scene_id: 7,
    audio_urls: [
      { filename: "a.wav", voice: "v", text: "t", audio_url: "u" },
      { filename: "b.wav", voice: "v", text: "t", audio_url: "u" },
    ],
    total_lines: 2,
  };
  const subtitleFixture = {
    scene_id: 7,
    srt_content: "srt",
    segments: [
      { start: 0, end: 1, text: "一" },
      { start: 1, end: 2, text: "二" },
    ],
    language: "zh",
    srt_url: "u",
  };
  const editFixture = { project_id: "p-boost", title: "Boost成片", final_video_url: "http://x/f.mp4", duration_seconds: 12.34, segments_count: 2 };
  const qualityFixture = {
    project_id: "p-boost",
    title: "Boost剧本",
    score: 90,
    summary: "",
    issues: [
      { category: "c", severity: "critical", scene_id: 7, message: "m", suggestion: "s" },
      { category: "c", severity: "warning", scene_id: null, message: "m", suggestion: "s" },
      { category: "c", severity: "warning", scene_id: 8, message: "m", suggestion: "s" },
    ],
    checked_at: 0,
  };
  const visualQualityFixture = {
    project_id: "p-boost",
    title: "Boost剧本",
    scene_id: 7,
    score: 80,
    summary: "",
    issues: [
      { category: "c", severity: "critical", timestamp: 1.2, message: "m", suggestion: "s" },
      { category: "c", severity: "warning", timestamp: null, message: "m", suggestion: "s" },
    ],
    checked_at: 0,
  };

  const Btn = ({ id, onClick }: { id: string; onClick: () => void }) => (
    <button data-testid={id} onClick={onClick}>
      {id}
    </button>
  );

  return {
    ScriptModal: (props: any) => (
      <div data-testid="modal-script">
        <span data-testid="script-prop-title">{props.scriptData ? props.scriptData.title : "(null)"}</span>
        <Btn id="script-success" onClick={() => props.onSuccess(scriptFixture)} />
        <Btn id="script-update" onClick={() => props.onUpdate()} />
        <Btn id="script-close" onClick={props.onClose} />
      </div>
    ),
    CharacterModal: (props: any) => (
      <div data-testid="modal-character">
        <span data-testid="character-count">{props.characters.length}</span>
        <Btn id="character-success" onClick={() => props.onSuccess("阿升")} />
        <Btn id="character-close" onClick={props.onClose} />
      </div>
    ),
    StoryboardModal: (props: any) => (
      <div data-testid="modal-storyboard">
        <span data-testid="storyboard-scene-count">{props.scenes.length}</span>
        <span data-testid="storyboard-char-count">{props.characters.length}</span>
        <Btn id="storyboard-success" onClick={() => props.onSuccess(storyboardFixture)} />
        <Btn id="storyboard-close" onClick={props.onClose} />
      </div>
    ),
    VideoModal: (props: any) => (
      <div data-testid="modal-video">
        <span data-testid="video-storyboard-count">{props.storyboards.length}</span>
        <span data-testid="video-scene-count">{props.scenes.length}</span>
        <Btn id="video-success" onClick={() => props.onSuccess(videoFixture)} />
        <Btn id="video-close" onClick={props.onClose} />
      </div>
    ),
    VoiceModal: (props: any) => (
      <div data-testid="modal-voice">
        <span data-testid="voice-scene-count">{props.scenes.length}</span>
        <Btn id="voice-success" onClick={() => props.onSuccess(voiceFixture)} />
        <Btn id="voice-close" onClick={props.onClose} />
      </div>
    ),
    SubtitleModal: (props: any) => (
      <div data-testid="modal-subtitle">
        <span data-testid="subtitle-voice-count">{props.voices.length}</span>
        <Btn id="subtitle-success" onClick={() => props.onSuccess(subtitleFixture)} />
        <Btn id="subtitle-close" onClick={props.onClose} />
      </div>
    ),
    EditModal: (props: any) => (
      <div data-testid="modal-edit">
        <span data-testid="edit-counts">{`${props.videos.length}/${props.voices.length}/${props.subtitles.length}`}</span>
        <Btn id="edit-success" onClick={() => props.onSuccess(editFixture)} />
        <Btn id="edit-close" onClick={props.onClose} />
      </div>
    ),
    QualityModal: (props: any) => (
      <div data-testid="modal-quality">
        <span data-testid="quality-subtitle-count">{props.subtitles.length}</span>
        <Btn id="quality-success" onClick={() => props.onSuccess(qualityFixture)} />
        <Btn id="quality-close" onClick={props.onClose} />
      </div>
    ),
    VisualQualityModal: (props: any) => (
      <div data-testid="modal-visualQuality">
        <span data-testid="visual-title">{props.title}</span>
        <span data-testid="visual-video-count">{props.videos.length}</span>
        <Btn id="visual-success" onClick={() => props.onSuccess(visualQualityFixture)} />
        <Btn id="visual-close" onClick={props.onClose} />
      </div>
    ),
    PipelineModal: (props: any) => (
      <div data-testid="modal-pipeline">
        <Btn id="pipeline-close" onClick={props.onClose} />
      </div>
    ),
  };
});

const sampleScript: ScriptData = {
  project_id: "p1",
  title: "测试短剧",
  genre: "都市悬疑",
  aspect_ratio: "9:16",
  total_episodes: 1,
  characters: [{ character_id: "c1", name: "Alice", role: "主角", age: 26, description: "主角", personality: "" }],
  scenes: [{ scene_id: 1, episode: 1, shot_type: "中景", description: "开场", prompt: "", negative_prompt: "", dialogue: "", emotion: "neutral", duration_seconds: 5, camera_movement: "static" }],
};

const getTopbar = () => {
  const title = screen.getByText("DashBox");
  return title.closest(".topbar") as HTMLElement;
};
const openFlowMenu = () => {
  fireEvent.click(within(getTopbar()).getByText("操作流程"));
  return getTopbar().querySelector(".dropdown-menu") as HTMLElement;
};

/** 将 store 填充到「全步骤完成」状态（顺序敏感：setScriptData 会清空下游） */
function fillAllDone() {
  const s = useDramaStore.getState();
  s.setScriptData(sampleScript);
  s.addCharacterCard({ character_id: "c1", name: "Alice", reference_images: {}, consistency_level: "high" });
  s.addStoryboard({ scene_id: 1, image_url: "http://x/sb.jpg", prompt_used: "" });
  s.addVideo({ scene_id: 1, video_url: "http://x/v.mp4", duration_seconds: 3 });
  s.addVoice({ scene_id: 1, audio_urls: [], total_lines: 0 });
  s.addSubtitle({ scene_id: 1, srt_content: "", segments: [], language: "zh", srt_url: "" });
  s.setEditData({ project_id: "p1", title: "成片", final_video_url: "http://x/f.mp4", duration_seconds: 10, segments_count: 1 });
  s.setQualityData({ project_id: "p1", title: "测试短剧", score: 90, summary: "", issues: [], checked_at: 0 });
}

describe("App boost — 模态 onSuccess/onClose/onUpdate 回调接线", () => {
  beforeEach(() => {
    useDramaStore.getState().reset();
  });

  it("ScriptModal onSuccess：写入剧本 + 状态栏摘要 + 关模态", () => {
    useDramaStore.getState().setModal("script", true);
    render(<App />);
    fireEvent.click(screen.getByTestId("script-success"));
    const s = useDramaStore.getState();
    expect(s.scriptData?.title).toBe("Boost剧本");
    expect(s.statusInfo).toBe("剧本已生成: Boost剧本 | 1 角色 | 1 分镜");
    expect(s.modals.script).toBe(false);
  });

  it("ScriptModal onUpdate：提示已保存并关模态", () => {
    useDramaStore.getState().setModal("script", true);
    render(<App />);
    fireEvent.click(screen.getByTestId("script-update"));
    expect(useDramaStore.getState().statusInfo).toBe("剧本修改已保存");
    expect(useDramaStore.getState().modals.script).toBe(false);
  });

  it("ScriptModal onClose：仅关模态；scriptData 透传给模态", () => {
    useDramaStore.getState().setScriptData(sampleScript);
    useDramaStore.getState().setModal("script", true);
    render(<App />);
    expect(screen.getByTestId("script-prop-title")).toHaveTextContent("测试短剧");
    fireEvent.click(screen.getByTestId("script-close"));
    expect(useDramaStore.getState().modals.script).toBe(false);
    expect(useDramaStore.getState().scriptData?.title).toBe("测试短剧");
  });

  it("ScriptModal 无剧本时 scriptData prop 为 null 分支", () => {
    useDramaStore.getState().setModal("script", true);
    render(<App />);
    expect(screen.getByTestId("script-prop-title")).toHaveTextContent("(null)");
  });

  it("CharacterModal onSuccess：状态栏提示定妆照并关模态；characters 透传", () => {
    useDramaStore.getState().setScriptData(sampleScript);
    useDramaStore.getState().setModal("character", true);
    render(<App />);
    expect(screen.getByTestId("character-count")).toHaveTextContent("1");
    fireEvent.click(screen.getByTestId("character-success"));
    expect(useDramaStore.getState().statusInfo).toBe("角色定妆照已生成: 阿升");
    expect(useDramaStore.getState().modals.character).toBe(false);
  });

  it("CharacterModal onClose + 无剧本时 characters 回退空数组", () => {
    useDramaStore.getState().setModal("character", true);
    render(<App />);
    expect(screen.getByTestId("character-count")).toHaveTextContent("0");
    fireEvent.click(screen.getByTestId("character-close"));
    expect(useDramaStore.getState().modals.character).toBe(false);
  });

  it("StoryboardModal onSuccess：分镜入库 + 状态栏（不关模态）；scenes/characters 透传", () => {
    useDramaStore.getState().setScriptData(sampleScript);
    useDramaStore.getState().setModal("storyboard", true);
    render(<App />);
    expect(screen.getByTestId("storyboard-scene-count")).toHaveTextContent("1");
    expect(screen.getByTestId("storyboard-char-count")).toHaveTextContent("1");
    fireEvent.click(screen.getByTestId("storyboard-success"));
    const s = useDramaStore.getState();
    expect(s.storyboards.map((x) => x.scene_id)).toEqual([7]);
    expect(s.statusInfo).toBe("分镜关键帧已生成: 场景 7");
    expect(s.modals.storyboard).toBe(true); // 该 handler 不关模态
    fireEvent.click(screen.getByTestId("storyboard-close"));
    expect(useDramaStore.getState().modals.storyboard).toBe(false);
  });

  it("StoryboardModal 无剧本时 scenes/characters 回退空数组", () => {
    useDramaStore.getState().setModal("storyboard", true);
    render(<App />);
    expect(screen.getByTestId("storyboard-scene-count")).toHaveTextContent("0");
    expect(screen.getByTestId("storyboard-char-count")).toHaveTextContent("0");
  });

  it("VideoModal onSuccess：视频入库 + 状态栏时长；storyboards/scenes 透传", () => {
    fillAllDone();
    useDramaStore.getState().setModal("video", true);
    render(<App />);
    expect(screen.getByTestId("video-storyboard-count")).toHaveTextContent("1");
    expect(screen.getByTestId("video-scene-count")).toHaveTextContent("1");
    fireEvent.click(screen.getByTestId("video-success"));
    const s = useDramaStore.getState();
    expect(s.videos.map((x) => x.scene_id)).toContain(7);
    expect(s.statusInfo).toBe("视频片段已生成: 场景 7 (4s)");
    fireEvent.click(screen.getByTestId("video-close"));
    expect(useDramaStore.getState().modals.video).toBe(false);
  });

  it("VoiceModal onSuccess：配音入库 + 状态栏条数；scenes 透传", () => {
    useDramaStore.getState().setScriptData(sampleScript);
    useDramaStore.getState().setModal("voice", true);
    render(<App />);
    expect(screen.getByTestId("voice-scene-count")).toHaveTextContent("1");
    fireEvent.click(screen.getByTestId("voice-success"));
    const s = useDramaStore.getState();
    expect(s.voices.map((x) => x.scene_id)).toEqual([7]);
    expect(s.statusInfo).toBe("配音已生成: 场景 7 (2 条语音)");
    fireEvent.click(screen.getByTestId("voice-close"));
    expect(useDramaStore.getState().modals.voice).toBe(false);
  });

  it("SubtitleModal onSuccess：字幕入库 + 状态栏段数；voices 透传", () => {
    fillAllDone();
    useDramaStore.getState().setModal("subtitle", true);
    render(<App />);
    expect(screen.getByTestId("subtitle-voice-count")).toHaveTextContent("1");
    fireEvent.click(screen.getByTestId("subtitle-success"));
    const s = useDramaStore.getState();
    expect(s.subtitles.map((x) => x.scene_id)).toContain(7);
    expect(s.statusInfo).toBe("字幕已生成: 场景 7 (2 段)");
    fireEvent.click(screen.getByTestId("subtitle-close"));
    expect(useDramaStore.getState().modals.subtitle).toBe(false);
  });

  it("EditModal onSuccess：成片入库 + 状态栏（toFixed 一位小数）+ 关模态；三路数组透传", () => {
    fillAllDone();
    useDramaStore.getState().setModal("edit", true);
    render(<App />);
    expect(screen.getByTestId("edit-counts")).toHaveTextContent("1/1/1");
    fireEvent.click(screen.getByTestId("edit-success"));
    const s = useDramaStore.getState();
    expect(s.editData?.title).toBe("Boost成片");
    expect(s.statusInfo).toBe("成片已合成: Boost成片 | 2 场景 | 12.3s");
    expect(s.modals.edit).toBe(false);
  });

  it("QualityModal onSuccess：质检入库 + critical/warning 统计 + 关模态；subtitles 透传", () => {
    fillAllDone();
    useDramaStore.getState().setModal("quality", true);
    render(<App />);
    expect(screen.getByTestId("quality-subtitle-count")).toHaveTextContent("1");
    fireEvent.click(screen.getByTestId("quality-success"));
    const s = useDramaStore.getState();
    expect(s.qualityData?.score).toBe(90);
    expect(s.statusInfo).toBe("质检完成: Boost剧本 | 质量分 90 | critical 1 | warning 2");
    expect(s.modals.quality).toBe(false);
  });

  it("VisualQualityModal onSuccess：视觉质检入库 + 统计 + 关模态；videos/title 透传", () => {
    fillAllDone();
    useDramaStore.getState().setModal("visualQuality", true);
    render(<App />);
    expect(screen.getByTestId("visual-video-count")).toHaveTextContent("1");
    expect(screen.getByTestId("visual-title")).toHaveTextContent("测试短剧");
    fireEvent.click(screen.getByTestId("visual-success"));
    const s = useDramaStore.getState();
    expect(s.visualQualityData?.scene_id).toBe(7);
    expect(s.statusInfo).toBe("视觉质检完成: 场景 7 | 质量分 80 | critical 1 | warning 1");
    expect(s.modals.visualQuality).toBe(false);
  });

  it("VisualQualityModal 无剧本时 title 回退「未命名短剧」", () => {
    useDramaStore.getState().setModal("visualQuality", true);
    render(<App />);
    expect(screen.getByTestId("visual-title")).toHaveTextContent("未命名短剧");
    fireEvent.click(screen.getByTestId("visual-close"));
    expect(useDramaStore.getState().modals.visualQuality).toBe(false);
  });

  it("EditModal onClose 关闭模态", () => {
    // 预填成片数据：onClose 必须「只关模态、零副作用」——
    // 若误接成 onSuccess(handleEditGenerated)，数据会被覆写且状态栏被改写
    const existing = {
      project_id: "p1",
      title: "既有成片",
      final_video_url: "http://x/old.mp4",
      duration_seconds: 8,
      segments_count: 1,
    };
    useDramaStore.getState().setEditData(existing);
    useDramaStore.getState().setModal("edit", true);
    render(<App />);
    expect(screen.getByTestId("modal-edit")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("edit-close"));
    const s = useDramaStore.getState();
    expect(s.modals.edit).toBe(false);
    expect(s.editData).toEqual(existing); // 未被覆写/清空
    expect(s.statusInfo).toBe("就绪"); // 未走 onSuccess 的状态栏改写
  });

  it("QualityModal onClose 关闭模态", () => {
    // 同 EditModal：预填质检数据，区分 onClose 与 onSuccess 接线
    const existing = {
      project_id: "p1",
      title: "既有质检",
      score: 77,
      summary: "s",
      issues: [],
      checked_at: 0,
    };
    useDramaStore.getState().setQualityData(existing);
    useDramaStore.getState().setModal("quality", true);
    render(<App />);
    expect(screen.getByTestId("modal-quality")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("quality-close"));
    const s = useDramaStore.getState();
    expect(s.modals.quality).toBe(false);
    expect(s.qualityData).toEqual(existing);
    expect(s.statusInfo).toBe("就绪");
  });

  it("PipelineModal onClose 关闭模态", () => {
    useDramaStore.getState().setModal("pipeline", true);
    render(<App />);
    expect(screen.getByTestId("modal-pipeline")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("pipeline-close"));
    expect(useDramaStore.getState().modals.pipeline).toBe(false);
  });
});

describe("App boost — 顶栏步骤与状态点分支", () => {
  beforeEach(() => {
    useDramaStore.getState().reset();
  });

  it("全步骤完成：8 个步骤全 done，activeStepIndex 回退末位（无 active）", () => {
    fillAllDone();
    const { container } = render(<App />);
    expect(container.querySelectorAll(".topbar-step.done").length).toBe(8);
    expect(container.querySelector(".topbar-step.active")).not.toBeInTheDocument();
  });

  it("部分完成：下一个未完成步骤呈 active 态", () => {
    useDramaStore.getState().setScriptData(sampleScript);
    const { container } = render(<App />);
    const active = container.querySelector(".topbar-step.active");
    expect(active).toBeInTheDocument();
    expect(active!.textContent).toContain("角色");
  });

  it("全步骤完成后「操作流程」8 个菜单项全部可用", () => {
    fillAllDone();
    render(<App />);
    const menu = openFlowMenu();
    menu.querySelectorAll(".dropdown-item").forEach((item) => {
      expect(item).not.toBeDisabled();
    });
  });

  it("statusInfo 含「失败」时状态点呈 error 态", () => {
    useDramaStore.getState().setStatusInfo("生成失败: LLM 离线");
    const { container } = render(<App />);
    expect(container.querySelector(".status-dot.error")).toBeInTheDocument();
    expect(screen.getByText("生成失败: LLM 离线")).toBeInTheDocument();
  });

  it("statusInfo 含「错误」时状态点同样呈 error 态", () => {
    useDramaStore.getState().setStatusInfo("请求错误: 超时");
    const { container } = render(<App />);
    expect(container.querySelector(".status-dot.error")).toBeInTheDocument();
  });

  it("globalLoading 时状态点呈 loading 态", () => {
    useDramaStore.getState().startGlobalLoading("全链路生成中");
    const { container } = render(<App />);
    expect(container.querySelector(".status-dot.loading")).toBeInTheDocument();
    expect(container.querySelector(".status-dot.error")).not.toBeInTheDocument();
  });
});

describe("App boost — 顶栏动作按钮打开模态", () => {
  beforeEach(() => {
    useDramaStore.getState().reset();
  });

  it("「一键成片」打开 PipelineModal", () => {
    render(<App />);
    // 前置：模态初始关闭，翻转只能来自本次 onClick
    expect(useDramaStore.getState().modals.pipeline).toBe(false);
    fireEvent.click(within(getTopbar()).getByText("一键成片"));
    const s = useDramaStore.getState();
    expect(s.modals.pipeline).toBe(true);
    // 互斥：不影响其他模态入口
    expect(s.modals.script).toBe(false);
    expect(screen.getByTestId("modal-pipeline")).toBeInTheDocument();
  });

  it("「新建剧本」打开 ScriptModal", () => {
    render(<App />);
    expect(useDramaStore.getState().modals.script).toBe(false);
    fireEvent.click(within(getTopbar()).getByText("新建剧本"));
    const s = useDramaStore.getState();
    expect(s.modals.script).toBe(true);
    expect(s.modals.pipeline).toBe(false);
    expect(screen.getByTestId("modal-script")).toBeInTheDocument();
  });
});
