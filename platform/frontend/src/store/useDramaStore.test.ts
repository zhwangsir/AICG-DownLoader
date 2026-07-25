import { useDramaStore } from "./useDramaStore";
import type { ScriptData, StoryboardData, VideoData, VoiceData, SubtitleData, EditData, QualityCheckData, QualityVisualData, LipSyncData, PostprocessData } from "../api/client";

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
const sampleLipSync: LipSyncData = { scene_id: 1, video_url: "http://x/ls.mp4", original_video_url: "http://x/v.mp4", synced: true, elapsed_seconds: 12.5 };
const samplePostprocess: PostprocessData = {
  scene_id: 1,
  final_video_url: "http://x/pp.mp4",
  original_video_url: "http://x/v.mp4",
  steps: [
    { step: "super_resolution", success: true, output_url: "http://x/sr.mp4", elapsed_seconds: 30, message: "ok", skipped: false },
    { step: "final_encode", success: true, output_url: "http://x/pp.mp4", elapsed_seconds: 5, message: "ok", skipped: false },
  ],
  success: true,
  elapsed_seconds: 35,
};

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

  it("should toggle lipSync and postprocess modals", () => {
    useDramaStore.getState().setModal("lipSync", true);
    useDramaStore.getState().setModal("postprocess", true);
    expect(useDramaStore.getState().modals.lipSync).toBe(true);
    expect(useDramaStore.getState().modals.postprocess).toBe(true);
  });

  it("should add and replace lipSyncs by scene_id", () => {
    useDramaStore.getState().addLipSync(sampleLipSync);
    expect(useDramaStore.getState().lipSyncs).toHaveLength(1);
    useDramaStore.getState().addLipSync({ ...sampleLipSync, video_url: "http://x/ls2.mp4" });
    const lipSyncs = useDramaStore.getState().lipSyncs;
    expect(lipSyncs).toHaveLength(1);
    expect(lipSyncs[0].video_url).toBe("http://x/ls2.mp4");
  });

  it("should add and replace postprocesses by scene_id", () => {
    useDramaStore.getState().addPostprocess(samplePostprocess);
    expect(useDramaStore.getState().postprocesses).toHaveLength(1);
    useDramaStore.getState().addPostprocess({ ...samplePostprocess, success: false });
    const postprocesses = useDramaStore.getState().postprocesses;
    expect(postprocesses).toHaveLength(1);
    expect(postprocesses[0].success).toBe(false);
  });

  it("should clear lipSyncs and postprocesses when script is reset", () => {
    const state = useDramaStore.getState();
    state.addLipSync(sampleLipSync);
    state.addPostprocess(samplePostprocess);
    state.setScriptData(sampleScript);
    expect(useDramaStore.getState().lipSyncs).toEqual([]);
    expect(useDramaStore.getState().postprocesses).toEqual([]);
  });
});
