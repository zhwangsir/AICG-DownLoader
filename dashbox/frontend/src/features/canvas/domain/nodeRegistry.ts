// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import {
  AUTO_REQUEST_ASPECT_RATIO,
  CANVAS_NODE_TYPES,
  DEFAULT_ASPECT_RATIO,
  type BeatContextNodeData,
  type AudioNodeData,
  type ImageSize,
  type CanvasNodeData,
  type CanvasNodeType,
  type ExportImageNodeData,
  type GroupNodeData,
  type ImageEditNodeData,
  type ImageGenNodeData,
  type NSFWImageGenNodeData,
  type NSFWDramaStudioNodeData,
  type NSFWScriptNodeData,
  type NSFWStoryboardNodeData,
  type NSFWVideoBatchNodeData,
  type NSFWVideoGenNodeData,
  type NSFWFactoryInitNodeData,
  type NSFWFactoryScriptNodeData,
  type NSFWFactoryAssetNodeData,
  type NSFWFactoryStoryboardNodeData,
  type NSFWFactoryShotNodeData,
  type NSFWFactoryAudioNodeData,
  type NSFWFactoryComposeNodeData,
  type NSFWFactoryQcNodeData,
  type Pano360ViewerNodeData,
  type ScriptNodeData,
  type SkillNodeData,
  type StoryboardSplitNodeData,
  type StoryboardGenNodeData,
  type TextAnnotationNodeData,
  type ThreeDWorldNodeData,
  type UploadImageNodeData,
  type VideoComposeNodeData,
  type VideoNodeData,
  type VideoStoryNodeData,
} from './canvasNodes';
import { DEFAULT_NODE_DISPLAY_NAME } from './nodeDisplay';
import { SKILL_SCHEMA_VERSION } from '@/features/freezone/context/skillRoles';
import { DEFAULT_IMAGE_MODEL_ID } from '../models';
import {
  DEFAULT_SHARED_MODEL_ID,
  DEFAULT_VIDEO_MODEL_ID,
} from '../ui/ProviderModelPicker';
import { readLastVideoModel } from './lastVideoModel';

export type MenuIconKey = 'upload' | 'sparkles' | 'layout' | 'text' | 'video' | 'audio' | 'script' | 'pano360' | 'threeDWorld' | 'videoCompose' | 'nsfw';

export interface CanvasNodeCapabilities {
  toolbar: boolean;
  promptInput: boolean;
}

export interface CanvasNodeConnectivity {
  sourceHandle: boolean;
  targetHandle: boolean;
  connectMenu: {
    fromSource: boolean;
    fromTarget: boolean;
  };
}

export interface CanvasNodeDefinition<TData extends CanvasNodeData = CanvasNodeData> {
  type: CanvasNodeType;
  menuLabelKey: string;
  menuIcon: MenuIconKey;
  visibleInMenu: boolean;
  capabilities: CanvasNodeCapabilities;
  connectivity: CanvasNodeConnectivity;
  createDefaultData: () => TData;
}

const uploadNodeDefinition: CanvasNodeDefinition<UploadImageNodeData> = {
  type: CANVAS_NODE_TYPES.upload,
  menuLabelKey: 'node.menu.uploadImage',
  menuIcon: 'upload',
  visibleInMenu: true,
  capabilities: {
    toolbar: true,
    promptInput: false,
  },
  connectivity: {
    sourceHandle: true,
    targetHandle: false,
    connectMenu: {
      fromSource: false,
      fromTarget: true,
    },
  },
  createDefaultData: () => ({
    displayName: DEFAULT_NODE_DISPLAY_NAME[CANVAS_NODE_TYPES.upload],
    imageUrl: null,
    previewImageUrl: null,
    aspectRatio: '1:1',
    isSizeManuallyAdjusted: false,
    sourceFileName: null,
  }),
};

const imageEditNodeDefinition: CanvasNodeDefinition<ImageEditNodeData> = {
  type: CANVAS_NODE_TYPES.imageEdit,
  menuLabelKey: 'node.menu.aiImageGeneration',
  menuIcon: 'sparkles',
  visibleInMenu: false,
  capabilities: {
    toolbar: true,
    promptInput: false,
  },
  connectivity: {
    sourceHandle: true,
    targetHandle: true,
    connectMenu: {
      fromSource: true,
      fromTarget: false,
    },
  },
  createDefaultData: () => ({
    displayName: DEFAULT_NODE_DISPLAY_NAME[CANVAS_NODE_TYPES.imageEdit],
    imageUrl: null,
    previewImageUrl: null,
    aspectRatio: DEFAULT_ASPECT_RATIO,
    isSizeManuallyAdjusted: false,
    requestAspectRatio: AUTO_REQUEST_ASPECT_RATIO,
    prompt: '',
    model: DEFAULT_IMAGE_MODEL_ID,
    size: '2K' as ImageSize,
    extraParams: {},
    generationMode: 'text_to_image',
    isGenerating: false,
    generationStartedAt: null,
    generationDurationMs: 60000,
  }),
};

const imageGenNodeDefinition: CanvasNodeDefinition<ImageGenNodeData> = {
  type: CANVAS_NODE_TYPES.imageGen,
  menuLabelKey: 'node.menu.image',
  menuIcon: 'sparkles',
  visibleInMenu: true,
  capabilities: {
    toolbar: false,
    promptInput: false,
  },
  connectivity: {
    sourceHandle: true,
    targetHandle: true,
    connectMenu: {
      fromSource: true,
      fromTarget: false,
    },
  },
  createDefaultData: () => ({
    displayName: DEFAULT_NODE_DISPLAY_NAME[CANVAS_NODE_TYPES.imageGen],
    imageUrl: null,
    previewImageUrl: null,
    aspectRatio: '16:9',
    isSizeManuallyAdjusted: false,
    requestAspectRatio: AUTO_REQUEST_ASPECT_RATIO,
    prompt: '',
    model: DEFAULT_SHARED_MODEL_ID,
    size: '2K' as ImageSize,
    count: 1,
    styleTemplateId: null,
    focusRegion: null,
    cameraSelection: null,
    referenceImageUrl: null,
    marks: [],
    isGenerating: false,
    generationStartedAt: null,
    generationDurationMs: 60000,
  }),
};

// R18 图片节点：与 imageGenNode 功能同构（提示词/参考图/连线/结果回填），
// 但提交走 model-library 本地 NSFW 管线。菜单入口仅在 R18 开启后出现
// （CanvasAddNodeGrid 按 useNsfwStatus 过滤），节点本体未开启时呈锁定态。
const nsfwImageGenNodeDefinition: CanvasNodeDefinition<NSFWImageGenNodeData> = {
  type: CANVAS_NODE_TYPES.nsfwImageGen,
  menuLabelKey: 'node.menu.nsfwImage',
  menuIcon: 'nsfw',
  visibleInMenu: true,
  capabilities: {
    toolbar: false,
    promptInput: false,
  },
  connectivity: {
    sourceHandle: true,
    targetHandle: true,
    connectMenu: {
      fromSource: true,
      fromTarget: false,
    },
  },
  createDefaultData: () => ({
    displayName: DEFAULT_NODE_DISPLAY_NAME[CANVAS_NODE_TYPES.nsfwImageGen],
    imageUrl: null,
    previewImageUrl: null,
    aspectRatio: '16:9',
    isSizeManuallyAdjusted: false,
    prompt: '',
    negativePrompt: '',
    checkpoint: '',
    size: '1216x832',
    referenceImageUrl: null,
    isGenerating: false,
    generationStartedAt: null,
    generationDurationMs: 60000,
  }),
};

