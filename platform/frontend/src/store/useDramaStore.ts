import { create } from "zustand";
import { persist } from "zustand/middleware";
import type {
  ScriptData,
  StoryboardData,
  VideoData,
  VoiceData,
  SubtitleData,
  EditData,
  QualityCheckData,
  QualityVisualData,
  CharacterData,
  CharacterCardData,
  SceneData,
  SubtitleSegment,
} from "../api/client";

export interface ModalsState {
  script: boolean;
  character: boolean;
  storyboard: boolean;
  video: boolean;
  voice: boolean;
  subtitle: boolean;
  edit: boolean;
  quality: boolean;
  visualQuality: boolean;
  pipeline: boolean;
  /** M27 NSFW 门禁（PIN 输入/设置/修改） */
  nsfwGate: boolean;
}

/** 角色生成预览/确认阶段 */
export type CharacterPreviewStage =
  | "idle"          // 未打开预览
  | "searching"     // AI 联网搜索中
  | "editing"       // 用户可编辑（默认）
  | "generating"    // 生成中
  | "completed";    // 生成完成

/** 角色生成预览数据 */
export interface CharacterPreviewData {
  character_id: string;
  character: CharacterData;
  style: string;
  /** AI 联网搜索结果 */
  searchReference: string;
  /** LLM 生成的提示词 */
  generatedPrompts: {
    front_view_prompt: string;
    side_view_prompt: string;
    closeup_prompt: string;
    negative_prompt: string;
  };
  /** 用户编辑后的提示词 */
  editedPrompts: {
    front_view_prompt: string;
    side_view_prompt: string;
    closeup_prompt: string;
    negative_prompt: string;
  };
  stage: CharacterPreviewStage;
  error?: string;
}

/** DramaClaw 式任务中心条目：统一登记全局长任务（管线/批量/单镜视频） */
export interface TaskEntry {
  id: string;
  label: string;
  kind: "pipeline" | "video" | "batch";
  status: "running" | "completed" | "failed";
  /** 0-100；无法细分进度的任务由调用方按阶段跳变（0→100） */
  percent: number;
  message: string;
  error?: string;
  startedAt: number;
}

interface DramaState {
  modals: ModalsState;
  scriptData: ScriptData | null;
  storyboards: StoryboardData[];
  videos: VideoData[];
  voices: VoiceData[];
  subtitles: SubtitleData[];
  editData: EditData | null;
  qualityData: QualityCheckData | null;
  visualQualityData: QualityVisualData | null;
  statusInfo: string;

  /** 项目级全局视觉风格，影响角色、分镜、视频生成 */
  projectStyle: string;

  /** 全局生成状态：任一 Agent 生成中为 true，禁用所有生成按钮 */
  globalLoading: boolean;
  globalLoadingText: string;

  /** 角色生成预览数据（按角色 ID 索引） */
  characterPreviews: Record<string, CharacterPreviewData>;

  /** 角色生成结果（定妆照），持久化以保留生成完成状态 */
  characterCards: CharacterCardData[];

  /** LibTV 式左侧导航激活的资产面板（null=收起，不持久化） */
  activePanel: "characters" | "models" | "engine" | null;

  /** M27 NSFW 状态（由后端 /api/settings/nsfw 同步，不持久化） */
  nsfwEnabled: boolean;
  nsfwHasPin: boolean;
  setNsfwState: (enabled: boolean, hasPin: boolean) => void;

  /** AgentBar 输入的创意草稿（打开剧本模态时预填，读后即清，不持久化） */
  draftPremise: string;

  /** 最近一次全链路任务的 project_id（shot_params.json 目录名，锚点重拍定位用，不持久化） */
  pipelineProjectId: string;

  /** 任务中心：全局长任务登记表（DramaClaw 任务中心对标，不持久化） */
  tasks: TaskEntry[];
  /** 运行中的 pipeline SSE 流（task_id → stream_url；TaskCenter watcher 订阅用，不持久化） */
  pipelineStreams: Record<string, string>;

  upsertTask: (task: TaskEntry) => void;
  patchTask: (id: string, patch: Partial<TaskEntry>) => void;
  removeTask: (id: string) => void;
  /** 清除全部已完成/失败任务 */
  clearFinishedTasks: () => void;
  setPipelineStream: (taskId: string, streamUrl: string | null) => void;

