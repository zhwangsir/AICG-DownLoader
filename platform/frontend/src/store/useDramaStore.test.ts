import { useDramaStore } from "./useDramaStore";
import type { ScriptData, StoryboardData, VideoData, VoiceData, SubtitleData, EditData, QualityCheckData, QualityVisualData } from "../api/client";

const sampleScript: ScriptData = {
  project_id: "p1",
  title: "测试短剧",
  genre: "都市悬疑",
  aspect_ratio: "9:16",
  total_episodes: 1,
  characters: [{ character_id: "c1", name: "Alice", role: "主角", age: 26, description: "主角", personality: "" }],
  scenes: [{ scene_id: 1, episode: 1, shot_type: "中景", description: "开场", prompt: "", negative_prompt: "", dialogue: "", emotion: "neutral", duration_seconds: 5, camera_movement: "static" }],
};

const sampleStoryboard: StoryboardData = { scene_id: 1, image_url: "http://x/sb.jpg", prompt_used: "" };
const sampleVideo: VideoData = { scene_id: 1, video_url: "http://x/v.mp4", duration_seconds: 3 };
const sampleVoice: VoiceData = { scene_id: 1, audio_urls: [], total_lines: 0 };
const sampleSubtitle: SubtitleData = { scene_id: 1, srt_content: "", segments: [], language: "zh", srt_url: "" };
const sampleEdit: EditData = { project_id: "p1", title: "成片", final_video_url: "http://x/f.mp4", duration_seconds: 10, segments_count: 1 };
const sampleQuality: QualityCheckData = { project_id: "p1", title: "测试短剧", score: 90, summary: "", issues: [], checked_at: 0 };
const sampleVisualQuality: QualityVisualData = { project_id: "p1", title: "测试短剧", scene_id: 1, score: 85, summary: "", issues: [], checked_at: 0 };

describe("useDramaStore", () => {
  beforeEach(() => {
    useDramaStore.getState().reset();
  });

  it("should initialize with default state", () => {
    const state = useDramaStore.getState();
    expect(state.scriptData).toBeNull();
    expect(state.storyboards).toEqual([]);
    expect(state.statusInfo).toBe("就绪");
    expect(state.modals.script).toBe(false);
  });

  it("should set modal state", () => {
    useDramaStore.getState().setModal("script", true);
    expect(useDramaStore.getState().modals.script).toBe(true);
  });

  it("should set script data and reset downstream state", () => {
    useDramaStore.getState().setScriptData(sampleScript);
    const state = useDramaStore.getState();
    expect(state.scriptData).toEqual(sampleScript);
    expect(state.storyboards).toEqual([]);
    expect(state.videos).toEqual([]);
  });

  it("should add and replace storyboards", () => {
    useDramaStore.getState().addStoryboard(sampleStoryboard);
    expect(useDramaStore.getState().storyboards).toHaveLength(1);
    useDramaStore.getState().addStoryboard({ ...sampleStoryboard, image_url: "http://x/sb2.jpg" });
    expect(useDramaStore.getState().storyboards).toHaveLength(1);
    expect(useDramaStore.getState().storyboards[0].image_url).toBe("http://x/sb2.jpg");
  });

  it("should add and replace videos", () => {
    useDramaStore.getState().addVideo(sampleVideo);
    expect(useDramaStore.getState().videos).toHaveLength(1);
  });

  it("should add and replace voices", () => {
    useDramaStore.getState().addVoice(sampleVoice);
    expect(useDramaStore.getState().voices).toHaveLength(1);
  });

  it("should add and replace subtitles", () => {
    useDramaStore.getState().addSubtitle(sampleSubtitle);
    expect(useDramaStore.getState().subtitles).toHaveLength(1);
  });

  it("should set edit, quality and visual quality data", () => {
    useDramaStore.getState().setEditData(sampleEdit);
    useDramaStore.getState().setQualityData(sampleQuality);
    useDramaStore.getState().setVisualQualityData(sampleVisualQuality);
    const state = useDramaStore.getState();
    expect(state.editData).toEqual(sampleEdit);
    expect(state.qualityData).toEqual(sampleQuality);
    expect(state.visualQualityData).toEqual(sampleVisualQuality);
  });

  it("should set status info", () => {
    useDramaStore.getState().setStatusInfo("测试中");
    expect(useDramaStore.getState().statusInfo).toBe("测试中");
  });

  it("should reset all state", () => {
    const state = useDramaStore.getState();
    state.setScriptData(sampleScript);
    state.addStoryboard(sampleStoryboard);
    state.setEditData(sampleEdit);
    state.reset();
    expect(useDramaStore.getState().scriptData).toBeNull();
    expect(useDramaStore.getState().storyboards).toEqual([]);
    expect(useDramaStore.getState().editData).toBeNull();
    expect(useDramaStore.getState().statusInfo).toBe("就绪");
  });
});