// R18 视频节点：内置 4 个 NSFW 预设（Civitai 逆向 LoRA 链）直提 ComfyUI，
// mp4 落盘项目媒体。菜单入口仅 R18 开启后出现，与图片节点同口径。
const nsfwVideoGenNodeDefinition: CanvasNodeDefinition<NSFWVideoGenNodeData> = {
  type: CANVAS_NODE_TYPES.nsfwVideoGen,
  menuLabelKey: 'node.menu.nsfwVideo',
  menuIcon: 'nsfw',
  visibleInMenu: true,
  capabilities: {
    toolbar: false,
    promptInput: false,
  },
  connectivity: {
    sourceHandle: true,
    targetHandle: true,
    connectMenu: {
      fromSource: true,
      fromTarget: false,
    },
  },
  createDefaultData: () => ({
    displayName: DEFAULT_NODE_DISPLAY_NAME[CANVAS_NODE_TYPES.nsfwVideoGen],
    prompt: '',
    presetId: '',
    width: 832,
    height: 480,
    length: 81,
    videoUrl: null,
    firstFrameUrl: null,
    isGenerating: false,
    generationStartedAt: null,
    generationDurationMs: 300000,
  }),
};

// R18 剧本节点：梗概+角色卡 → LLM 结构化分镜 scenes JSON（同步端点，
// 本地 uncensored 模型）。菜单/锁定态与 R18 图片节点同口径；产物
// planResult 由下游「R18 分镜节点」经连线消费（非图像流，节点直读）。
const nsfwScriptNodeDefinition: CanvasNodeDefinition<NSFWScriptNodeData> = {
  type: CANVAS_NODE_TYPES.nsfwScript,
  menuLabelKey: 'node.menu.nsfwScript',
  menuIcon: 'nsfw',
  visibleInMenu: true,
  capabilities: {
    toolbar: false,
    promptInput: false,
  },
  connectivity: {
    sourceHandle: true,
    targetHandle: true,
    connectMenu: {
      fromSource: true,
      fromTarget: false,
    },
  },
  createDefaultData: () => ({
    displayName: DEFAULT_NODE_DISPLAY_NAME[CANVAS_NODE_TYPES.nsfwScript],
    synopsis: '',
    charactersText: '',
    styleHint: '',
    durationSec: 90,
    aspect: '9:16',
    planResult: null,
    isGenerating: false,
    generationStartedAt: null,
    generationDurationMs: 120000,
  }),
};

// R18 分镜节点：上游 R18 剧本 scenes × 定妆照（IPAdapter 锚定）→ 并发批量
// 首帧；每帧落图 spawn exportImage 子节点供 R18 视频节点单独连线。
const nsfwStoryboardNodeDefinition: CanvasNodeDefinition<NSFWStoryboardNodeData> = {
  type: CANVAS_NODE_TYPES.nsfwStoryboard,
  menuLabelKey: 'node.menu.nsfwStoryboard',
  menuIcon: 'nsfw',
  visibleInMenu: true,
  capabilities: {
    toolbar: false,
    promptInput: false,
  },
  connectivity: {
    sourceHandle: true,
    targetHandle: true,
    connectMenu: {
      fromSource: true,
      fromTarget: false,
    },
  },
  createDefaultData: () => ({
    displayName: DEFAULT_NODE_DISPLAY_NAME[CANVAS_NODE_TYPES.nsfwStoryboard],
    checkpoint: '',
    size: '832x1216',
    frames: [],
    anchorUploadUrl: null,
    isBatchRunning: false,
    batchError: null,
  }),
};

// R18 出片节点：消费上游「R18 分镜」已生成首帧的镜头——TTS 逐句配音 +
// 视频逐镜头顺序生成（action 走预设 / plot·portrait 走 h3-clean），
// 产物 spawn 视频/音频子节点连视频合成；附 SRT 字幕导出。
const nsfwVideoBatchNodeDefinition: CanvasNodeDefinition<NSFWVideoBatchNodeData> = {
  type: CANVAS_NODE_TYPES.nsfwVideoBatch,
  menuLabelKey: 'node.menu.nsfwVideoBatch',
  menuIcon: 'nsfw',
  visibleInMenu: true,
  capabilities: {
    toolbar: false,
    promptInput: false,
  },
  connectivity: {
    sourceHandle: true,
    targetHandle: true,
    connectMenu: {
      fromSource: true,
      fromTarget: false,
    },
  },
  createDefaultData: () => ({
    displayName: DEFAULT_NODE_DISPLAY_NAME[CANVAS_NODE_TYPES.nsfwVideoBatch],
    voice: 'human-zh-paimon',
    shots: [],
    isBatchRunning: false,
    batchError: null,
  }),
};

// R18 短剧工厂：单节点全流程（剧本→首帧→配音→出片→合成），五阶段状态机
// 持久化断点续拍。上游可选图片节点（定妆照锚定）。
const nsfwDramaStudioNodeDefinition: CanvasNodeDefinition<NSFWDramaStudioNodeData> = {
  type: CANVAS_NODE_TYPES.nsfwDramaStudio,
  menuLabelKey: 'node.menu.nsfwDramaStudio',
  menuIcon: 'nsfw',
  visibleInMenu: true,
  capabilities: {
    toolbar: false,
    promptInput: false,
  },
  connectivity: {
    sourceHandle: true,
    targetHandle: true,
    connectMenu: {
      fromSource: true,
      fromTarget: false,
    },
  },
  createDefaultData: () => ({
    displayName: DEFAULT_NODE_DISPLAY_NAME[CANVAS_NODE_TYPES.nsfwDramaStudio],
    synopsis: '',
    charactersText: '',
    styleHint: '',
    durationSec: 90,
    aspect: '9:16',
    checkpoint: '',
    size: '832x1216',
    anchorUploadUrl: null,
    voice: 'human-zh-paimon',
    autoConfirm: false,
    planTitle: '',
    scenes: [],
    frameUrls: {},
    shotOutputs: {},
    composeUrl: null,
    composeDurationSec: null,
    pipeline: 'idle',
  }),
};

// ---- R18 制作工厂流水线（8 工序节点，2026-08-19）----
// 共用 connectivity 形状：双 handle + 右侧 source 菜单。
const FACTORY_CONNECTIVITY = {
  sourceHandle: true,
  targetHandle: true,
  connectMenu: { fromSource: true, fromTarget: false },
} as const;
const FACTORY_CAPABILITIES = { toolbar: false, promptInput: false } as const;

const nsfwFactoryInitNodeDefinition: CanvasNodeDefinition<NSFWFactoryInitNodeData> = {
  type: CANVAS_NODE_TYPES.nsfwFactoryInit,
  menuLabelKey: 'node.menu.nsfwFactoryInit',
  menuIcon: 'nsfw',
  visibleInMenu: true,
  capabilities: FACTORY_CAPABILITIES,
  connectivity: { ...FACTORY_CONNECTIVITY, connectMenu: { fromSource: true, fromTarget: false } },
  createDefaultData: () => ({
    displayName: DEFAULT_NODE_DISPLAY_NAME[CANVAS_NODE_TYPES.nsfwFactoryInit],
    theme: '都市情感',
    themeNote: '',
    checkpoint: '',
    size: '832x1216',
    durationSec: 90,
    aspect: '9:16',
  }),
};

const nsfwFactoryScriptNodeDefinition: CanvasNodeDefinition<NSFWFactoryScriptNodeData> = {
  type: CANVAS_NODE_TYPES.nsfwFactoryScript,
  menuLabelKey: 'node.menu.nsfwFactoryScript',
  menuIcon: 'nsfw',
  visibleInMenu: true,
  capabilities: FACTORY_CAPABILITIES,
  connectivity: FACTORY_CONNECTIVITY,
  createDefaultData: () => ({
    displayName: DEFAULT_NODE_DISPLAY_NAME[CANVAS_NODE_TYPES.nsfwFactoryScript],
    synopsis: '',
    charactersText: '',
    episodeCount: 1,
    planTitle: '',
    episodes: [],
    isGenerating: false,
    generationStartedAt: null,
    generationError: null,
  }),
};

