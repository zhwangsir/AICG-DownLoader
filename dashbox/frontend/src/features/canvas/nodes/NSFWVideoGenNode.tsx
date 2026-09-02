// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
/**
 * R18 视频节点 —— 短剧/漫剧成片引擎钉死 MiniMax H3（Wan JSON 留盘，不进可选目录）：
 * - MiniMax H3（全能动作+音画 / 剧情无 LoRA）→ :8195
 * - 画布 768×1344 24fps
 *
 * 与 R18 图片节点同构的门禁：R18 关闭时锁定态；首帧锚定强制（自身上传
 * 或上游连线）；产物 mp4 落盘项目媒体（videoUrl 回填，下游可引用）。
 * 视频出片慢（约 4 分钟），生成中节点保持 loading 遮罩（后端同步等待）。
 */
import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Handle,
  Position,
  useUpdateNodeInternals,
  type NodeProps,
} from '@xyflow/react';
import {
  AlertTriangle,
  ArrowUp,
  Clapperboard,
  Loader2,
  ShieldAlert,
  Upload,
} from 'lucide-react';

import {
  CANVAS_NODE_TYPES,
  type NSFWVideoGenNodeData,
} from '@/features/canvas/domain/canvasNodes';
import { resolveNodeDisplayName } from '@/features/canvas/domain/nodeDisplay';
import { resolveImageDisplayUrl } from '@/features/canvas/application/imageData';
import { NodeHeader, NODE_HEADER_FLOATING_POSITION_CLASS } from '@/features/canvas/ui/NodeHeader';
import { NodeGenerationOverlay } from '@/features/canvas/ui/NodeGenerationOverlay';
import { RegenerateButton } from '@/features/canvas/ui/RegenerateButton';
import {
  CANVAS_NODE_INPUT_BODY_FRAME_CLASS,
  CANVAS_NODE_INPUT_SURFACE_CLASS,
  CANVAS_NODE_OPS_PANEL_CLASS,
  CANVAS_NODE_PANEL_SURFACE_CLASS,
  canvasNodeFrameClass,
} from '@/features/canvas/ui/nodeFrameStyles';
import {
  NODE_GENERATE_BUTTON_BASE_CLASS,
  NODE_GENERATE_BUTTON_DISABLED_CLASS,
  NODE_GENERATE_BUTTON_ENABLED_CLASS,
  NODE_TEXT_CONTROL_TRIGGER_CLASS,
} from '@/features/canvas/ui/nodeControlStyles';
import { useCanvasStore } from '@/stores/canvasStore';
import { readUrl } from '@/lib/url-params';
import {
  useGenerateVideo,
  useNsfwStatus,
  useVideoPresets,
  type NsfwVideoPreset,
} from '@/lib/queries/model-library';
import { uploadFreezoneImage } from '@/api/ops';
import { useUpstreamImages } from '@/features/canvas/application/useUpstreamGraph';

type NSFWVideoGenNodeProps = NodeProps & {
  id: string;
  data: NSFWVideoGenNodeData;
  selected?: boolean;
};

const DEFAULT_WIDTH = 480;
const DEFAULT_HEIGHT = 300;
const OPERATIONS_PANEL_HEIGHT = 252;
const OPERATIONS_PANEL_GAP = 12;

/** H3 画布钉死 768P（768×1344 竖 / 1344×768 横），24fps。 */
const H3_SIZE_PRESETS: ReadonlyArray<{ w: number; h: number; label: string }> = [
  { w: 1344, h: 768, label: '横 16:9' },
  { w: 768, h: 1344, label: '竖 9:16' },
];
/** 帧数档：h3 24fps（124≈5s / 241≈10s，17k+5 网格）。 */
const LENGTH_PRESETS: ReadonlyArray<{ length: number; label: string }> = [
  { length: 124, label: '5s' },
  { length: 241, label: '10s' },
];

