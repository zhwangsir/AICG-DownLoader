// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
/**
 * R18 分镜节点 —— 消费上游「R18 剧本」scenes × 定妆照（IPAdapter 锚定）
 * 并发批量生成全部镜头首帧；每帧落图后自动 spawn exportImage 子节点，
 * 子节点在图像解析白名单内，可单独连线到「R18 视频节点」做 firstFrame。
 *
 * - 首帧生成走 /model-library/generate-image（复用 R18 图片管线，参考图锚定）
 * - 前端并发池（3 路）：浏览器连接数友好 + LB 三后端正好吃满
 * - 单帧可重生（剧本节点改 image_prompt 后回这里重新生成该帧）
 * - 菜单/锁定态与 R18 图片节点同口径
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
  Flame,
  Layers,
  Loader2,
  RefreshCw,
  ShieldAlert,
  Share2,
  Upload,
} from 'lucide-react';

import {
  CANVAS_NODE_TYPES,
  type NsfwStoryboardFrameItem,
  type NSFWStoryboardNodeData,
} from '@/features/canvas/domain/canvasNodes';
import { resolveNodeDisplayName } from '@/features/canvas/domain/nodeDisplay';
import { resolveImageDisplayUrl } from '@/features/canvas/application/imageData';
import { NodeHeader, NODE_HEADER_FLOATING_POSITION_CLASS } from '@/features/canvas/ui/NodeHeader';
import {
  CANVAS_NODE_INPUT_BODY_FRAME_CLASS,
  CANVAS_NODE_INPUT_SURFACE_CLASS,
  CANVAS_NODE_OPS_PANEL_CLASS,
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
  gatewayErrorMessage,
  useGenerateImage,
  useNsfwStatus,
  type R18SceneData,
} from '@/lib/queries/model-library';
import { ModelNamePicker } from '@/components/settings/model-name-picker';
import { uploadFreezoneImage } from '@/api/ops';
import {
  useUpstreamImages,
  useUpstreamNodes,
} from '@/features/canvas/application/useUpstreamGraph';

type NSFWStoryboardNodeProps = NodeProps & {
  id: string;
  data: NSFWStoryboardNodeData;
  selected?: boolean;
};

const DEFAULT_WIDTH = 460;
const DEFAULT_HEIGHT = 420;
const OPERATIONS_PANEL_HEIGHT = 250;
const OPERATIONS_PANEL_GAP = 12;
/** 前端并发池大小：LB 三后端 + 浏览器 6 连接限制的折中。 */
const BATCH_CONCURRENCY = 3;

const SIZE_PRESETS: ReadonlyArray<{ value: string; label: string }> = [
  { value: '832x1216', label: '竖 2:3' },
  { value: '1216x832', label: '横 3:2' },
  { value: '1024x1024', label: '方 1:1' },
];