const nsfwFactoryAssetNodeDefinition: CanvasNodeDefinition<NSFWFactoryAssetNodeData> = {
  type: CANVAS_NODE_TYPES.nsfwFactoryAsset,
  menuLabelKey: 'node.menu.nsfwFactoryAsset',
  menuIcon: 'nsfw',
  visibleInMenu: true,
  capabilities: FACTORY_CAPABILITIES,
  connectivity: FACTORY_CONNECTIVITY,
  createDefaultData: () => ({
    displayName: DEFAULT_NODE_DISPLAY_NAME[CANVAS_NODE_TYPES.nsfwFactoryAsset],
    items: [],
    styleAnchorUrl: null,
    isGenerating: false,
    generationError: null,
  }),
};

const nsfwFactoryStoryboardNodeDefinition: CanvasNodeDefinition<NSFWFactoryStoryboardNodeData> = {
  type: CANVAS_NODE_TYPES.nsfwFactoryStoryboard,
  menuLabelKey: 'node.menu.nsfwFactoryStoryboard',
  menuIcon: 'nsfw',
  visibleInMenu: true,
  capabilities: FACTORY_CAPABILITIES,
  connectivity: FACTORY_CONNECTIVITY,
  createDefaultData: () => ({
    displayName: DEFAULT_NODE_DISPLAY_NAME[CANVAS_NODE_TYPES.nsfwFactoryStoryboard],
    rows: [],
    confirmed: false,
  }),
};

const nsfwFactoryShotNodeDefinition: CanvasNodeDefinition<NSFWFactoryShotNodeData> = {
  type: CANVAS_NODE_TYPES.nsfwFactoryShot,
  menuLabelKey: 'node.menu.nsfwFactoryShot',
  menuIcon: 'nsfw',
  visibleInMenu: true,
  capabilities: FACTORY_CAPABILITIES,
  connectivity: FACTORY_CONNECTIVITY,
  createDefaultData: () => ({
    displayName: DEFAULT_NODE_DISPLAY_NAME[CANVAS_NODE_TYPES.nsfwFactoryShot],
    outputs: {},
    isRunning: false,
    error: null,
    interrupted: false,
  }),
};

const nsfwFactoryAudioNodeDefinition: CanvasNodeDefinition<NSFWFactoryAudioNodeData> = {
  type: CANVAS_NODE_TYPES.nsfwFactoryAudio,
  menuLabelKey: 'node.menu.nsfwFactoryAudio',
  menuIcon: 'nsfw',
  visibleInMenu: true,
  capabilities: FACTORY_CAPABILITIES,
  connectivity: FACTORY_CONNECTIVITY,
  createDefaultData: () => ({
    displayName: DEFAULT_NODE_DISPLAY_NAME[CANVAS_NODE_TYPES.nsfwFactoryAudio],
    voice: 'human-zh-paimon',
    ttsUrls: {},
    bgmUrl: '',
    bgmVolume: 0.35,
    envSfxUrl: '',
    envSfxVolume: 0.25,
    isRunning: false,
    error: null,
  }),
};

const nsfwFactoryComposeNodeDefinition: CanvasNodeDefinition<NSFWFactoryComposeNodeData> = {
  type: CANVAS_NODE_TYPES.nsfwFactoryCompose,
  menuLabelKey: 'node.menu.nsfwFactoryCompose',
  menuIcon: 'nsfw',
  visibleInMenu: true,
  capabilities: FACTORY_CAPABILITIES,
  connectivity: FACTORY_CONNECTIVITY,
  createDefaultData: () => ({
    displayName: DEFAULT_NODE_DISPLAY_NAME[CANVAS_NODE_TYPES.nsfwFactoryCompose],
    colorProfile: 'none',
    transition: 'fade',
    openingText: '',
    closingText: '',
    burnSubtitle: true,
    composeUrl: null,
    composeDurationSec: null,
    isRunning: false,
    error: null,
  }),
};

const nsfwFactoryQcNodeDefinition: CanvasNodeDefinition<NSFWFactoryQcNodeData> = {
  type: CANVAS_NODE_TYPES.nsfwFactoryQc,
  menuLabelKey: 'node.menu.nsfwFactoryQc',
  menuIcon: 'nsfw',
  visibleInMenu: true,
  capabilities: FACTORY_CAPABILITIES,
  connectivity: { ...FACTORY_CONNECTIVITY, connectMenu: { fromSource: false, fromTarget: false } },
  createDefaultData: () => ({
    displayName: DEFAULT_NODE_DISPLAY_NAME[CANVAS_NODE_TYPES.nsfwFactoryQc],
    reportUrl: null,
    report: null,
    isRunning: false,
    error: null,
  }),
};

const exportImageNodeDefinition: CanvasNodeDefinition<ExportImageNodeData> = {
  type: CANVAS_NODE_TYPES.exportImage,
  menuLabelKey: 'node.menu.uploadImage',
  menuIcon: 'upload',
  visibleInMenu: false,
  capabilities: {
    toolbar: true,
    promptInput: false,
  },
  connectivity: {
    sourceHandle: true,
    targetHandle: true,
    connectMenu: {
      fromSource: false,
      fromTarget: false,
    },
  },
  createDefaultData: () => ({
    displayName: DEFAULT_NODE_DISPLAY_NAME[CANVAS_NODE_TYPES.exportImage],
    imageUrl: null,
    previewImageUrl: null,
    aspectRatio: DEFAULT_ASPECT_RATIO,
    isSizeManuallyAdjusted: false,
    resultKind: 'generic',
  }),
};

const beatContextNodeDefinition: CanvasNodeDefinition<BeatContextNodeData> = {
  type: CANVAS_NODE_TYPES.beatContext,
  menuLabelKey: 'node.menu.beatContext',
  menuIcon: 'text',
  visibleInMenu: true,
  capabilities: {
    toolbar: true,
    promptInput: false,
  },
  connectivity: {
    sourceHandle: true,
    targetHandle: false,
    connectMenu: {
      fromSource: true,
      fromTarget: false,
    },
  },
  createDefaultData: () => ({
    displayName: DEFAULT_NODE_DISPLAY_NAME[CANVAS_NODE_TYPES.beatContext],
    content: '',
    context_scope: 'standalone',
    beat_context: {
      schema: 'beat_context.v1',
      source: 'standalone',
      title: '自定义镜头上下文',
      visual_description: '',
      narration_segment: '',
      scene_id: '',
      time_of_day: '',
      detected_identities: [],
      detected_props: [],
      sketch_colors: {},
      prop_marker_colors: {},
    },
    snapshot: {
      visualDescription: '',
      narrationSegment: '',
      sceneId: '',
      timeOfDay: '',
      detectedIdentities: [],
      detectedProps: [],
      sketchColors: {},
      propMarkerColors: {},
    },
    syncStatus: 'fresh',
  }),
};

const groupNodeDefinition: CanvasNodeDefinition<GroupNodeData> = {
  type: CANVAS_NODE_TYPES.group,
  menuLabelKey: 'node.menu.storyboard',
  menuIcon: 'layout',
  visibleInMenu: false,
  capabilities: {
    toolbar: false,
    promptInput: false,
  },
  connectivity: {
    sourceHandle: false,
    targetHandle: false,
    connectMenu: {
      fromSource: false,
      fromTarget: false,
    },
  },
  createDefaultData: () => ({
    displayName: DEFAULT_NODE_DISPLAY_NAME[CANVAS_NODE_TYPES.group],
    label: '组',
  }),
};