/** 相对项目媒体 URL → 后端可下载的绝对地址。 */
function toAbsoluteUrl(url: string): string {
  if (/^https?:\/\//i.test(url) || url.startsWith('data:')) return url;
  return `${window.location.origin}${url.startsWith('/') ? '' : '/'}${url}`;
}

export const NSFWVideoGenNode = memo(({ id, data, selected }: NSFWVideoGenNodeProps) => {
  const updateNodeInternals = useUpdateNodeInternals();
  const updateNodeData = useCanvasStore((state) => state.updateNodeData);
  const setSelectedNode = useCanvasStore((state) => state.setSelectedNode);

  const { data: nsfwStatusData, isLoading: nsfwLoading } = useNsfwStatus();
  const nsfwEnabled = nsfwStatusData?.data?.nsfw_enabled === true;
  const { data: presetsData } = useVideoPresets(nsfwEnabled);
  const presets = useMemo<NsfwVideoPreset[]>(
    () => (presetsData?.data?.items ?? []).filter((item) => item.route === 'h3'),
    [presetsData],
  );

  const generateVideo = useGenerateVideo();
  const upstreamImages = useUpstreamImages(id);

  const resolvedTitle = useMemo(
    () => resolveNodeDisplayName(CANVAS_NODE_TYPES.nsfwVideoGen, data),
    [data],
  );

  const prompt = typeof data.prompt === 'string' ? data.prompt : '';
  const presetId = typeof data.presetId === 'string' ? data.presetId : '';
  const width = typeof data.width === 'number' ? data.width : 768;
  const height = typeof data.height === 'number' ? data.height : 1344;
  const length = typeof data.length === 'number' ? data.length : 124;
  const isGenerating = data.isGenerating === true;
  const generationError =
    typeof data.generationError === 'string' && data.generationError.length > 0
      ? data.generationError
      : null;
  const firstFrameUrl =
    typeof data.firstFrameUrl === 'string' && data.firstFrameUrl.length > 0
      ? data.firstFrameUrl
      : null;
  const anchorImageUrl = firstFrameUrl ?? upstreamImages[0] ?? null;

  const [isUploading, setIsUploading] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    updateNodeInternals(id);
  }, [id, updateNodeInternals]);

  // 选预设时同步尺寸/帧数默认档（用户可再改）。
  const activePreset = presets.find((p) => p.id === presetId) ?? null;
  useEffect(() => {
    if (!activePreset) return;
    const sizes = H3_SIZE_PRESETS;
    const len = LENGTH_PRESETS[0];
    updateNodeData(id, {
      width: sizes[1].w,
      height: sizes[1].h,
      length: len?.length ?? 124,
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps -- 仅在切换预设时同步
  }, [presetId]);

  const submitDisabled =
    !nsfwEnabled ||
    isGenerating ||
    prompt.trim().length === 0 ||
    presetId.length === 0 ||
    !anchorImageUrl;

  const handleSubmit = useCallback(async () => {
    if (submitDisabled || isGenerating) return;
    const projectId = readUrl().project;
    if (!projectId) {
      updateNodeData(id, { generationError: '缺少项目上下文（project 参数）' });
      return;
    }
    updateNodeData(id, {
      isGenerating: true,
      generationStartedAt: Date.now(),
      generationError: null,
    });
    try {
      const result = await generateVideo.mutateAsync({
        preset_id: presetId,
        prompt: prompt.trim(),
        first_frame_url: toAbsoluteUrl(anchorImageUrl ?? ''),
        width,
        height,
        length,
        project_id: projectId,
      });
      const payload = result.ok ? result.data : null;
      if (payload?.url) {
        updateNodeData(id, {
          videoUrl: payload.url,
          aspectRatio: width > height ? '16:9' : '9:16',
          isGenerating: false,
          generationStartedAt: null,
          generationError: null,
        });
        return;
      }
      updateNodeData(id, {
        isGenerating: false,
        generationStartedAt: null,
        generationError: '生成返回为空',
      });
    } catch (error) {
      const message =
        error instanceof Error && error.message ? error.message : String(error);
      updateNodeData(id, {
        isGenerating: false,
        generationStartedAt: null,
        generationError: message,
      });
    }
  }, [
    anchorImageUrl,
    generateVideo,
    height,
    id,
    isGenerating,
    length,
    presetId,
    prompt,
    submitDisabled,
    updateNodeData,
    width,
  ]);

  const handleUploadFile = useCallback(
    async (file: File) => {
      const projectId = readUrl().project;
      if (!projectId) return;
      setIsUploading(true);
      try {
        const uploaded = await uploadFreezoneImage(projectId, file, file.name);
        updateNodeData(id, { firstFrameUrl: uploaded.url });
      } catch (error) {
        console.error('[nsfw-video-gen] upload failed', error);
      } finally {
        setIsUploading(false);
      }
    },
    [id, updateNodeData],
  );

  // ── R18 未开启：锁定态 ──
  if (!nsfwLoading && !nsfwEnabled) {
    return (
      <div
        className={`relative flex h-full w-full flex-col items-center justify-center gap-2 rounded-[var(--node-radius)] border border-amber-400/30 bg-amber-950/25 ${CANVAS_NODE_INPUT_SURFACE_CLASS}`}
        style={{ width: DEFAULT_WIDTH, height: DEFAULT_HEIGHT }}
        onClick={() => setSelectedNode(id)}
      >
        <Handle
          type="target"
          position={Position.Left}
          id="target"
          className="!h-2 !w-2 !border-0 !bg-[rgb(148,163,184)]"
        />
        <Handle
          type="source"
          position={Position.Right}
          id="source"
          className="!h-2 !w-2 !border-0 !bg-[rgb(148,163,184)]"
        />
        <NodeHeader
          className={NODE_HEADER_FLOATING_POSITION_CLASS}
          icon={<Clapperboard className="h-4 w-4 text-amber-300/80" />}
          titleText={resolvedTitle}
          editable
          onTitleChange={(nextTitle) => updateNodeData(id, { displayName: nextTitle })}
        />
        <ShieldAlert className="h-8 w-8 text-amber-300/70" aria-hidden />
        <div className="px-6 text-center text-[12px] leading-5 text-amber-100/75">
          R18 内容未开启。请前往「设置 → 模型库」开启 R18 后使用本节点。
        </div>
      </div>
    );
  }

  const sizePresets = H3_SIZE_PRESETS;
  const lengthOptions = LENGTH_PRESETS;

  return (
    <div
      className="group relative h-full w-full overflow-visible"
      style={{ width: DEFAULT_WIDTH, height: DEFAULT_HEIGHT }}
      onClick={() => setSelectedNode(id)}
    >
      <Handle
        type="target"
        position={Position.Left}
        id="target"
        className="!h-2 !w-2 !border-0 !bg-[rgb(148,163,184)]"
      />
      <Handle
        type="source"
        position={Position.Right}
        id="source"
        className="!h-2 !w-2 !border-0 !bg-[rgb(148,163,184)]"
      />

      <NodeHeader
        className={NODE_HEADER_FLOATING_POSITION_CLASS}
        icon={<Clapperboard className="h-4 w-4 text-amber-300/80" />}
        titleText={resolvedTitle}
        editable
        onTitleChange={(nextTitle) => updateNodeData(id, { displayName: nextTitle })}
      />

      <div
        className={`relative flex h-full w-full items-center justify-center overflow-hidden rounded-[var(--node-radius)] border transition-colors ${
          data.videoUrl ? CANVAS_NODE_PANEL_SURFACE_CLASS : CANVAS_NODE_INPUT_SURFACE_CLASS
        } ${canvasNodeFrameClass({ selected })} ${data.videoUrl ? '' : CANVAS_NODE_INPUT_BODY_FRAME_CLASS}`}
      >
        {data.videoUrl && !isGenerating ? (
          <video
            src={resolveImageDisplayUrl(data.videoUrl)}
            controls
            playsInline
            className="h-full w-full object-contain"
            onClick={(event) => event.stopPropagation()}
          />
        ) : (
          <div className="flex h-full w-full flex-col items-center justify-center gap-2 text-text-muted/55">
            <Clapperboard className="h-8 w-8 text-amber-300/50" aria-hidden />
            <span className="text-[12px]">
              R18 视频
              {anchorImageUrl
                ? ''
                : upstreamImages.length > 0
                  ? ' · 上游图片节点还没有产出图'
                  : ' · 连线或上传首帧图'}
            </span>
          </div>
        )}

        {isGenerating && (
          <NodeGenerationOverlay
            startedAt={data.generationStartedAt ?? null}
            durationMs={data.generationDurationMs ?? 300000}
            hasBackground={Boolean(data.videoUrl)}
          />
        )}

        {!isGenerating && generationError && (
          <div className="nodrag absolute inset-x-5 top-1/2 z-10 flex -translate-y-1/2 flex-col items-center text-center">
            <div className="inline-flex items-center gap-1.5 text-[12px] font-semibold text-red-200">
              <AlertTriangle className="h-3.5 w-3.5 shrink-0 text-red-300/90" />
              <span>生成失败</span>
            </div>
            <div
              className="mt-1 max-h-16 max-w-full overflow-y-auto break-words text-[11px] leading-4 text-red-100/76 [overflow-wrap:anywhere]"
              title={generationError}
            >
              {generationError}
            </div>
            <div className="mt-2 flex justify-center">
              <RegenerateButton
                onClick={() => void handleSubmit()}
                busy={isGenerating}
                disabled={submitDisabled}
              />
            </div>
          </div>
        )}
      </div>

      {selected && nsfwEnabled && !isGenerating && (
        <div
          className={`nodrag absolute left-1/2 z-10 flex -translate-x-1/2 flex-col gap-2 rounded-[var(--node-radius)] p-3 ${CANVAS_NODE_OPS_PANEL_CLASS}`}
          style={{
            top: `calc(100% + ${OPERATIONS_PANEL_GAP}px)`,
            height: OPERATIONS_PANEL_HEIGHT,
            width: Math.max(DEFAULT_WIDTH, 620),
          }}
          onClick={(event) => event.stopPropagation()}
        >
          {/* 预设选择：H3 成片链（Wan 已从短剧目录隐藏） */}
          <div className="flex items-center gap-1.5">
            {presets.map((preset) => (
              <button
                key={preset.id}
                type="button"
                title={`${preset.label} · 触发词: ${preset.trigger}`}
                onClick={() => updateNodeData(id, { presetId: preset.id })}
                className={`h-7 flex-1 truncate rounded-md px-2 text-[11px] transition-colors ${
                  presetId === preset.id
                    ? 'bg-white/[0.13] text-text-dark ring-1 ring-white/24'
                    : 'bg-white/[0.07] text-text-muted/95 hover:bg-white/[0.11] hover:text-text-dark'
                }`}
              >
                {preset.label.split('（')[0]}
              </button>
            ))}
            {presets.length === 0 && (
              <span className="text-[11px] text-text-muted/60">预设加载中…</span>
            )}
          </div>

          {/* 首帧缺位主动引导：I2V 必需首帧，禁用原因只在 tooltip 里用户看不到。 */}
          {!anchorImageUrl && (
            <div className="flex items-center gap-1.5 rounded-md border border-amber-400/30 bg-amber-950/25 px-2 py-1.5 text-[11px] leading-4 text-amber-100/85">
              <ShieldAlert className="h-3.5 w-3.5 shrink-0 text-amber-300/80" />
              <span>
                图生视频需要一张首帧图：点下方「首帧」上传，或连线上游图片节点
                {upstreamImages.length > 0
                  ? '（上游节点还没有产出图，先生成或上传一张）'
                  : ''}
                。仅输入提示词无法生成。
              </span>
            </div>
          )}

          <textarea
            value={prompt}
            onChange={(event) => updateNodeData(id, { prompt: event.target.value })}
            placeholder={
              activePreset
                ? `描述画面（建议保留触发词 ${activePreset.trigger}）…`
                : '先选择预设，再描述画面…'
            }
            className="min-h-0 w-full flex-1 resize-none whitespace-pre-wrap break-words border-none bg-transparent px-1 py-1 text-sm leading-6 text-text-dark outline-none placeholder:text-text-muted/45"
          />

          <div className="flex items-center justify-between gap-2">
            <div className="flex min-w-0 items-center gap-1.5">
              {/* 尺寸档 */}
              {sizePresets.map((s) => (
                <button
                  key={`${s.w}x${s.h}`}
                  type="button"
                  title={`${s.w}×${s.h}`}
                  onClick={() => updateNodeData(id, { width: s.w, height: s.h })}
                  className={`h-7 rounded-md px-2 text-[11px] transition-colors ${
                    width === s.w && height === s.h
                      ? 'bg-white/[0.13] text-text-dark ring-1 ring-white/24'
                      : 'bg-white/[0.07] text-text-muted/95 hover:bg-white/[0.11]'
                  }`}
                >
                  {s.label}
                </button>
              ))}
              <span className="text-text-muted/50">·</span>
              {/* 时长档 */}
              {lengthOptions.map((l) => (
                <button
                  key={l.length}
                  type="button"
                  title={`${l.length} 帧`}
                  onClick={() => updateNodeData(id, { length: l.length })}
                  className={`h-7 rounded-md px-2 text-[11px] transition-colors ${
                    length === l.length
                      ? 'bg-white/[0.13] text-text-dark ring-1 ring-white/24'
                      : 'bg-white/[0.07] text-text-muted/95 hover:bg-white/[0.11]'
                  }`}
                >
                  {l.label}
                </button>
              ))}
              <button
                type="button"
                disabled={isUploading}
                title="上传首帧图（I2V 锚定）"
                onClick={(event) => {
                  event.stopPropagation();
                  fileInputRef.current?.click();
                }}
                className={NODE_TEXT_CONTROL_TRIGGER_CLASS}
              >
                {isUploading ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Upload className="h-3.5 w-3.5" />
                )}
                <span>{firstFrameUrl ? '换首帧' : '首帧'}</span>
              </button>
              {anchorImageUrl && (
                <img
                  src={resolveImageDisplayUrl(anchorImageUrl)}
                  alt=""
                  className="h-8 w-8 rounded-md border border-white/15 object-cover"
                  draggable={false}
                  title={firstFrameUrl ? '自身上传的首帧图' : '来自上游连线的首帧图'}
                />
              )}
            </div>
            <button
              type="button"
              disabled={submitDisabled}
              title={
                !anchorImageUrl
                  ? '需先上传首帧图或连线上游图片节点'
                  : !presetId
                    ? '需先选择预设'
                    : '生成 R18 视频（约 4 分钟）'
              }
              onClick={(event) => {
                event.stopPropagation();
                void handleSubmit();
              }}
              className={`${NODE_GENERATE_BUTTON_BASE_CLASS} ${
                submitDisabled
                  ? NODE_GENERATE_BUTTON_DISABLED_CLASS
                  : NODE_GENERATE_BUTTON_ENABLED_CLASS
              }`}
            >
              <ArrowUp className="h-4 w-4" />
            </button>
          </div>
        </div>
      )}

      <input
        ref={fileInputRef}
        type="file"
        accept="image/*"
        className="hidden"
        onChange={(event) => {
          const file = event.target.files?.[0];
          event.target.value = '';
          if (file) void handleUploadFile(file);
        }}
      />
    </div>
  );
});

NSFWVideoGenNode.displayName = 'NSFWVideoGenNode';