  setActivePanel: (panel: "characters" | "models" | "engine" | null) => void;
  setDraftPremise: (text: string) => void;
  setPipelineProjectId: (id: string) => void;

  setModal: (key: keyof ModalsState, open: boolean) => void;
  setScriptData: (data: ScriptData | null) => void;
  /** 局部更新剧本字段（不清空下游数据） */
  updateScriptField: (patch: Partial<ScriptData>) => void;
  /** 更新单个角色 */
  updateCharacter: (charId: string, patch: Partial<CharacterData>) => void;
  /** 更新单个场景 */
  updateScene: (sceneId: number, patch: Partial<SceneData>) => void;
  /** 更新单条字幕文本 */
  updateSubtitleSegment: (sceneId: number, index: number, text: string) => void;
  addStoryboard: (data: StoryboardData) => void;
  addVideo: (data: VideoData) => void;
  addVoice: (data: VoiceData) => void;
  addSubtitle: (data: SubtitleData) => void;
  setEditData: (data: EditData | null) => void;
  setQualityData: (data: QualityCheckData | null) => void;
  setVisualQualityData: (data: QualityVisualData | null) => void;
  setStatusInfo: (info: string) => void;

  /** 项目级全局视觉风格 */
  setProjectStyle: (style: string) => void;

  /** 全局生成状态 */
  startGlobalLoading: (text: string) => void;
  stopGlobalLoading: () => void;

  /** 角色预览数据管理 */
  setCharacterPreview: (charId: string, data: CharacterPreviewData | null) => void;
  updateCharacterPreview: (charId: string, patch: Partial<CharacterPreviewData>) => void;
  updateCharacterPreviewPrompt: (
    charId: string,
    view: keyof CharacterPreviewData["editedPrompts"],
    value: string
  ) => void;

  /** 角色生成结果管理 */
  addCharacterCard: (data: CharacterCardData) => void;

  reset: () => void;
}

const initialModals: ModalsState = {
  script: false,
  character: false,
  storyboard: false,
  video: false,
  voice: false,
  subtitle: false,
  edit: false,
  quality: false,
  visualQuality: false,
  pipeline: false,
  nsfwGate: false,
};

const initialState = {
  modals: { ...initialModals },
  scriptData: null,
  storyboards: [],
  videos: [],
  voices: [],
  subtitles: [],
  editData: null,
  qualityData: null,
  visualQualityData: null,
  statusInfo: "就绪",
  projectStyle: "写实电影感",
  globalLoading: false,
  globalLoadingText: "",
  nsfwEnabled: false,
  nsfwHasPin: false,
  characterPreviews: {},
  characterCards: [],
  activePanel: null,
  draftPremise: "",
  pipelineProjectId: "",
  tasks: [],
  pipelineStreams: {},
};