const textAnnotationNodeDefinition: CanvasNodeDefinition<TextAnnotationNodeData> = {
  type: CANVAS_NODE_TYPES.textAnnotation,
  menuLabelKey: 'node.menu.textAnnotation',
  menuIcon: 'text',
  visibleInMenu: true,
  capabilities: {
    toolbar: true,
    promptInput: false,
  },
  connectivity: {
    sourceHandle: true,
    targetHandle: true,
    connectMenu: {
      fromSource: true,
      fromTarget: false,
    },
  },
  createDefaultData: () => ({
    displayName: DEFAULT_NODE_DISPLAY_NAME[CANVAS_NODE_TYPES.textAnnotation],
    content: '',
    model: DEFAULT_SHARED_MODEL_ID,
    extraParams: {},
    isGenerating: false,
  }),
};

const storyboardSplitDefinition: CanvasNodeDefinition<StoryboardSplitNodeData> = {
  type: CANVAS_NODE_TYPES.storyboardSplit,
  menuLabelKey: 'node.menu.storyboard',
  menuIcon: 'layout',
  visibleInMenu: false,
  capabilities: {
    toolbar: false,
    promptInput: false,
  },
  connectivity: {
    sourceHandle: true,
    targetHandle: true,
    connectMenu: {
      fromSource: false,
      fromTarget: false,
    },
  },
  createDefaultData: () => ({
    displayName: DEFAULT_NODE_DISPLAY_NAME[CANVAS_NODE_TYPES.storyboardSplit],
    aspectRatio: DEFAULT_ASPECT_RATIO,
    frameAspectRatio: DEFAULT_ASPECT_RATIO,
    gridRows: 2,
    gridCols: 2,
    frames: [],
    exportOptions: {
      showFrameIndex: false,
      showFrameNote: false,
      notePlacement: 'overlay',
      imageFit: 'cover',
      frameIndexPrefix: 'S',
      cellGap: 8,
      outerPadding: 0,
      fontSize: 4,
      backgroundColor: '#0f1115',
      textColor: '#f8fafc',
    },
  }),
};

const storyboardGenNodeDefinition: CanvasNodeDefinition<StoryboardGenNodeData> = {
  type: CANVAS_NODE_TYPES.storyboardGen,
  menuLabelKey: 'node.menu.storyboardGen',
  menuIcon: 'sparkles',
  visibleInMenu: true,
  capabilities: {
    toolbar: true,
    promptInput: false,
  },
  connectivity: {
    sourceHandle: true,
    targetHandle: true,
    connectMenu: {
      fromSource: true,
      fromTarget: false,
    },
  },
  createDefaultData: () => ({
    displayName: DEFAULT_NODE_DISPLAY_NAME[CANVAS_NODE_TYPES.storyboardGen],
    gridRows: 2,
    gridCols: 2,
    frames: [],
    ratioControlMode: 'cell',
    model: DEFAULT_IMAGE_MODEL_ID,
    size: '2K' as ImageSize,
    requestAspectRatio: AUTO_REQUEST_ASPECT_RATIO,
    extraParams: {},
    imageUrl: null,
    previewImageUrl: null,
    aspectRatio: DEFAULT_ASPECT_RATIO,
    isGenerating: false,
    generationStartedAt: null,
    generationDurationMs: 60000,
  }),
};

const videoNodeDefinition: CanvasNodeDefinition<VideoNodeData> = {
  type: CANVAS_NODE_TYPES.video,
  menuLabelKey: 'node.menu.video',
  menuIcon: 'video',
  visibleInMenu: true,
  capabilities: {
    toolbar: true,
    promptInput: false,
  },
  connectivity: {
    sourceHandle: true,
    targetHandle: true,
    connectMenu: {
      fromSource: true,
      fromTarget: false,
    },
  },
  createDefaultData: () => ({
    displayName: DEFAULT_NODE_DISPLAY_NAME[CANVAS_NODE_TYPES.video],
    videoUrl: null,
    previewImageUrl: null,
    aspectRatio: '16:9',
    isSizeManuallyAdjusted: false,
    sourceFileName: null,
    widthPx: null,
    heightPx: null,
    durationMs: null,
    isUploading: false,
    isAnalyzing: false,
    analysisResult: null,
    analysisError: null,
    // generation panel defaults
    prompt: '',
    genMode: 'textToVideo',
    // 继承用户上次为视频节点选的模型；无记录时回落到默认模型。
    model: readLastVideoModel() ?? DEFAULT_VIDEO_MODEL_ID,
    quality: '720P',
    durationSec: 5,
    count: 1,
    isGenerating: false,
    generationStartedAt: null,
    generationDurationMs: 60000,
  }),
};

const audioNodeDefinition: CanvasNodeDefinition<AudioNodeData> = {
  type: CANVAS_NODE_TYPES.audio,
  menuLabelKey: 'node.menu.audio',
  menuIcon: 'audio',
  visibleInMenu: true,
  capabilities: {
    toolbar: true,
    promptInput: false,
  },
  connectivity: {
    sourceHandle: true,
    targetHandle: true,
    connectMenu: {
      fromSource: true,
      fromTarget: true,
    },
  },
  createDefaultData: () => ({
    displayName: DEFAULT_NODE_DISPLAY_NAME[CANVAS_NODE_TYPES.audio],
    audioUrl: null,
    sourceFileName: null,
    durationMs: null,
    isUploading: false,
    text: '',
    emotionPrompt: '',
    // 默认音色：留空。AudioOperationsPanel 挂载时会拉一次音色库
    // references 并落到第一个；这里硬编码 project_narrator 会让初始化
    // effect 提前 bail，导致用户始终看到"项目解说人"无法替换。
    voiceLanguage: '',
    isGenerating: false,
    generationStartedAt: null,
  }),
};

const videoStoryNodeDefinition: CanvasNodeDefinition<VideoStoryNodeData> = {
  type: CANVAS_NODE_TYPES.videoStory,
  menuLabelKey: 'node.menu.videoStory',
  menuIcon: 'video',
  // Only ever spawned programmatically (after video-analyze success), never
  // through the double-click or "+" menus.
  visibleInMenu: false,
  capabilities: {
    toolbar: false,
    promptInput: false,
  },
  connectivity: {
    sourceHandle: false,
    targetHandle: true,
    connectMenu: {
      fromSource: false,
      fromTarget: false,
    },
  },
  createDefaultData: () => ({
    displayName: DEFAULT_NODE_DISPLAY_NAME[CANVAS_NODE_TYPES.videoStory],
    sourceVideoUrl: null,
    rows: [],
    rawResult: null,
    isAnalyzing: false,
    analysisError: null,
  }),
};

const videoComposeNodeDefinition: CanvasNodeDefinition<VideoComposeNodeData> = {
  type: CANVAS_NODE_TYPES.videoCompose,
  menuLabelKey: 'node.menu.videoCompose',
  menuIcon: 'videoCompose',
  visibleInMenu: true,
  capabilities: {
    toolbar: true,
    promptInput: false,
  },
  connectivity: {
    // 接收 ≥2 个上游视频（可选音频）节点；合成结果可继续向下游输出。
    sourceHandle: true,
    targetHandle: true,
    connectMenu: {
      fromSource: true,
      fromTarget: false,
    },
  },
  createDefaultData: () => ({
    displayName: DEFAULT_NODE_DISPLAY_NAME[CANVAS_NODE_TYPES.videoCompose],
    resultVideoUrl: null,
    previewImageUrl: null,
    resolution: '1080p',
  }),
};