/** 相对项目媒体 URL → 后端可下载的绝对地址。 */
function toAbsoluteUrl(url: string): string {
  if (/^https?:\/\//i.test(url) || url.startsWith('data:')) return url;
  return `${window.location.origin}${url.startsWith('/') ? '' : '/'}${url}`;
}

const KIND_DOT_CLASS: Record<NsfwStoryboardFrameItem['kind'], string> = {
  plot: 'bg-slate-400/70',
  action: 'bg-rose-400/80',
  portrait: 'bg-amber-400/80',
};

export const NSFWStoryboardNode = memo(({ id, data, selected }: NSFWStoryboardNodeProps) => {
  const updateNodeInternals = useUpdateNodeInternals();
  const updateNodeData = useCanvasStore((state) => state.updateNodeData);
  const setSelectedNode = useCanvasStore((state) => state.setSelectedNode);
  const queryClient = useQueryClient();

  const { data: nsfwStatusData, isLoading: nsfwLoading } = useNsfwStatus();
  const nsfwEnabled = nsfwStatusData?.data?.nsfw_enabled === true;

  const generateImage = useGenerateImage();
  const upstreamNodes = useUpstreamNodes(id);
  const upstreamImages = useUpstreamImages(id);

  const resolvedTitle = useMemo(
    () => resolveNodeDisplayName(CANVAS_NODE_TYPES.nsfwStoryboard, data),
    [data],
  );

  const checkpoint = typeof data.checkpoint === 'string' ? data.checkpoint : '';
  const size = typeof data.size === 'string' && data.size ? data.size : '832x1216';
  const frames = Array.isArray(data.frames) ? data.frames : [];
  const isBatchRunning = data.isBatchRunning === true;
  const batchError =
    typeof data.batchError === 'string' && data.batchError.length > 0
      ? data.batchError
      : null;
  const anchorUploadUrl =
    typeof data.anchorUploadUrl === 'string' && data.anchorUploadUrl.length > 0
      ? data.anchorUploadUrl
      : null;
  const anchorImageUrl = anchorUploadUrl ?? upstreamImages[0] ?? null;

  const [isUploading, setIsUploading] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    updateNodeInternals(id);
  }, [id, updateNodeInternals]);

  // ── 上游 scenes：直连的 R18 剧本节点（planResult 权威源）──
  const upstreamScenes = useMemo<R18SceneData[]>(() => {
    for (const node of upstreamNodes) {
      if (node.type !== 'nsfwScriptNode') continue;
      const plan = (node.data as { planResult?: { scenes?: R18SceneData[] } | null }).planResult;
      if (plan && Array.isArray(plan.scenes) && plan.scenes.length > 0) {
        return plan.scenes;
      }
    }
    return [];
  }, [upstreamNodes]);

  /** scenes 内容签名：变化才重建 frames（保留同 scene_no + 同提示词的已完成帧）。 */
  const scenesSignature = useMemo(
    () =>
      upstreamScenes
        .map(
          (s) =>
            `${s.scene_no}|${s.kind}|${s.image_prompt}|${s.video_prompt}|${s.preset_id}|${s.dialogue}|${s.narration}|${s.duration_sec}|${s.audio}`,
        )
        .join('\n'),
    [upstreamScenes],
  );

  useEffect(() => {
    if (upstreamScenes.length === 0) return;
    const prevById = new Map(frames.map((f) => [f.sceneNo, f] as const));
    const nextFrames: NsfwStoryboardFrameItem[] = upstreamScenes.map((scene) => {
      const prev = prevById.get(scene.scene_no);
      const promptSame = prev?.imagePrompt === scene.image_prompt;
      return {
        id: prev && promptSame ? prev.id : `r18-frame-${scene.scene_no}`,
        sceneNo: scene.scene_no,
        kind: scene.kind,
        title: scene.title ?? '',
        imagePrompt: scene.image_prompt ?? '',
        videoPrompt: scene.video_prompt ?? '',
        presetId: scene.preset_id ?? '',
        dialogue: scene.dialogue ?? '',
        narration: scene.narration ?? '',
        durationSec: scene.duration_sec || 5,
        audio: scene.audio ?? 'tts',
        // 提示词改了旧图作废；没改则保留已生成图与子节点
        imageUrl: prev && promptSame ? prev.imageUrl : null,
        isGenerating: false,
        error: null,
        childNodeId: prev && promptSame ? (prev.childNodeId ?? null) : null,
      };
    });
    const nextSignature = nextFrames
      .map((f) => `${f.sceneNo}|${f.kind}|${f.imagePrompt}|${f.videoPrompt}|${f.presetId}|${f.dialogue}|${f.narration}|${f.durationSec}|${f.audio}`)
      .join('\n');
    const currentSignature = frames
      .map((f) => `${f.sceneNo}|${f.kind}|${f.imagePrompt}|${f.videoPrompt}|${f.presetId}|${f.dialogue}|${f.narration}|${f.durationSec}|${f.audio}`)
      .join('\n');
    if (nextSignature !== currentSignature) {
      updateNodeData(id, { frames: nextFrames });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scenesSignature, id]);

  const doneCount = frames.filter((f) => f.imageUrl).length;
  const total = frames.length;

  /** 从 store 读最新 frames 打补丁（并发完成回调防闭包过期）。 */
  const updateFrame = useCallback(
    (frameId: string, patch: Partial<NsfwStoryboardFrameItem>) => {
      const node = useCanvasStore
        .getState()
        .nodes.find((n) => n.id === id);
      const current = (
        (node?.data as NSFWStoryboardNodeData | undefined)?.frames ?? []
      ) as NsfwStoryboardFrameItem[];
      updateNodeData(id, {
        frames: current.map((f) => (f.id === frameId ? { ...f, ...patch } : f)),
      });
    },
    [id, updateNodeData],
  );

  /** 首帧落图后 spawn exportImage 子节点（图像白名单内，下游视频节点可单连）。 */
  const spawnFrameChild = useCallback(
    (frame: NsfwStoryboardFrameItem): string | null => {
      if (!frame.imageUrl) return null;
      const store = useCanvasStore.getState();
      const position = store.findNodePosition(id, 220, 260);
      const childId = store.addNode(CANVAS_NODE_TYPES.exportImage, position, {
        displayName: `R18 首帧 S${frame.sceneNo}`,
        imageUrl: frame.imageUrl,
        previewImageUrl: frame.imageUrl,
        isSizeManuallyAdjusted: false,
        resultKind: 'generic',
      });
      if (childId) store.addEdge(id, childId);
      return childId;
    },
    [id],
  );

  /** 生成单帧首帧（IPAdapter 锚定定妆照），成功后 spawn 子节点。 */
  const generateFrame = useCallback(
    async (frame: NsfwStoryboardFrameItem) => {
      const projectId = readUrl().project;
      if (!projectId) {
        updateFrame(frame.id, { error: '缺少项目上下文（project 参数）', isGenerating: false });
        return;
      }
      updateFrame(frame.id, { isGenerating: true, error: null });
      try {
        const result = await generateImage.mutateAsync({
          prompt: frame.imagePrompt,
          checkpoint: checkpoint.trim(),
          size,
          project_id: projectId,
          ...(anchorImageUrl ? { reference_url: toAbsoluteUrl(anchorImageUrl) } : {}),
        });
        const payload = result.ok ? result.data : null;
        const url = payload?.url ?? '';
        if (!url) {
          updateFrame(frame.id, { isGenerating: false, error: '生成返回为空' });
          return;
        }
        const childId = spawnFrameChild({ ...frame, imageUrl: url });
        updateFrame(frame.id, {
          imageUrl: url,
          isGenerating: false,
          error: null,
          childNodeId: childId ?? null,
        });
      } catch (error) {
        updateFrame(frame.id, {
          isGenerating: false,
          error: gatewayErrorMessage(error, '首帧生成失败'),
        });
        void queryClient.invalidateQueries({ queryKey: ['model-library', 'models'] });
      }
    },
    [anchorImageUrl, checkpoint, generateImage, queryClient, size, spawnFrameChild, updateFrame],
  );

  /** 批量：待生成帧入队，3 路并发消费。 */
  const handleBatch = useCallback(async () => {
    if (isBatchRunning) return;
    const pending = frames.filter((f) => !f.imageUrl && !f.isGenerating);
    if (pending.length === 0) return;
    if (checkpoint.trim().length === 0) {
      updateNodeData(id, { batchError: '先在面板选择底模' });
      return;
    }
    updateNodeData(id, { isBatchRunning: true, batchError: null });
    const queue = [...pending];
    const worker = async () => {
      for (;;) {
        const frame = queue.shift();
        if (!frame) return;
        await generateFrame(frame);
      }
    };
    try {
      await Promise.all(
        Array.from({ length: Math.min(BATCH_CONCURRENCY, queue.length) }, worker),
      );
    } finally {
      updateNodeData(id, { isBatchRunning: false });
    }
  }, [checkpoint, frames, generateFrame, id, isBatchRunning, updateNodeData]);

  const handleUploadFile = useCallback(
    async (file: File) => {
      const projectId = readUrl().project;
      if (!projectId) return;
      setIsUploading(true);
      try {
        const uploaded = await uploadFreezoneImage(projectId, file, file.name);
        updateNodeData(id, { anchorUploadUrl: uploaded.url });
      } catch (error) {
        console.error('[nsfw-storyboard] upload failed', error);
      } finally {
        setIsUploading(false);
      }
    },
    [id, updateNodeData],
  );

  const batchDisabled =
    !nsfwEnabled || isBatchRunning || total === 0 || checkpoint.trim().length === 0 || doneCount === total;

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
        className={`relative flex h-full w-full flex-col overflow-hidden rounded-[var(--node-radius)] border transition-colors ${
          CANVAS_NODE_INPUT_SURFACE_CLASS
        } ${canvasNodeFrameClass({ selected })} ${CANVAS_NODE_INPUT_BODY_FRAME_CLASS}`}
      >
        {total === 0 && (
          <div className="flex h-full w-full flex-col items-center justify-center gap-2 text-text-muted/55">
            <Layers className="h-8 w-8 text-amber-300/50" aria-hidden />
            <span className="px-6 text-center text-[12px] leading-5">
              R18 首帧批量生成
              <br />
              连接上游「R18 剧本」节点后在此批量出全部镜头首帧
            </span>
          </div>
        )}

        {total > 0 && (
          <div className="flex h-full w-full flex-col overflow-hidden px-3 pb-2 pt-7">
            <div className="mb-1.5 flex shrink-0 items-center gap-2">
              <span className="text-[12px] font-medium text-text-dark">
                首帧 {doneCount}/{total}
              </span>
              {isBatchRunning && (
                <Loader2 className="h-3 w-3 animate-spin text-amber-300/80" />
              )}
              <div className="h-1 min-w-0 flex-1 overflow-hidden rounded-full bg-white/[0.08]">
                <div
                  className="h-full rounded-full bg-amber-400/70 transition-all"
                  style={{ width: `${total > 0 ? (doneCount / total) * 100 : 0}%` }}
                />
              </div>
              {anchorImageUrl && (
                <img
                  src={resolveImageDisplayUrl(anchorImageUrl)}
                  alt=""
                  className="h-6 w-6 rounded border border-white/15 object-cover"
                  draggable={false}
                  title="定妆照锚定（IPAdapter）"
                />
              )}
            </div>
            <div className="min-h-0 flex-1 space-y-1.5 overflow-y-auto pr-0.5">
              {frames.map((frame) => (
                <div
                  key={frame.id}
                  className="flex items-center gap-2 rounded-md border border-white/[0.07] bg-white/[0.035] px-2 py-1.5"
                >
                  {frame.imageUrl ? (
                    <img
                      src={resolveImageDisplayUrl(frame.imageUrl)}
                      alt={`S${frame.sceneNo}`}
                      className="h-12 w-12 shrink-0 rounded border border-white/12 object-cover"
                      draggable={false}
                    />
                  ) : (
                    <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded border border-dashed border-white/12">
                      {frame.isGenerating ? (
                        <Loader2 className="h-4 w-4 animate-spin text-amber-300/80" />
                      ) : (
                        <span className="text-[10px] tabular-nums text-text-muted/60">
                          S{frame.sceneNo}
                        </span>
                      )}
                    </div>
                  )}
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-1.5">
                      <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${KIND_DOT_CLASS[frame.kind] ?? 'bg-slate-400/70'}`} />
                      <span className="shrink-0 text-[10px] tabular-nums text-text-muted">
                        #{frame.sceneNo}
                      </span>
                      {frame.presetId && (
                        <span className="shrink-0 truncate rounded bg-rose-500/15 px-1 py-px text-[10px] text-rose-200/90">
                          {frame.presetId}
                        </span>
                      )}
                      <span className="min-w-0 flex-1 truncate text-[11px] text-text-dark/85">
                        {frame.title || frame.imagePrompt}
                      </span>
                      <span className="shrink-0 text-[10px] tabular-nums text-text-muted">
                        {frame.durationSec}s
                      </span>
                    </div>
                    {frame.error && (
                      <div
                        className="mt-0.5 truncate text-[10px] text-red-300/85"
                        title={frame.error}
                      >
                        {frame.error}
                      </div>
                    )}
                    {!frame.error && (frame.dialogue || frame.narration) && (
                      <div className="mt-0.5 truncate text-[10px] text-cyan-200/60">
                        {frame.dialogue || frame.narration}
                      </div>
                    )}
                  </div>
                  <div className="flex shrink-0 items-center gap-1">
                    <button
                      type="button"
                      title="重新生成该帧"
                      disabled={frame.isGenerating || isBatchRunning || checkpoint.trim().length === 0}
                      onClick={(event) => {
                        event.stopPropagation();
                        void generateFrame(frame);
                      }}
                      className="nodrag inline-flex h-6 w-6 items-center justify-center rounded-md bg-white/[0.06] text-text-muted transition-colors hover:bg-white/[0.12] hover:text-text-dark disabled:opacity-40"
                    >
                      <RefreshCw className="h-3 w-3" />
                    </button>
                    {frame.imageUrl && !frame.childNodeId && (
                      <button
                        type="button"
                        title="补建子节点（供视频节点连线）"
                        onClick={(event) => {
                          event.stopPropagation();
                          const childId = spawnFrameChild(frame);
                          if (childId) updateFrame(frame.id, { childNodeId });
                        }}
                        className="nodrag inline-flex h-6 w-6 items-center justify-center rounded-md bg-white/[0.06] text-text-muted transition-colors hover:bg-white/[0.12] hover:text-text-dark"
                      >
                        <Share2 className="h-3 w-3" />
                      </button>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {!isBatchRunning && batchError && (
          <div className="nodrag pointer-events-none absolute inset-x-4 bottom-2 z-10 flex items-center justify-center gap-1.5 text-[11px] font-medium text-red-200">
            <AlertTriangle className="h-3.5 w-3.5 shrink-0 text-red-300/90" />
            <span className="truncate">{batchError}</span>
          </div>
        )}
      </div>

      {selected && nsfwEnabled && (
        <div
          className={`nodrag absolute left-1/2 z-10 flex -translate-x-1/2 flex-col gap-2 rounded-[var(--node-radius)] p-3 ${CANVAS_NODE_OPS_PANEL_CLASS}`}
          style={{
            top: `calc(100% + ${OPERATIONS_PANEL_GAP}px)`,
            height: OPERATIONS_PANEL_HEIGHT,
            width: Math.max(DEFAULT_WIDTH, 540),
          }}
          onClick={(event) => event.stopPropagation()}
        >
          <div className="flex items-center gap-2">
            <div className="min-w-0 flex-1">
              <ModelNamePicker
                value={checkpoint}
                onChange={(next) => updateNodeData(id, { checkpoint: next })}
                expectedTypes={['checkpoints']}
                ariaLabel="R18 底模（首帧）"
                getOptionDisabledReason={(entry) =>
                  entry.sdxl_incompatible
                    ? (entry.sdxl_incompatible_reason ?? '不兼容 SDXL 工作流')
                    : null
                }
              />
            </div>
            {checkpoint.trim().length === 0 && (
              <span className="shrink-0 text-[11px] leading-4 text-amber-300/85">← 先选底模</span>
            )}
          </div>

          <div className="flex items-center gap-1">
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
            <span className="ml-auto text-[10.5px] text-text-muted/70">
              首帧尺寸（建议跟随剧本画幅）
            </span>
          </div>

          <div className="flex items-center justify-between gap-2">
            <button
              type="button"
              disabled={isUploading}
              title="上传定妆照（IPAdapter 锚定全部首帧）"
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
              <span>{anchorUploadUrl ? '换定妆照' : '上传定妆照'}</span>
            </button>
            <span className="min-w-0 flex-1 truncate text-[10.5px] text-text-muted/70">
              {anchorImageUrl
                ? anchorUploadUrl
                  ? '锚定：本节点上传的定妆照'
                  : '锚定：上游连线第一张图'
                : '无定妆照时纯文生图（角色一致性弱）'}
            </span>
            <button
              type="button"
              disabled={batchDisabled}
              title={
                total === 0
                  ? '先连接 R18 剧本节点'
                  : checkpoint.trim().length === 0
                    ? '先选择底模'
                    : doneCount === total
                      ? '全部首帧已生成'
                      : `批量生成剩余 ${total - doneCount} 帧首帧`
              }
              onClick={(event) => {
                event.stopPropagation();
                void handleBatch();
              }}
              className={`${NODE_GENERATE_BUTTON_BASE_CLASS} ${
                batchDisabled
                  ? NODE_GENERATE_BUTTON_DISABLED_CLASS
                  : NODE_GENERATE_BUTTON_ENABLED_CLASS
              }`}
            >
              {isBatchRunning ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Flame className="h-4 w-4" />
              )}
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

NSFWStoryboardNode.displayName = 'NSFWStoryboardNode';