export const useDramaStore = create<DramaState>()(
  persist(
    (set) => ({
      ...initialState,

      setActivePanel: (panel) => set({ activePanel: panel }),

      setNsfwState: (enabled, hasPin) =>
        set({ nsfwEnabled: enabled, nsfwHasPin: hasPin }),

      setDraftPremise: (text) => set({ draftPremise: text }),

      setPipelineProjectId: (id) => set({ pipelineProjectId: id }),

      upsertTask: (task) =>
        set((state) => ({
          tasks: [...state.tasks.filter((t) => t.id !== task.id), task],
        })),

      patchTask: (id, patch) =>
        set((state) => ({
          tasks: state.tasks.map((t) => (t.id === id ? { ...t, ...patch } : t)),
        })),

      removeTask: (id) =>
        set((state) => ({ tasks: state.tasks.filter((t) => t.id !== id) })),

      clearFinishedTasks: () =>
        set((state) => ({ tasks: state.tasks.filter((t) => t.status === "running") })),

      setPipelineStream: (taskId, streamUrl) =>
        set((state) => ({
          pipelineStreams: streamUrl
            ? { ...state.pipelineStreams, [taskId]: streamUrl }
            : Object.fromEntries(
                Object.entries(state.pipelineStreams).filter(([k]) => k !== taskId)
              ),
        })),

      setModal: (key, open) =>
        set((state) => ({
          modals: { ...state.modals, [key]: open },
        })),

      setScriptData: (data) =>
        set({
          scriptData: data,
          storyboards: [],
          videos: [],
          voices: [],
          subtitles: [],
          editData: null,
          qualityData: null,
          visualQualityData: null,
          characterPreviews: {},
          characterCards: [],
        }),

      updateScriptField: (patch) =>
        set((state) => ({
          scriptData: state.scriptData ? { ...state.scriptData, ...patch } : null,
        })),

      updateCharacter: (charId, patch) =>
        set((state) => ({
          scriptData: state.scriptData
            ? {
                ...state.scriptData,
                characters: state.scriptData.characters.map((c) =>
                  c.character_id === charId ? { ...c, ...patch } : c
                ),
              }
            : null,
        })),

      updateScene: (sceneId, patch) =>
        set((state) => ({
          scriptData: state.scriptData
            ? {
                ...state.scriptData,
                scenes: state.scriptData.scenes.map((s) =>
                  s.scene_id === sceneId ? { ...s, ...patch } : s
                ),
              }
            : null,
        })),

      updateSubtitleSegment: (sceneId, index, text) =>
        set((state) => ({
          subtitles: state.subtitles.map((sub) => {
            if (sub.scene_id !== sceneId) return sub;
            const segments = sub.segments.map((seg, i) =>
              i === index ? { ...seg, text } : seg
            );
            const srt_content = segments
              .map(
                (seg, i) =>
                  `${i + 1}\n${formatSrtTime(seg.start)} --> ${formatSrtTime(seg.end)}\n${seg.text}\n`
              )
              .join("\n");
            return { ...sub, segments, srt_content };
          }),
        })),

      addStoryboard: (data) =>
        set((state) => ({
          storyboards: [...state.storyboards.filter((s) => s.scene_id !== data.scene_id), data],
        })),

      addVideo: (data) =>
        set((state) => ({
          videos: [...state.videos.filter((v) => v.scene_id !== data.scene_id), data],
        })),

      addVoice: (data) =>
        set((state) => ({
          voices: [...state.voices.filter((v) => v.scene_id !== data.scene_id), data],
        })),

      addSubtitle: (data) =>
        set((state) => ({
          subtitles: [...state.subtitles.filter((s) => s.scene_id !== data.scene_id), data],
        })),

      setEditData: (data) => set({ editData: data }),
      setQualityData: (data) => set({ qualityData: data }),
      setVisualQualityData: (data) => set({ visualQualityData: data }),

      setStatusInfo: (info) => set({ statusInfo: info }),

      setProjectStyle: (style) => set({ projectStyle: style }),

      startGlobalLoading: (text) =>
        set({ globalLoading: true, globalLoadingText: text }),
      stopGlobalLoading: () => set({ globalLoading: false, globalLoadingText: "" }),

      setCharacterPreview: (charId, data) =>
        set((state) => ({
          characterPreviews: data
            ? { ...state.characterPreviews, [charId]: data }
            : Object.fromEntries(
                Object.entries(state.characterPreviews).filter(([k]) => k !== charId)
              ),
        })),

      updateCharacterPreview: (charId, patch) =>
        set((state) => ({
          characterPreviews: {
            ...state.characterPreviews,
            [charId]: state.characterPreviews[charId]
              ? { ...state.characterPreviews[charId], ...patch }
              : ({ ...patch, character_id: charId } as CharacterPreviewData),
          },
        })),

      updateCharacterPreviewPrompt: (charId, view, value) =>
        set((state) => {
          const preview = state.characterPreviews[charId];
          if (!preview) return state;
          return {
            characterPreviews: {
              ...state.characterPreviews,
              [charId]: {
                ...preview,
                editedPrompts: { ...preview.editedPrompts, [view]: value },
              },
            },
          };
        }),

      addCharacterCard: (data) =>
        set((state) => ({
          characterCards: [
            ...state.characterCards.filter((c) => c.character_id !== data.character_id),
            data,
          ],
        })),

      reset: () => set(initialState),
    }),
    {
      name: "aicg-drama-store",
      partialize: (state) => ({
        scriptData: state.scriptData,
        storyboards: state.storyboards,
        videos: state.videos,
        voices: state.voices,
        subtitles: state.subtitles,
        editData: state.editData,
        qualityData: state.qualityData,
        visualQualityData: state.visualQualityData,
        characterPreviews: state.characterPreviews,
        characterCards: state.characterCards,
      }),
    }
  )
);

/** 将秒数格式化为 SRT 时间码 HH:MM:SS,mmm */
function formatSrtTime(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  const ms = Math.floor((seconds % 1) * 1000);
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")},${String(ms).padStart(3, "0")}`;
}