// 写死的脚本生成模型 id（脚本生成接口暂未提供 list）。和 ScriptNode 内的
// SCRIPT_MODELS 保持同步，仅供 createDefaultData 选默认。
const DEFAULT_SCRIPT_MODEL_ID = 'gvlm-3.1';

const scriptNodeDefinition: CanvasNodeDefinition<ScriptNodeData> = {
  type: CANVAS_NODE_TYPES.script,
  menuLabelKey: 'node.menu.script',
  menuIcon: 'script',
  visibleInMenu: true,
  capabilities: {
    toolbar: false,
    promptInput: false,
  },
  connectivity: {
    sourceHandle: true,
    targetHandle: true,
    connectMenu: {
      fromSource: true,
      fromTarget: true,
    },
  },
  createDefaultData: () => ({
    displayName: DEFAULT_NODE_DISPLAY_NAME[CANVAS_NODE_TYPES.script],
    prompt: '',
    model: DEFAULT_SCRIPT_MODEL_ID,
    lastAction: null,
    scriptResult: null,
    isGenerating: false,
    generationStartedAt: null,
    generationDurationMs: 60000,
  }),
};

const pano360ViewerNodeDefinition: CanvasNodeDefinition<Pano360ViewerNodeData> = {
  type: CANVAS_NODE_TYPES.pano360Viewer,
  menuLabelKey: 'node.menu.pano360Viewer',
  menuIcon: 'pano360',
  visibleInMenu: true,
  capabilities: {
    toolbar: false,
    promptInput: false,
  },
  connectivity: {
    // 全景查看器既接收上游贴图，也能向下游输出截图节点（截当前 / 2×2 / 4×3），
    // 所以两端都要有 handle——否则截图连线在画布重新水合时会被过滤掉。
    sourceHandle: true,
    targetHandle: true,
    connectMenu: {
      fromSource: true,
      fromTarget: false,
    },
  },
  createDefaultData: () => ({
    displayName: DEFAULT_NODE_DISPLAY_NAME[CANVAS_NODE_TYPES.pano360Viewer],
    imageUrl: null,
    previewImageUrl: null,
    sourceNodeId: null,
    sphereCorrectionDeg: { roll: 0, pitch: 0, yaw: 0 },
    frontYawDeg: 0,
    fovDeg: 70,
    lastExportedEntry: null,
  }),
};

const threeDWorldNodeDefinition: CanvasNodeDefinition<ThreeDWorldNodeData> = {
  type: CANVAS_NODE_TYPES.threeDWorld,
  menuLabelKey: 'node.menu.threeDWorld',
  menuIcon: 'threeDWorld',
  visibleInMenu: true,
  capabilities: {
    toolbar: false,
    promptInput: false,
  },
  connectivity: {
    sourceHandle: true,
    targetHandle: true,
    connectMenu: {
      fromSource: true,
      fromTarget: true,
    },
  },
  createDefaultData: () => ({
    displayName: DEFAULT_NODE_DISPLAY_NAME[CANVAS_NODE_TYPES.threeDWorld],
    prompt: '',
    model: 'marble-1.1',
    taskKey: null,
    plyUrl: null,
    sourceNodeId: null,
    sourceKind: null,
    isGenerating: false,
    generationStartedAt: null,
    generationDurationMs: 90000,
    errorMessage: null,
  }),
};

const skillNodeDefinition: CanvasNodeDefinition<SkillNodeData> = {
  type: CANVAS_NODE_TYPES.skill,
  menuLabelKey: 'node.menu.skill',
  menuIcon: 'sparkles',
  visibleInMenu: false,
  capabilities: {
    toolbar: false,
    promptInput: false,
  },
  connectivity: {
    sourceHandle: true,
    targetHandle: true,
    connectMenu: {
      fromSource: true,
      fromTarget: true,
    },
  },
  createDefaultData: () => ({
    displayName: DEFAULT_NODE_DISPLAY_NAME[CANVAS_NODE_TYPES.skill],
    skill_id: '',
    skill_schema_version: SKILL_SCHEMA_VERSION,
  }),
};

export const canvasNodeDefinitions: Record<CanvasNodeType, CanvasNodeDefinition> = {
  [CANVAS_NODE_TYPES.upload]: uploadNodeDefinition,
  [CANVAS_NODE_TYPES.imageEdit]: imageEditNodeDefinition,
  [CANVAS_NODE_TYPES.imageGen]: imageGenNodeDefinition,
  [CANVAS_NODE_TYPES.exportImage]: exportImageNodeDefinition,
  [CANVAS_NODE_TYPES.beatContext]: beatContextNodeDefinition,
  [CANVAS_NODE_TYPES.textAnnotation]: textAnnotationNodeDefinition,
  [CANVAS_NODE_TYPES.group]: groupNodeDefinition,
  [CANVAS_NODE_TYPES.storyboardSplit]: storyboardSplitDefinition,
  [CANVAS_NODE_TYPES.storyboardGen]: storyboardGenNodeDefinition,
  [CANVAS_NODE_TYPES.video]: videoNodeDefinition,
  [CANVAS_NODE_TYPES.audio]: audioNodeDefinition,
  [CANVAS_NODE_TYPES.videoStory]: videoStoryNodeDefinition,
  [CANVAS_NODE_TYPES.videoCompose]: videoComposeNodeDefinition,
  [CANVAS_NODE_TYPES.script]: scriptNodeDefinition,
  [CANVAS_NODE_TYPES.pano360Viewer]: pano360ViewerNodeDefinition,
  [CANVAS_NODE_TYPES.threeDWorld]: threeDWorldNodeDefinition,
  [CANVAS_NODE_TYPES.skill]: skillNodeDefinition,
  [CANVAS_NODE_TYPES.nsfwImageGen]: nsfwImageGenNodeDefinition,
  [CANVAS_NODE_TYPES.nsfwVideoGen]: nsfwVideoGenNodeDefinition,
  [CANVAS_NODE_TYPES.nsfwScript]: nsfwScriptNodeDefinition,
  [CANVAS_NODE_TYPES.nsfwStoryboard]: nsfwStoryboardNodeDefinition,
  [CANVAS_NODE_TYPES.nsfwVideoBatch]: nsfwVideoBatchNodeDefinition,
  [CANVAS_NODE_TYPES.nsfwDramaStudio]: nsfwDramaStudioNodeDefinition,
  [CANVAS_NODE_TYPES.nsfwFactoryInit]: nsfwFactoryInitNodeDefinition,
  [CANVAS_NODE_TYPES.nsfwFactoryScript]: nsfwFactoryScriptNodeDefinition,
  [CANVAS_NODE_TYPES.nsfwFactoryAsset]: nsfwFactoryAssetNodeDefinition,
  [CANVAS_NODE_TYPES.nsfwFactoryStoryboard]: nsfwFactoryStoryboardNodeDefinition,
  [CANVAS_NODE_TYPES.nsfwFactoryShot]: nsfwFactoryShotNodeDefinition,
  [CANVAS_NODE_TYPES.nsfwFactoryAudio]: nsfwFactoryAudioNodeDefinition,
  [CANVAS_NODE_TYPES.nsfwFactoryCompose]: nsfwFactoryComposeNodeDefinition,
  [CANVAS_NODE_TYPES.nsfwFactoryQc]: nsfwFactoryQcNodeDefinition,
};

export function getNodeDefinition(type: CanvasNodeType): CanvasNodeDefinition {
  return canvasNodeDefinitions[type];
}

export function getMenuNodeDefinitions(): CanvasNodeDefinition[] {
  return Object.values(canvasNodeDefinitions).filter((definition) => definition.visibleInMenu);
}

