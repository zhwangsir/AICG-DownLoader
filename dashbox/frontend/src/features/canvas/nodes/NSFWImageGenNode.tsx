// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
/**
 * R18 图片节点 —— 与普通图片节点功能同构（提示词 / 负面词 / 底模选择 /
 * 尺寸 / 参考图锚定 / 连线引用 / 结果回填画布），但提交走 model-library
 * 本地 NSFW 管线（checkpoint 级 SDXL 出图，产物落盘项目媒体）。
 *
 * - 菜单入口仅在 R18 开启后出现（CanvasAddNodeGrid 过滤）
 * - R18 关闭时节点本体呈锁定态：可连线、可保留，但生成禁用
 * - NSFW 底模候选由后端按 R18 开关过滤（ModelNamePicker 直连模型库）
 */
import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import {
  Handle,
  Position,
  useUpdateNodeInternals,
  type NodeProps,
} from '@xyflow/react';
import {
  AlertTriangle,
  ArrowUp,
  Flame,
  Loader2,
  ShieldAlert,
  Upload,
  X,
} from 'lucide-react';

import {
  CANVAS_NODE_TYPES,
  type NSFWImageGenNodeData,
} from '@/features/canvas/domain/canvasNodes';
import { resolveNodeDisplayName } from '@/features/canvas/domain/nodeDisplay';
import { resolveImageDisplayUrl } from '@/features/canvas/application/imageData';
import { NodeHeader, NODE_HEADER_FLOATING_POSITION_CLASS } from '@/features/canvas/ui/NodeHeader';
import { NodeGenerationOverlay } from '@/features/canvas/ui/NodeGenerationOverlay';
import { CanvasNodeImage } from '@/features/canvas/ui/CanvasNodeImage';
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
  useGenerateImage,
  useNsfwStatus,
} from '@/lib/queries/model-library';
import { ModelNamePicker } from '@/components/settings/model-name-picker';
import { uploadFreezoneImage } from '@/api/ops';
import { useUpstreamImages } from '@/features/canvas/application/useUpstreamGraph';

type NSFWImageGenNodeProps = NodeProps & {
  id: string;
  data: NSFWImageGenNodeData;
  selected?: boolean;
};

const DEFAULT_WIDTH = 480;
const DEFAULT_HEIGHT = 320;
const OPERATIONS_PANEL_HEIGHT = 232;
const OPERATIONS_PANEL_GAP = 12;

/** SDXL 原生分辨率档（与后端 local_gateway SDXL 工作流对齐）。 */
const SIZE_PRESETS: ReadonlyArray<{ value: string; label: string }> = [
  { value: '1216x832', label: '横 3:2' },
  { value: '832x1216', label: '竖 2:3' },
  { value: '1024x1024', label: '方 1:1' },
  { value: '1152x896', label: '横 4:3' },
  { value: '896x1152', label: '竖 3:4' },
  { value: '1344x768', label: '横 16:9' },
];

