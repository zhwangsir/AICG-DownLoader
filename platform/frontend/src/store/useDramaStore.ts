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
  LipSyncData,
  PostprocessData,
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
  lipSync: boolean;
  postprocess: boolean;
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
  /** 唇形同步结果（按场景去重，P4.4） */
  lipSyncs: LipSyncData[];
  /** 后处理编排结果（按场景去重，P4.4） */
  postprocesses: PostprocessData[];
  statusInfo: string;

  /** 全局生成状态：任一 Agent 生成中为 true，禁用所有生成按钮 */
  globalLoading: boolean;
  globalLoadingText: string;

  /** 角色生成预览数据（按角色 ID 索引） */
  characterPreviews: Record<string, CharacterPreviewData>;

  /** 角色生成结果（定妆照），持久化以保留生成完成状态 */
  characterCards: CharacterCardData[];

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
  addLipSync: (data: LipSyncData) => void;
  addPostprocess: (data: PostprocessData) => void;
  setStatusInfo: (info: string) => void;

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
  lipSync: false,
  postprocess: false,
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
  lipSyncs: [],
  postprocesses: [],
  statusInfo: "就绪",
  globalLoading: false,
  globalLoadingText: "",
  characterPreviews: {},
  characterCards: [],
};

export const useDramaStore = create<DramaState>()(
  persist(
    (set) => ({
      ...initialState,

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
          lipSyncs: [],
          postprocesses: [],
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

      addLipSync: (data) =>
        set((state) => ({
          lipSyncs: [...state.lipSyncs.filter((l) => l.scene_id !== data.scene_id), data],
        })),

      addPostprocess: (data) =>
        set((state) => ({
          postprocesses: [
            ...state.postprocesses.filter((p) => p.scene_id !== data.scene_id),
            data,
          ],
        })),

      setStatusInfo: (info) => set({ statusInfo: info }),

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
        lipSyncs: state.lipSyncs,
        postprocesses: state.postprocesses,
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