export function nodeHasSourceHandle(type: CanvasNodeType): boolean {
  return canvasNodeDefinitions[type].connectivity.sourceHandle;
}

export function nodeHasTargetHandle(type: CanvasNodeType): boolean {
  return canvasNodeDefinitions[type].connectivity.targetHandle;
}

// 「目标节点类型」→ 允许的上游（源）节点类型白名单。这张表和下面的
// DOWNSTREAM_TARGET_WHITELIST 一起，是建边规则的单一事实来源：UI 层（连线菜单 /
// 手动拖线 / isValidConnection）与 store 建边收口（onConnect / addEdge /
// addEdgeWithData / 加载规范化）都经由 isUpstreamConnectionAllowed 查它们，避免
// 任何一条建边路径绕过规则。不在表中的目标类型表示「不额外限制类型」，仅受
// handle 级默认规则约束。
const UPSTREAM_SOURCE_WHITELIST: Partial<Record<CanvasNodeType, readonly CanvasNodeType[]>> = {
  // 音频节点的上游只能是文本节点（生成来源），外加视频节点 —— 后者是「人声/背景音
  // 分离」动作留下的溯源边：那个动作从一个视频节点同时产出「背景音」音频节点和
  // 「无声」视频节点，两条边一起画回源视频。少了 video 这一项，音频那条边会被建边
  // 收口静默丢掉，画布上一边连着无声视频、一边孤零零挂着背景音。
  [CANVAS_NODE_TYPES.audio]: [
    CANVAS_NODE_TYPES.textAnnotation,
    CANVAS_NODE_TYPES.video,
    // R18 出片 spawn 配音子节点时的溯源边（batch→audio），少了会被建边
    // 收口静默丢掉（2026-08-18 E2E 实测复现）
    CANVAS_NODE_TYPES.nsfwVideoBatch,
  ],
  // ---- R18 制作工厂流水线（链式相邻工序 + 图片源可选锚定）----
  // 工序序（2026-08-19 调整：分镜表提前，数字资产在分镜确认后按具体镜头生成）：
  // ①立项 → ②剧本 → ③分镜表 → ④数字资产 → ⑤镜头 → ⑥音频 → ⑦后期 → ⑧质检
  [CANVAS_NODE_TYPES.nsfwFactoryInit]: [],
  [CANVAS_NODE_TYPES.nsfwFactoryScript]: [CANVAS_NODE_TYPES.nsfwFactoryInit],
  [CANVAS_NODE_TYPES.nsfwFactoryStoryboard]: [CANVAS_NODE_TYPES.nsfwFactoryScript],
  [CANVAS_NODE_TYPES.nsfwFactoryAsset]: [
    CANVAS_NODE_TYPES.nsfwFactoryStoryboard,
    CANVAS_NODE_TYPES.upload,
    CANVAS_NODE_TYPES.imageGen,
    CANVAS_NODE_TYPES.nsfwImageGen,
    CANVAS_NODE_TYPES.exportImage,
  ],
  // 资产为可选工序：允许分镜表直连镜头（跳过资产生成）
  [CANVAS_NODE_TYPES.nsfwFactoryShot]: [
    CANVAS_NODE_TYPES.nsfwFactoryAsset,
    CANVAS_NODE_TYPES.nsfwFactoryStoryboard,
  ],
  [CANVAS_NODE_TYPES.nsfwFactoryAudio]: [CANVAS_NODE_TYPES.nsfwFactoryShot],
  [CANVAS_NODE_TYPES.nsfwFactoryCompose]: [
    CANVAS_NODE_TYPES.nsfwFactoryAudio,
    // 无配音/BGM 时允许镜头直连合成
    CANVAS_NODE_TYPES.nsfwFactoryShot,
  ],
  [CANVAS_NODE_TYPES.nsfwFactoryQc]: [CANVAS_NODE_TYPES.nsfwFactoryCompose],
};

// 「源节点类型」→ 允许的下游（目标）节点类型白名单。与上面那张表对称：那张按
// 目标收口（谁能连进我），这张按源收口（我能连去谁）。两张表一起构成建边规则，
// 同样被所有建边路径查询。不在表中的源类型表示「不额外限制下游类型」。
//
// 音频是目前唯一需要按源收口的类型：只有视频节点（声轨素材）和视频合成（音频轨）
// 会读上游音频，连到图片 / 脚本 / 3D 等节点上不产生任何效果，只是画布上一根骗人
// 的线。这条规则本来就写在别处了 —— getDownstreamSpawnTypes(audio) 只给
// [video, videoCompose]，Canvas.resolveAllowedNodeTypes 里图片节点的上游菜单也
// 明写「不收音频」—— 但那两处只管菜单能创建什么，拖线落到一个**既有**节点上时
// 谁都没拦。放进这里才真正收口。
const DOWNSTREAM_TARGET_WHITELIST: Partial<Record<CanvasNodeType, readonly CanvasNodeType[]>> = {
  [CANVAS_NODE_TYPES.audio]: [CANVAS_NODE_TYPES.video, CANVAS_NODE_TYPES.videoCompose],
  // ---- R18 制作工厂流水线（镜像上游表）----
  [CANVAS_NODE_TYPES.nsfwFactoryInit]: [CANVAS_NODE_TYPES.nsfwFactoryScript],
  [CANVAS_NODE_TYPES.nsfwFactoryScript]: [CANVAS_NODE_TYPES.nsfwFactoryStoryboard],
  [CANVAS_NODE_TYPES.nsfwFactoryStoryboard]: [
    CANVAS_NODE_TYPES.nsfwFactoryAsset,
    CANVAS_NODE_TYPES.nsfwFactoryShot,
  ],
  [CANVAS_NODE_TYPES.nsfwFactoryAsset]: [CANVAS_NODE_TYPES.nsfwFactoryShot],
  [CANVAS_NODE_TYPES.nsfwFactoryShot]: [
    CANVAS_NODE_TYPES.nsfwFactoryAudio,
    CANVAS_NODE_TYPES.nsfwFactoryCompose,
  ],
  [CANVAS_NODE_TYPES.nsfwFactoryAudio]: [CANVAS_NODE_TYPES.nsfwFactoryCompose],
  [CANVAS_NODE_TYPES.nsfwFactoryCompose]: [CANVAS_NODE_TYPES.nsfwFactoryQc],
  [CANVAS_NODE_TYPES.nsfwFactoryQc]: [],
};

// 返回某目标类型允许的上游源类型；返回 null 表示该类型不施加额外类型限制。
export function getAllowedUpstreamSourceTypes(
  targetType: CanvasNodeType,
): readonly CanvasNodeType[] | null {
  return UPSTREAM_SOURCE_WHITELIST[targetType] ?? null;
}

// 返回某源类型允许的下游目标类型；返回 null 表示该类型不施加额外类型限制。
export function getAllowedDownstreamTargetTypes(
  sourceType: CanvasNodeType,
): readonly CanvasNodeType[] | null {
  return DOWNSTREAM_TARGET_WHITELIST[sourceType] ?? null;
}

// 判断从 sourceType 连向 targetType 的连接是否合法：两张白名单都要过。
export function isUpstreamConnectionAllowed(
  sourceType: CanvasNodeType,
  targetType: CanvasNodeType,
): boolean {
  const allowedSources = UPSTREAM_SOURCE_WHITELIST[targetType];
  if (allowedSources && !allowedSources.includes(sourceType)) {
    return false;
  }
  const allowedTargets = DOWNSTREAM_TARGET_WHITELIST[sourceType];
  if (allowedTargets && !allowedTargets.includes(targetType)) {
    return false;
  }
  return true;
}