describe("useDramaStore — 任务中心切片（DramaClaw 对标）", () => {
  beforeEach(() => {
    useDramaStore.getState().reset();
  });

  const task = (id: string, patch: Partial<import("./useDramaStore").TaskEntry> = {}) => ({
    id,
    label: `任务${id}`,
    kind: "pipeline" as const,
    status: "running" as const,
    percent: 0,
    message: "启动",
    startedAt: Date.now(),
    ...patch,
  });

  it("upsertTask 新增；同 id 替换不重复", () => {
    const store = useDramaStore.getState();
    store.upsertTask(task("a"));
    store.upsertTask(task("b"));
    expect(useDramaStore.getState().tasks).toHaveLength(2);
    store.upsertTask(task("a", { percent: 50, message: "更新" }));
    const tasks = useDramaStore.getState().tasks;
    expect(tasks).toHaveLength(2);
    expect(tasks.find((t) => t.id === "a")?.percent).toBe(50);
  });

  it("patchTask 局部更新指定任务", () => {
    const store = useDramaStore.getState();
    store.upsertTask(task("a"));
    store.patchTask("a", { status: "completed", percent: 100 });
    const t = useDramaStore.getState().tasks[0];
    expect(t.status).toBe("completed");
    expect(t.percent).toBe(100);
    expect(t.label).toBe("任务a"); // 未触碰字段保留
  });

  it("patchTask 对不存在 id 静默无操作", () => {
    useDramaStore.getState().patchTask("ghost", { percent: 99 });
    expect(useDramaStore.getState().tasks).toHaveLength(0);
  });

  it("removeTask 删除指定任务", () => {
    const store = useDramaStore.getState();
    store.upsertTask(task("a"));
    store.upsertTask(task("b"));
    store.removeTask("a");
    expect(useDramaStore.getState().tasks.map((t) => t.id)).toEqual(["b"]);
  });

  it("clearFinishedTasks 仅保留 running", () => {
    const store = useDramaStore.getState();
    store.upsertTask(task("a"));
    store.upsertTask(task("b", { status: "completed" }));
    store.upsertTask(task("c", { status: "failed" }));
    store.clearFinishedTasks();
    expect(useDramaStore.getState().tasks.map((t) => t.id)).toEqual(["a"]);
  });

  it("setPipelineStream 登记/注销流", () => {
    const store = useDramaStore.getState();
    store.setPipelineStream("a", "http://x/stream");
    expect(useDramaStore.getState().pipelineStreams["a"]).toBe("http://x/stream");
    store.setPipelineStream("a", null);
    expect(useDramaStore.getState().pipelineStreams["a"]).toBeUndefined();
  });

  it("tasks/pipelineStreams 不参与持久化（partialize 排除）", () => {
    const store = useDramaStore.getState();
    store.upsertTask(task("a"));
    store.setPipelineStream("a", "http://x");
    const persisted = JSON.parse(localStorage.getItem("aicg-drama-store") || "{}");
    expect(persisted?.state?.tasks).toBeUndefined();
    expect(persisted?.state?.pipelineStreams).toBeUndefined();
  });
});