/** 相对项目媒体 URL → local_gateway 可下载的绝对地址。 */
function toAbsoluteUrl(url: string): string {
  if (/^https?:\/\//i.test(url) || url.startsWith('data:')) return url;
  return `${window.location.origin}${url.startsWith('/') ? '' : '/'}${url}`;
}

export const NSFWImageGenNode = memo(({ id, data, selected }: NSFWImageGenNodeProps) => {
  const updateNodeInternals = useUpdateNodeInternals();
  const updateNodeData = useCanvasStore((state) => state.updateNodeData);
  const setSelectedNode = useCanvasStore((state) => state.setSelectedNode);
  const queryClient = useQueryClient();

  const { data: nsfwStatusData, isLoading: nsfwLoading } = useNsfwStatus();
  const nsfwEnabled = nsfwStatusData?.data?.nsfw_enabled === true;

  const generateImage = useGenerateImage();
  const upstreamImages = useUpstreamImages(id);

  const resolvedTitle = useMemo(
    () => resolveNodeDisplayName(CANVAS_NODE_TYPES.nsfwImageGen, data),
    [data],
  );

  const prompt = typeof data.prompt === 'string' ? data.prompt : '';
  const negativePrompt =
    typeof data.negativePrompt === 'string' ? data.negativePrompt : '';
  const checkpoint = typeof data.checkpoint === 'string' ? data.checkpoint : '';
  const size = typeof data.size === 'string' && data.size ? data.size : '1216x832';
  const isGenerating = data.isGenerating === true;
  const generationError =
    typeof data.generationError === 'string' && data.generationError.length > 0
      ? data.generationError
      : null;
  const referenceImageUrl =
    typeof data.referenceImageUrl === 'string' && data.referenceImageUrl.length > 0
      ? data.referenceImageUrl
      : null;
  // 锚定参考图优先级：自身上传 > 上游连线第一张。
  const anchorImageUrl = referenceImageUrl ?? upstreamImages[0] ?? null;

  const [naturalSize, setNaturalSize] = useState<{ width: number; height: number } | null>(null);
  const [isUploading, setIsUploading] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    updateNodeInternals(id);
  }, [id, updateNodeInternals]);

  const previewUrl = useMemo(() => {
    if (data.previewImageUrl) return resolveImageDisplayUrl(data.previewImageUrl);
    if (data.imageUrl) return resolveImageDisplayUrl(data.imageUrl);
    if (anchorImageUrl) return resolveImageDisplayUrl(anchorImageUrl);
    return null;
  }, [data.imageUrl, data.previewImageUrl, anchorImageUrl]);
  const visiblePreviewUrl = isGenerating ? null : previewUrl;
  const hasGeneratedResult = Boolean(data.imageUrl);

  const submitDisabled =
    !nsfwEnabled ||
    isGenerating ||
    prompt.trim().length === 0 ||
    checkpoint.trim().length === 0;

  const handleSubmit = useCallback(async () => {
    if (submitDisabled || isGenerating) return;
    const projectId = readUrl().project;
    if (!projectId) {
      updateNodeData(id, { generationError: '缺少项目上下文（project 参数）' });
      return;
    }
    const effectivePrompt = prompt.trim();
    const anchor = anchorImageUrl ?? '';
    updateNodeData(id, {
      isGenerating: true,
      generationStartedAt: Date.now(),
      generationError: null,
    });
    try {
      const result = await generateImage.mutateAsync({
        prompt: effectivePrompt,
        negative_prompt: negativePrompt.trim(),
        checkpoint: checkpoint.trim(),
        size,
        project_id: projectId,
        // 参考图可选：有则 IPAdapter 锚定（images/edits），无则纯文生图
        ...(anchor ? { reference_url: toAbsoluteUrl(anchor) } : {}),
      });
      const payload = result.ok ? result.data : null;
      if (payload?.url) {
        updateNodeData(id, {
          imageUrl: payload.url,
          previewImageUrl: payload.url,
          isGenerating: false,
          generationStartedAt: null,
          generationError: null,
        });
        return;
      }
      // 无 project 落盘路径的兜底：b64 内嵌直接显示（正常画布流程不会走到）。
      const b64 = payload?.data?.[0]?.b64_json ?? '';
      if (b64) {
        const dataUrl = `data:image/png;base64,${b64}`;
        updateNodeData(id, {
          imageUrl: dataUrl,
          previewImageUrl: dataUrl,
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
      // 失败可能伴随后端把底模记入 SDXL 不兼容清单（422 场景）——刷新模型库
      // 缓存让 picker 立即禁选该底模，避免用户原地再踩一次。
      void queryClient.invalidateQueries({ queryKey: ['model-library', 'models'] });
    }
  }, [
    anchorImageUrl,
    checkpoint,
    generateImage,
    id,
    isGenerating,
    negativePrompt,
    prompt,
    queryClient,
    size,
    submitDisabled,
    updateNodeData,
  ]);

  const handleUploadFile = useCallback(
    async (file: File) => {
      const projectId = readUrl().project;
      if (!projectId) return;
      setIsUploading(true);
      try {
        const uploaded = await uploadFreezoneImage(projectId, file, file.name);
        updateNodeData(id, { referenceImageUrl: uploaded.url });
      } catch (error) {
        console.error('[nsfw-image-gen] upload failed', error);
      } finally {
        setIsUploading(false);
      }
    },
    [id, updateNodeData],
  );

  // ── R18 未开启：锁定态（保留连线把手，禁止生成）──
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
          icon={<Flame className="h-4 w-4 text-amber-300/80" />}
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
        icon={<Flame className="h-4 w-4 text-amber-300/80" />}
        titleText={resolvedTitle}
        editable
        onTitleChange={(nextTitle) => updateNodeData(id, { displayName: nextTitle })}
      />

      <div
        className={`relative flex h-full w-full items-center justify-center overflow-hidden rounded-[var(--node-radius)] border transition-colors ${
          visiblePreviewUrl ? CANVAS_NODE_PANEL_SURFACE_CLASS : CANVAS_NODE_INPUT_SURFACE_CLASS
        } ${canvasNodeFrameClass({ selected })} ${visiblePreviewUrl ? '' : CANVAS_NODE_INPUT_BODY_FRAME_CLASS}`}
      >
        {visiblePreviewUrl ? (
          <>
            <CanvasNodeImage
              src={visiblePreviewUrl}
              alt={resolvedTitle}
              viewerSourceUrl={visiblePreviewUrl}
              onLoad={(event) => {
                const w = event.currentTarget.naturalWidth;
                const h = event.currentTarget.naturalHeight;
                if (w > 0 && h > 0) {
                  setNaturalSize((prev) =>
                    prev && prev.width === w && prev.height === h ? prev : { width: w, height: h },
                  );
                }
              }}
              className="h-full w-full object-contain"
            />
            {!hasGeneratedResult && anchorImageUrl && !isGenerating && referenceImageUrl && (
              <button
                type="button"
                onClick={(event) => {
                  event.stopPropagation();
                  updateNodeData(id, { referenceImageUrl: null });
                }}
                title="移除参考图"
                className="nodrag absolute right-2 top-2 inline-flex h-6 w-6 items-center justify-center rounded-full bg-black/55 text-white transition-colors hover:bg-black/75"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            )}
          </>
        ) : (
          <div className="flex h-full w-full flex-col items-center justify-center gap-2 text-text-muted/55">
            <Flame className="h-8 w-8 text-amber-300/50" aria-hidden />
            <span className="text-[12px]">
              R18 图片生成
              {checkpoint.trim().length === 0
                ? ' · 先在面板选择底模'
                : anchorImageUrl
                  ? ''
                  : ' · 提示词即可生成，参考图可选'}
            </span>
          </div>
        )}

        {isGenerating && (
          <NodeGenerationOverlay
            startedAt={data.generationStartedAt ?? null}
            durationMs={data.generationDurationMs}
            hasBackground={Boolean(visiblePreviewUrl)}
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

      {naturalSize && (
        <div
          className="absolute -top-7 right-1 z-20 flex items-center gap-1 rounded-md border border-white/10 bg-black/55 px-2 py-0.5 text-[11px] font-medium tabular-nums text-white/70 backdrop-blur-sm"
          title="分辨率"
        >
          <Flame className="h-3 w-3 text-amber-300/60" />
          {naturalSize.width}×{naturalSize.height}
        </div>
      )}

      {selected && nsfwEnabled && !isGenerating && (
        <div
          className={`nodrag absolute left-1/2 z-10 flex -translate-x-1/2 flex-col gap-2 rounded-[var(--node-radius)] p-3 ${CANVAS_NODE_OPS_PANEL_CLASS}`}
          style={{
            top: `calc(100% + ${OPERATIONS_PANEL_GAP}px)`,
            height: OPERATIONS_PANEL_HEIGHT,
            width: Math.max(DEFAULT_WIDTH, 560),
          }}
          onClick={(event) => event.stopPropagation()}
        >
          <div className="flex items-center gap-2">
            <div className="min-w-0 flex-1">
              <ModelNamePicker
                value={checkpoint}
                onChange={(next) => updateNodeData(id, { checkpoint: next })}
                expectedTypes={['checkpoints']}
                ariaLabel="R18 底模"
                getOptionDisabledReason={(entry) =>
                  entry.sdxl_incompatible
                    ? (entry.sdxl_incompatible_reason ?? '不兼容 SDXL 工作流')
                    : null
                }
              />
            </div>
            {checkpoint.trim().length === 0 && (
              <span className="shrink-0 text-[11px] leading-4 text-amber-300/85">
                ← 先选底模
              </span>
            )}
            <div className="flex shrink-0 items-center gap-1">
              {SIZE_PRESETS.map((preset) => (
                <button
                  key={preset.value}
                  type="button"
                  title={preset.value}
                  onClick={() => updateNodeData(id, { size: preset.value })}
                  className={`h-7 rounded-md px-2 text-[11px] transition-colors ${
                    size === preset.value
                      ? 'bg-white/[0.13] text-text-dark ring-1 ring-white/24'
                      : 'bg-white/[0.07] text-text-muted/95 hover:bg-white/[0.11] hover:text-text-dark'
                  }`}
                >
                  {preset.label}
                </button>
              ))}
            </div>
          </div>

          <textarea
            value={prompt}
            onChange={(event) => updateNodeData(id, { prompt: event.target.value })}
            placeholder="描述想要的成人向画面（触发词可参考模型库训练词）…"
            className="min-h-0 w-full flex-1 resize-none whitespace-pre-wrap break-words border-none bg-transparent px-1 py-1 text-sm leading-6 text-text-dark outline-none placeholder:text-text-muted/45"
          />

          <input
            value={negativePrompt}
            onChange={(event) => updateNodeData(id, { negativePrompt: event.target.value })}
            placeholder="负面提示词（可选）"
            className="w-full rounded-md border border-white/10 bg-white/[0.05] px-2 py-1.5 text-xs text-text-dark outline-none placeholder:text-text-muted/45 focus:border-white/25"
          />

          <div className="flex items-center justify-between gap-2">
            <div className="flex min-w-0 items-center gap-2">
              <button
                type="button"
                disabled={isUploading}
                title="上传参考图（IPAdapter 锚定）"
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
                <span>{referenceImageUrl ? '换参考图' : '上传参考图'}</span>
              </button>
              {anchorImageUrl && (
                <img
                  src={resolveImageDisplayUrl(anchorImageUrl)}
                  alt=""
                  className="h-8 w-8 rounded-md border border-white/15 object-cover"
                  draggable={false}
                  title={
                    referenceImageUrl
                      ? '自身上传的参考图'
                      : '来自上游连线的参考图'
                  }
                />
              )}
            </div>
            <button
              type="button"
              disabled={submitDisabled}
              title={
                prompt.trim().length === 0
                  ? '先输入提示词'
                  : checkpoint.trim().length === 0
                    ? '先选择底模'
                    : anchorImageUrl
                      ? '生成 R18 图片（IPAdapter 锚定参考图）'
                      : '生成 R18 图片（纯文生图，无参考图）'
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

NSFWImageGenNode.displayName = 'NSFWImageGenNode';