// 只由系统动作建立、**不开放给用户手工创建**的边。上面两张表必须放行它们（否则
// 加载规范化会把存量边丢掉），但连线菜单和手动拖线不该提供 —— 用户自己画一根出来
// 没有任何消费方，又是一根骗人的线。
const SYSTEM_ONLY_CONNECTIONS: readonly (readonly [CanvasNodeType, CanvasNodeType])[] = [
  // 「人声/背景音分离」把产出的「背景音」音频节点画回源视频，是条溯源边。音频节点
  // 自己只读文本上游（AudioOperationsPanel 只取 text），手工连一根视频进来什么都
  // 不会发生。
  [CANVAS_NODE_TYPES.video, CANVAS_NODE_TYPES.audio],
];

// 判断用户能不能**手工**建立这条边：建边规则放行，且不是系统专用边。菜单候选与
// 手动拖线查这条；store 的建边收口查的仍是 isUpstreamConnectionAllowed。
export function isManualConnectionAllowed(
  sourceType: CanvasNodeType,
  targetType: CanvasNodeType,
): boolean {
  if (!isUpstreamConnectionAllowed(sourceType, targetType)) {
    return false;
  }
  return !SYSTEM_ONLY_CONNECTIONS.some(
    ([source, target]) => source === sourceType && target === targetType,
  );
}

export function getConnectMenuNodeTypes(handleType: 'source' | 'target'): CanvasNodeType[] {
  const fromSource = handleType === 'source';
  return Object.values(canvasNodeDefinitions)
    .filter((definition) => (fromSource
      ? definition.connectivity.connectMenu.fromSource
      : definition.connectivity.connectMenu.fromTarget))
    .filter((definition) => (fromSource
      ? definition.connectivity.targetHandle
      : definition.connectivity.sourceHandle))
    .map((definition) => definition.type);
}

// 图片类节点（upload / imageEdit / imageGen / exportImage）共用的下游候选。
const IMAGE_DOWNSTREAM_SPAWN_TYPES: readonly CanvasNodeType[] = [
  CANVAS_NODE_TYPES.textAnnotation,
  CANVAS_NODE_TYPES.imageGen,
  CANVAS_NODE_TYPES.video,
  CANVAS_NODE_TYPES.script,
  CANVAS_NODE_TYPES.pano360Viewer,
  CANVAS_NODE_TYPES.threeDWorld,
];

// 「从右侧 source handle 出发能创建哪些下游节点」的产品白名单。这是 + 菜单
// （NodeSpawnPlusOverlay）和拖线落空菜单（Canvas.handleConnectEnd）共用的那一份。
// 不在表中的源类型回落到注册表的 connectMenu.fromSource 默认列表。
//
// 这里写的是**产品意图**，不代表边一定建得起来 —— 建边规则（上面两张表 +
// SYSTEM_ONLY_CONNECTIONS）才是最终裁决，取用时会再过一道。两者若打架，测试
// 「菜单白名单不与建边规则冲突」会红。
export const DOWNSTREAM_SPAWN_WHITELIST: Partial<
  Record<CanvasNodeType, readonly CanvasNodeType[]>
> = {
  // 视频：仅允许 文本 / 视频 / 视频合成 / 脚本 —— 图片、音频、多版本不该作为下游。
  [CANVAS_NODE_TYPES.video]: [
    CANVAS_NODE_TYPES.textAnnotation,
    CANVAS_NODE_TYPES.video,
    CANVAS_NODE_TYPES.videoCompose,
    CANVAS_NODE_TYPES.script,
  ],
  // 音频：下游只有视频（声轨素材）与视频合成（音频轨）读得懂。
  [CANVAS_NODE_TYPES.audio]: [CANVAS_NODE_TYPES.video, CANVAS_NODE_TYPES.videoCompose],
  // 360° 全景查看器：截图都是图片，下游只接图片类节点。
  [CANVAS_NODE_TYPES.pano360Viewer]: [
    CANVAS_NODE_TYPES.imageGen,
    CANVAS_NODE_TYPES.imageEdit,
    CANVAS_NODE_TYPES.exportImage,
    CANVAS_NODE_TYPES.upload,
  ],
  [CANVAS_NODE_TYPES.upload]: IMAGE_DOWNSTREAM_SPAWN_TYPES,
  [CANVAS_NODE_TYPES.imageEdit]: IMAGE_DOWNSTREAM_SPAWN_TYPES,
  [CANVAS_NODE_TYPES.imageGen]: IMAGE_DOWNSTREAM_SPAWN_TYPES,
  [CANVAS_NODE_TYPES.exportImage]: IMAGE_DOWNSTREAM_SPAWN_TYPES,
  // R18 剧本：下游唯一嫡系是「R18 分镜」（消费 planResult.scenes）。
  [CANVAS_NODE_TYPES.nsfwScript]: [CANVAS_NODE_TYPES.nsfwStoryboard],
  // R18 分镜：spawn 的首帧子节点之外，嫡系下游是「R18 出片」。
  [CANVAS_NODE_TYPES.nsfwStoryboard]: [
    CANVAS_NODE_TYPES.nsfwVideoBatch,
    ...IMAGE_DOWNSTREAM_SPAWN_TYPES,
  ],
  // ---- R18 制作工厂流水线（+ 菜单候选）----
  [CANVAS_NODE_TYPES.nsfwFactoryInit]: [CANVAS_NODE_TYPES.nsfwFactoryScript],
  [CANVAS_NODE_TYPES.nsfwFactoryScript]: [CANVAS_NODE_TYPES.nsfwFactoryStoryboard],
  [CANVAS_NODE_TYPES.nsfwFactoryStoryboard]: [
    CANVAS_NODE_TYPES.nsfwFactoryAsset,
    CANVAS_NODE_TYPES.nsfwFactoryShot,
  ],
  [CANVAS_NODE_TYPES.nsfwFactoryAsset]: [CANVAS_NODE_TYPES.nsfwFactoryShot],
  [CANVAS_NODE_TYPES.nsfwFactoryShot]: [
    CANVAS_NODE_TYPES.nsfwFactoryAudio,
    CANVAS_NODE_TYPES.nsfwFactoryCompose,
  ],
  [CANVAS_NODE_TYPES.nsfwFactoryAudio]: [CANVAS_NODE_TYPES.nsfwFactoryCompose],
  [CANVAS_NODE_TYPES.nsfwFactoryCompose]: [CANVAS_NODE_TYPES.nsfwFactoryQc],
};

// ---- 主菜单/通用「+」菜单隐藏集（2026-08-19 深度精简）----
// 旧分步三件套（R18 剧本/分镜/出片，已被工厂工序覆盖）与工厂 8 个单工序节点
// 不再出现在主添加菜单和普通节点的「+」候选里——常规用法走「R18 制作流水线」
// 一键按钮；单工序仍可从链上前一工序节点的「+」上下文 spawn（白名单路径不过
// 这道过滤）。旧画布里的既有节点渲染不受影响（只藏菜单，不删类型）。
export const MENU_HIDDEN_NODE_TYPES: ReadonlySet<CanvasNodeType> = new Set([
  CANVAS_NODE_TYPES.nsfwScript,
  CANVAS_NODE_TYPES.nsfwStoryboard,
  CANVAS_NODE_TYPES.nsfwVideoBatch,
  CANVAS_NODE_TYPES.nsfwFactoryInit,
  CANVAS_NODE_TYPES.nsfwFactoryScript,
  CANVAS_NODE_TYPES.nsfwFactoryAsset,
  CANVAS_NODE_TYPES.nsfwFactoryStoryboard,
  CANVAS_NODE_TYPES.nsfwFactoryShot,
  CANVAS_NODE_TYPES.nsfwFactoryAudio,
  CANVAS_NODE_TYPES.nsfwFactoryCompose,
  CANVAS_NODE_TYPES.nsfwFactoryQc,
]);

// 给定起源节点类型，返回「从右侧 source handle 出发能创建的下游节点类型集」。
export function getDownstreamSpawnTypes(
  originType: CanvasNodeType | undefined,
): CanvasNodeType[] {
  const base = getConnectMenuNodeTypes('source');
  if (!originType) return base.filter((type) => !MENU_HIDDEN_NODE_TYPES.has(type));
  // 再过一遍手工建边规则：菜单里出现的候选必须真的连得上。否则用户点了以后新节点
  // 建出来了、边被建边收口拒掉，画布上留下一个孤立节点（如脚本 → 音频）。
  const connectable = base.filter((type) => isManualConnectionAllowed(originType, type));
  const allowed = DOWNSTREAM_SPAWN_WHITELIST[originType];
  if (allowed) {
    // 有白名单（含工厂链式 spawn）：按白名单收口，不过隐藏集——链上工序
    // 本身就是隐藏类型，必须能从「+」spawn。
    return connectable.filter((type) => allowed.includes(type));
  }
  // 无白名单回落（普通节点的「+」）：过滤隐藏节点，避免 R18 全家桶刷屏。
  return connectable.filter((type) => !MENU_HIDDEN_NODE_TYPES.has(type));
}

// 「从左侧 target handle 出发能创建哪些上游节点」的产品白名单 —— 上面那张表的
// 镜像。同样只是产品意图，取用时仍要过手工建边规则。
export const UPSTREAM_SPAWN_WHITELIST: Partial<
  Record<CanvasNodeType, readonly CanvasNodeType[]>
> = {
  // 3D 世界：上游仅允许文本 / 图片节点（Phase 1）。
  [CANVAS_NODE_TYPES.threeDWorld]: [
    CANVAS_NODE_TYPES.textAnnotation,
    CANVAS_NODE_TYPES.imageGen,
  ],
  // 图片节点：上游仅允许 文本（textAnnotation / script）+ 图片（upload），
  // 不收音频 / 视频 / 3D 世界。
  [CANVAS_NODE_TYPES.imageGen]: [
    CANVAS_NODE_TYPES.textAnnotation,
    CANVAS_NODE_TYPES.script,
    CANVAS_NODE_TYPES.upload,
  ],
  // 视频节点：上游仅允许 文本 / 图片 / 音频，跟 NodeSpawnPlusOverlay 左侧「+」
  // 按钮的白名单保持一致。
  [CANVAS_NODE_TYPES.video]: [
    CANVAS_NODE_TYPES.textAnnotation,
    CANVAS_NODE_TYPES.imageGen,
    CANVAS_NODE_TYPES.audio,
  ],
  // R18 分镜：上游 = R18 剧本（scenes 来源）+ 任意图片节点（定妆照锚定）。
  [CANVAS_NODE_TYPES.nsfwStoryboard]: [
    CANVAS_NODE_TYPES.nsfwScript,
    CANVAS_NODE_TYPES.upload,
    CANVAS_NODE_TYPES.imageGen,
    CANVAS_NODE_TYPES.nsfwImageGen,
    CANVAS_NODE_TYPES.exportImage,
  ],
  // R18 出片：上游唯一嫡系是「R18 分镜」（frames + 首帧来源）。
  [CANVAS_NODE_TYPES.nsfwVideoBatch]: [CANVAS_NODE_TYPES.nsfwStoryboard],
  // R18 短剧工厂：上游可选图片节点（定妆照锚定）。
  [CANVAS_NODE_TYPES.nsfwDramaStudio]: [
    CANVAS_NODE_TYPES.upload,
    CANVAS_NODE_TYPES.imageGen,
    CANVAS_NODE_TYPES.nsfwImageGen,
    CANVAS_NODE_TYPES.exportImage,
  ],
  // 音频的「+ 菜单」只给文本（video/batch 边是系统 spawn 的溯源边，建边
  // 规则放行但菜单不提供——与 node-registry 测试约定一致）
  [CANVAS_NODE_TYPES.audio]: [CANVAS_NODE_TYPES.textAnnotation],
  // ---- R18 制作工厂流水线（左侧「+」上游候选，镜像下游表）----
  [CANVAS_NODE_TYPES.nsfwFactoryScript]: [CANVAS_NODE_TYPES.nsfwFactoryInit],
  [CANVAS_NODE_TYPES.nsfwFactoryStoryboard]: [CANVAS_NODE_TYPES.nsfwFactoryScript],
  [CANVAS_NODE_TYPES.nsfwFactoryAsset]: [
    CANVAS_NODE_TYPES.nsfwFactoryStoryboard,
    CANVAS_NODE_TYPES.upload,
    CANVAS_NODE_TYPES.imageGen,
    CANVAS_NODE_TYPES.nsfwImageGen,
    CANVAS_NODE_TYPES.exportImage,
  ],
  [CANVAS_NODE_TYPES.nsfwFactoryShot]: [
    CANVAS_NODE_TYPES.nsfwFactoryAsset,
    CANVAS_NODE_TYPES.nsfwFactoryStoryboard,
  ],
  [CANVAS_NODE_TYPES.nsfwFactoryAudio]: [CANVAS_NODE_TYPES.nsfwFactoryShot],
  [CANVAS_NODE_TYPES.nsfwFactoryCompose]: [
    CANVAS_NODE_TYPES.nsfwFactoryAudio,
    CANVAS_NODE_TYPES.nsfwFactoryShot,
  ],
  [CANVAS_NODE_TYPES.nsfwFactoryQc]: [CANVAS_NODE_TYPES.nsfwFactoryCompose],
};

// 给定目标节点类型，返回「从左侧 target handle 出发能创建的上游节点类型集」。
//
// 与下游那侧不同，这里多数分支**不沿用 base**：base 只收 connectMenu.fromTarget
// 为 true 的类型，而 textAnnotation / imageGen 的 fromTarget 是 false，沿用就会
// 被错杀。所以白名单在这里是「重写候选集」而不是「过滤候选集」。
export function getUpstreamSpawnTypes(
  originType: CanvasNodeType | undefined,
): CanvasNodeType[] {
  const base = getConnectMenuNodeTypes('target');
  if (!originType) return base;
  // 所有分支最后都过一道手工建边规则：菜单给的候选必须是用户真能连的。少了这道，
  // 音频节点的上游菜单会冒出「视频」——那是「人声/背景音分离」的溯源边，音频节点
  // 只读文本上游，用户手工连一根视频进来什么都不会发生。
  const manual = (types: readonly CanvasNodeType[]) =>
    types.filter((type) => isManualConnectionAllowed(type, originType));

  // 3D 世界是唯一与 base 取交集的分支（历史行为，未在本次改动中调整）。
  if (originType === CANVAS_NODE_TYPES.threeDWorld) {
    const allowed = UPSTREAM_SPAWN_WHITELIST[originType] ?? [];
    return manual(base.filter((type) => allowed.includes(type)));
  }

  const declared = UPSTREAM_SPAWN_WHITELIST[originType];
  if (declared) {
    return manual(declared);
  }

  // 受上游类型白名单约束的目标（如音频←文本）：直接用领域层白名单，保证连线菜单、
  // 手动拖线、isValidConnection、store 建边收口共用同一份规则。
  const allowedUpstream = getAllowedUpstreamSourceTypes(originType);
  if (allowedUpstream) {
    return manual(allowedUpstream);
  }

  // 最终回落（普通节点的左侧「+」）：过滤隐藏节点（同 getDownstreamSpawnTypes）。
  return manual(base).filter((type) => !MENU_HIDDEN_NODE_TYPES.has(type));
}
