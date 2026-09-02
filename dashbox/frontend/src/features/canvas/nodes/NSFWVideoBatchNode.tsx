// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
/**
 * R18 出片节点 —— 消费上游「R18 分镜」frames（已生成首帧的镜头）一键出片：
 *
 * 1. TTS 阶段：audio=tts 镜头逐句调 CosyVoice2（r18-tts），mp3 spawn 音频子节点
 * 2. 视频阶段：逐镜头顺序生成（action→其预设；plot/portrait→h3-clean 无 LoRA
 *    预设，避免 HMNSFW 触发词污染剧情镜头），mp4 spawn 视频子节点
 * 3. 字幕轨：按剧本对白/旁白 + 镜头时长导出 SRT（合成时烧录用）
 *
 * 全程顺序派发（H3 单实例 + GPU0 多服务共存，并发会挤显存）；
 * 单镜头可重试；spawn 的视频/音频子节点直接连 videoCompose 合成。
 */
import { memo, useCallback, useEffect, useMemo } from 'react';
import {
  Handle,
  Position,
  useUpdateNodeInternals,
  type NodeProps,
} from '@xyflow/react';
import {
  AlertTriangle,
  Clapperboard,
  Download,
  Film,
  Flame,
  Loader2,
  Music,
  RefreshCw,
  ShieldAlert,
} from 'lucide-react';

import {
  CANVAS_NODE_TYPES,
  type NsfwVideoBatchShot,
  type NSFWVideoBatchNodeData,
  type NsfwStoryboardFrameItem,
} from '@/features/canvas/domain/canvasNodes';
import { resolveNodeDisplayName } from '@/features/canvas/domain/nodeDisplay';
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
} from '@/features/canvas/ui/nodeControlStyles';
import { useCanvasStore } from '@/stores/canvasStore';
import { readUrl } from '@/lib/url-params';
import {
  gatewayErrorMessage,
  R18_TTS_VOICE_OPTIONS,
  useGenerateVideo,
  useNsfwStatus,
  useR18Tts,
} from '@/lib/queries/model-library';
import { useUpstreamNodes } from '@/features/canvas/application/useUpstreamGraph';

type NSFWVideoBatchNodeProps = NodeProps & {
  id: string;
  data: NSFWVideoBatchNodeData;
  selected?: boolean;
};

const DEFAULT_WIDTH = 460;
const DEFAULT_HEIGHT = 420;
const OPERATIONS_PANEL_HEIGHT = 170;
const OPERATIONS_PANEL_GAP = 12;

/** plot/portrait 镜头的默认预设（无 LoRA 的 H3 音画链，后端 NSFW_VIDEO_PRESETS 同名项）。 */
const PLOT_PRESET = 'h3-clean';

const KIND_DOT_CLASS: Record<NsfwVideoBatchShot['kind'], string> = {
  plot: 'bg-slate-400/70',
  action: 'bg-rose-400/80',
  portrait: 'bg-amber-400/80',
};

function toAbsoluteUrl(url: string): string {
  if (/^https?:\/\//i.test(url) || url.startsWith('data:')) return url;
  return `${window.location.origin}${url.startsWith('/') ? '' : '/'}${url}`;
}

/** 镜头时长 → 帧数（wan 16fps / h3 ≈24.8fps，clamp 到 API 上限 241）。 */
export function shotLengthFrames(durationSec: number, route: 'wan' | 'h3'): number {
  const raw = route === 'wan' ? durationSec * 16 + 1 : durationSec * 24.8;
  return Math.min(241, Math.max(9, Math.round(raw)));
}

/** 视频尺寸：h3 需 32 对齐（首帧生成分辨率 832x1216 等本就满足）；
 *  wan 14B 限制最长边 832（等比缩放 + 16 对齐），否则慢到不可用。 */
export function shotVideoSize(size: string, route: 'wan' | 'h3'): { width: number; height: number } {
  const [w, h] = size.split('x').map((v) => Number.parseInt(v, 10) || 768);
  const snap = (v: number, step: number) => Math.max(240, Math.round(v / step) * step);
  if (route === 'h3') {
    if (h >= w) return { width: 768, height: 1344 };
    return { width: 1344, height: 768 };
  }
  const scale = Math.min(1, 832 / Math.max(w, h));
  return { width: snap(w * scale, 16), height: snap(h * scale, 16) };
}

function formatSrtTime(totalSec: number): string {
  const ms = Math.round(totalSec * 1000);
  const h = Math.floor(ms / 3_600_000);
  const m = Math.floor((ms % 3_600_000) / 60_000);
  const s = Math.floor((ms % 60_000) / 1000);
  const rest = ms % 1000;
  const pad = (v: number, len = 2) => String(v).padStart(len, '0');
  return `${pad(h)}:${pad(m)}:${pad(s)},${pad(rest, 3)}`;
}

/** 按镜头顺序时长累积生成 SRT 字幕文本（对白优先，旁白兜底）。 */
export function buildSrtContent(shots: NsfwVideoBatchShot[]): string {
  let cursor = 0;
  let index = 0;
  const blocks: string[] = [];
  for (const shot of shots) {
    const dur = shot.durationSec || 5;
    const text = (shot.dialogue || shot.narration || '').trim();
    if (text) {
      index += 1;
      blocks.push(
        `${index}\n${formatSrtTime(cursor)} --> ${formatSrtTime(cursor + dur)}\n${text}\n`,
      );
    }
    cursor += dur;
  }
  return blocks.join('\n');
}

export const NSFWVideoBatchNode = memo(({ id, data, selected }: NSFWVideoBatchNodeProps) => {
  const updateNodeInternals = useUpdateNodeInternals();
  const updateNodeData = useCanvasStore((state) => state.updateNodeData);
  const setSelectedNode = useCanvasStore((state) => state.setSelectedNode);

  const { data: nsfwStatusData, isLoading: nsfwLoading } = useNsfwStatus();
  const nsfwEnabled = nsfwStatusData?.data?.nsfw_enabled === true;

  const generateVideo = useGenerateVideo();
  const synthesizeTts = useR18Tts();
  const upstreamNodes = useUpstreamNodes(id);

  const resolvedTitle = useMemo(
    () => resolveNodeDisplayName(CANVAS_NODE_TYPES.nsfwVideoBatch, data),
    [data],
  );

  const voice = typeof data.voice === 'string' && data.voice ? data.voice : R18_TTS_VOICE_OPTIONS[0].value;
  const shots = Array.isArray(data.shots) ? data.shots : [];
  const isBatchRunning = data.isBatchRunning === true;
  const batchError =
    typeof data.batchError === 'string' && data.batchError.length > 0
      ? data.batchError
      : null;

  useEffect(() => {
    updateNodeInternals(id);
  }, [id, updateNodeInternals]);

  // ── 上游分镜帧 + 生成分辨率（视频尺寸继承首帧）──
  const upstream = useMemo(() => {
    for (const node of upstreamNodes) {
      if (node.type !== 'nsfwStoryboardNode') continue;
      const d = node.data as { frames?: NsfwStoryboardFrameItem[]; size?: string };
      if (Array.isArray(d.frames)) {
        return { frames: d.frames as NsfwStoryboardFrameItem[], size: typeof d.size === 'string' ? d.size : '832x1216' };
      }
    }
    return null;
  }, [upstreamNodes]);

  const framesSignature = useMemo(
    () =>
      (upstream?.frames ?? [])
        .map(
          (f) =>
            `${f.sceneNo}|${f.kind}|${f.videoPrompt}|${f.presetId}|${f.dialogue}|${f.narration}|${f.durationSec}|${f.audio}|${f.imageUrl ?? ''}`,
        )
        .join('\n'),
    [upstream],
  );

  useEffect(() => {
    if (!upstream || upstream.frames.length === 0) return;
    const prevById = new Map(shots.map((s) => [s.sceneNo, s] as const));
    const nextShots: NsfwVideoBatchShot[] = upstream.frames.map((frame) => {
      const prev = prevById.get(frame.sceneNo);
      const same =
        prev?.videoPrompt === frame.videoPrompt &&
        prev?.firstFrameUrl === frame.imageUrl &&
        prev?.durationSec === frame.durationSec &&
        prev?.audio === frame.audio;
      return {
        id: `r18-shot-${frame.sceneNo}`,
        sceneNo: frame.sceneNo,
        kind: frame.kind,
        title: frame.title ?? '',
        videoPrompt: frame.videoPrompt ?? '',
        presetId: frame.presetId ?? '',
        dialogue: frame.dialogue ?? '',
        narration: frame.narration ?? '',
        durationSec: frame.durationSec || 5,
        audio: frame.audio ?? 'tts',
        firstFrameUrl: frame.imageUrl,
        videoUrl: same ? prev.videoUrl : null,
        videoNodeId: same ? (prev.videoNodeId ?? null) : null,
        audioUrl: same ? prev.audioUrl : null,
        audioNodeId: same ? (prev.audioNodeId ?? null) : null,
        phase: same ? prev.phase : 'pending',
        error: same ? (prev.error ?? null) : null,
      };
    });
    const nextSig = nextShots
      .map((s) => `${s.sceneNo}|${s.videoPrompt}|${s.firstFrameUrl}|${s.durationSec}|${s.audio}`)
      .join('\n');
    const curSig = shots
      .map((s) => `${s.sceneNo}|${s.videoPrompt}|${s.firstFrameUrl}|${s.durationSec}|${s.audio}`)
      .join('\n');
    if (nextSig !== curSig) {
      updateNodeData(id, { shots: nextShots });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [framesSignature, id]);

  const videoDone = shots.filter((s) => s.videoUrl).length;
  const ttsDone = shots.filter((s) => s.audio !== 'tts' || s.audioUrl).length;
  const ttsTotal = shots.filter((s) => s.audio === 'tts').length;
  const total = shots.length;
  const readyShots = shots.filter((s) => s.firstFrameUrl);

  /** 从 store 读最新 shots 打补丁（长批次防闭包过期）。 */
  const updateShot = useCallback(
    (shotId: string, patch: Partial<NsfwVideoBatchShot>) => {
      const node = useCanvasStore.getState().nodes.find((n) => n.id === id);
      const current = (node?.data as NSFWVideoBatchNodeData | undefined)?.shots ?? [];
      updateNodeData(id, {
        shots: current.map((s) => (s.id === shotId ? { ...s, ...patch } : s)),
      });
    },
    [id, updateNodeData],
  );

  const spawnVideoChild = useCallback(
    (shot: NsfwVideoBatchShot, videoUrl: string): string | null => {
      const store = useCanvasStore.getState();
      const position = store.findNodePosition(id, 320, 240);
      const { width, height } = shotVideoSize(upstream?.size ?? '768x1344', 'h3');
      const childId = store.addNode(CANVAS_NODE_TYPES.video, position, {
        displayName: `R18 成片 S${shot.sceneNo}`,
        videoUrl,
        previewImageUrl: shot.firstFrameUrl,
        referenceOnly: true,
        aspectRatio: width > height ? '16:9' : '9:16',
        isSizeManuallyAdjusted: false,
      });
      if (childId) store.addEdge(id, childId);
      return childId;
    },
    [id, upstream],
  );

  const spawnAudioChild = useCallback(
    (shot: NsfwVideoBatchShot, audioUrl: string): string | null => {
      const store = useCanvasStore.getState();
      const position = store.findNodePosition(id, 260, 140);
      const childId = store.addNode(CANVAS_NODE_TYPES.audio, position, {
        displayName: `R18 配音 S${shot.sceneNo}`,
        audioUrl,
        sourceFileName: null,
        durationMs: null,
      });
      if (childId) store.addEdge(id, childId);
      return childId;
    },
    [id],
  );

  /** 单镜头 TTS（audio=tts 且未生成）。 */
  const runTtsForShot = useCallback(
    async (shot: NsfwVideoBatchShot) => {
      const projectId = readUrl().project;
      const text = (shot.dialogue || shot.narration || '').trim();
      if (!projectId || !text) return;
      updateShot(shot.id, { phase: 'tts', error: null });
      try {
        const result = await synthesizeTts.mutateAsync({
          text,
          voice,
          project_id: projectId,
        });
        const url = result.ok ? (result.data.url ?? '') : '';
        if (!url) {
          updateShot(shot.id, { phase: 'error', error: '配音返回为空' });
          return;
        }
        const audioNodeId = spawnAudioChild(shot, url);
        updateShot(shot.id, { audioUrl: url, audioNodeId: audioNodeId ?? null, phase: 'pending' });
      } catch (error) {
        updateShot(shot.id, { phase: 'error', error: gatewayErrorMessage(error, '配音失败') });
      }
    },
    [spawnAudioChild, synthesizeTts, updateShot, voice],
  );

  /** 单镜头视频（预设路由 + 首帧 I2V）。 */
  const runVideoForShot = useCallback(
    async (shot: NsfwVideoBatchShot) => {
      const projectId = readUrl().project;
      if (!projectId || !shot.firstFrameUrl) return;
      updateShot(shot.id, { phase: 'video', error: null });
      const rawPreset =
        shot.kind === 'action' && shot.presetId ? shot.presetId : PLOT_PRESET;
      const presetId = rawPreset.startsWith('wan22') ? 'h3-aio' : rawPreset;
      const { width, height } = shotVideoSize(upstream?.size ?? '768x1344', 'h3');
      // I2V 提示词只描述运动（对白交给 TTS 音轨，不写进 prompt——h3 音画
      // 会照 prompt 念词，写了就与配音轨双声重叠）；portrait 空运动词给缓动兜底
      const prompt =
        shot.videoPrompt.trim() ||
        'subtle motion, gentle breathing, slow camera push in, cinematic';
      try {
        const result = await generateVideo.mutateAsync({
          preset_id: presetId,
          prompt,
          first_frame_url: toAbsoluteUrl(shot.firstFrameUrl),
          width,
          height,
          length: shotLengthFrames(shot.durationSec, 'h3'),
          project_id: projectId,
        });
        const url = result.ok ? (result.data.url ?? '') : '';
        if (!url) {
          updateShot(shot.id, { phase: 'error', error: '视频返回为空' });
          return;
        }
        const videoNodeId = spawnVideoChild(shot, url);
        updateShot(shot.id, { videoUrl: url, videoNodeId: videoNodeId ?? null, phase: 'done' });
      } catch (error) {
        updateShot(shot.id, { phase: 'error', error: gatewayErrorMessage(error, '视频生成失败') });
      }
    },
    [generateVideo, spawnVideoChild, updateShot, upstream],
  );

  /** 批量：TTS 逐句 → 视频逐镜头（全顺序，GPU0 多服务共存不做并发）。 */
  const handleBatch = useCallback(async () => {
    if (isBatchRunning) return;
    const pendingTts = shots.filter(
      (s) => s.audio === 'tts' && !s.audioUrl && (s.dialogue || s.narration),
    );
    const pendingVideos = shots.filter((s) => s.firstFrameUrl && !s.videoUrl);
    if (pendingTts.length === 0 && pendingVideos.length === 0) return;
    updateNodeData(id, { isBatchRunning: true, batchError: null });
    try {
      for (const shot of pendingTts) {
        await runTtsForShot(shot);
      }
      for (const shot of pendingVideos) {
        await runVideoForShot(shot);
      }
    } finally {
      updateNodeData(id, { isBatchRunning: false });
    }
  }, [id, isBatchRunning, runTtsForShot, runVideoForShot, shots, updateNodeData]);

  /** 重试单镜头（缺啥补啥）。 */
  const handleRetryShot = useCallback(
    async (shot: NsfwVideoBatchShot) => {
      if (isBatchRunning) return;
      if (shot.audio === 'tts' && !shot.audioUrl && (shot.dialogue || shot.narration)) {
        await runTtsForShot(shot);
      }
      if (shot.firstFrameUrl && !shot.videoUrl) {
        await runVideoForShot(shot);
      }
    },
    [isBatchRunning, runTtsForShot, runVideoForShot],
  );

  const handleDownloadSrt = useCallback(() => {
    const content = buildSrtContent(shots);
    if (!content) {
      updateNodeData(id, { batchError: '剧本无对白/旁白，SRT 为空' });
      return;
    }
    const blob = new Blob([`\ufeff${content}`], { type: 'text/plain;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `R18-${resolvedTitle}-${Date.now()}.srt`;
    a.click();
    URL.revokeObjectURL(url);
  }, [id, resolvedTitle, shots, updateNodeData]);

  const batchDisabled =
    !nsfwEnabled ||
    isBatchRunning ||
    readyShots.length === 0 ||
    (videoDone === total && ttsDone === total);

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
            <Clapperboard className="h-8 w-8 text-amber-300/50" aria-hidden />
            <span className="px-6 text-center text-[12px] leading-5">
              R18 一键出片
              <br />
              连接上游「R18 分镜」节点（首帧生成完成后）在此批量出片
            </span>
          </div>
        )}

        {total > 0 && (
          <div className="flex h-full w-full flex-col overflow-hidden px-3 pb-2 pt-7">
            <div className="mb-1.5 flex shrink-0 items-center gap-2">
              <span className="text-[12px] font-medium text-text-dark">
                视频 {videoDone}/{total}
              </span>
              {ttsTotal > 0 && (
                <span className="text-[12px] font-medium text-text-dark">
                  · 配音 {ttsDone}/{total}
                </span>
              )}
              {isBatchRunning && <Loader2 className="h-3 w-3 animate-spin text-amber-300/80" />}
              <div className="h-1 min-w-0 flex-1 overflow-hidden rounded-full bg-white/[0.08]">
                <div
                  className="h-full rounded-full bg-amber-400/70 transition-all"
                  style={{ width: `${total > 0 ? (videoDone / total) * 100 : 0}%` }}
                />
              </div>
              <button
                type="button"
                title="导出 SRT 字幕（对白/旁白 + 镜头时长）"
                onClick={(event) => {
                  event.stopPropagation();
                  handleDownloadSrt();
                }}
                className="nodrag inline-flex h-6 items-center gap-1 rounded-md bg-white/[0.06] px-2 text-[10.5px] text-text-muted transition-colors hover:bg-white/[0.12] hover:text-text-dark"
              >
                <Download className="h-3 w-3" />
                SRT
              </button>
            </div>
            <div className="min-h-0 flex-1 space-y-1.5 overflow-y-auto pr-0.5">
              {shots.map((shot) => (
                <div
                  key={shot.id}
                  className="flex items-center gap-2 rounded-md border border-white/[0.07] bg-white/[0.035] px-2 py-1.5"
                >
                  <div className="flex h-9 w-14 shrink-0 items-center justify-center overflow-hidden rounded border border-white/10">
                    {shot.firstFrameUrl ? (
                      // eslint-disable-next-line @next/next/no-img-element
                      <img
                        src={shot.firstFrameUrl}
                        alt={`S${shot.sceneNo}`}
                        className="h-full w-full object-cover"
                        draggable={false}
                      />
                    ) : (
                      <span className="text-[10px] text-text-muted/50">缺首帧</span>
                    )}
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-1.5">
                      <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${KIND_DOT_CLASS[shot.kind] ?? 'bg-slate-400/70'}`} />
                      <span className="shrink-0 text-[10px] tabular-nums text-text-muted">
                        #{shot.sceneNo}
                      </span>
                      <span className="min-w-0 flex-1 truncate text-[11px] text-text-dark/85">
                        {shot.title || shot.videoPrompt || `镜头 ${shot.sceneNo}`}
                      </span>
                      <span className="shrink-0 text-[10px] tabular-nums text-text-muted">
                        {shot.durationSec}s
                      </span>
                    </div>
                    <div className="mt-0.5 flex items-center gap-1.5 text-[10px]">
                      {shot.kind === 'action' && shot.presetId ? (
                        <span className="shrink-0 truncate rounded bg-rose-500/15 px-1 py-px text-rose-200/90">
                          {shot.presetId}
                        </span>
                      ) : (
                        <span className="shrink-0 rounded bg-slate-500/20 px-1 py-px text-slate-200/85">
                          h3-clean
                        </span>
                      )}
                      {shot.phase === 'tts' || shot.phase === 'video' ? (
                        <span className="inline-flex shrink-0 items-center gap-0.5 text-amber-200/85">
                          <Loader2 className="h-2.5 w-2.5 animate-spin" />
                          {shot.phase === 'tts' ? '配音中' : '出片中'}
                        </span>
                      ) : shot.phase === 'error' ? (
                        <span
                          className="min-w-0 flex-1 truncate text-red-300/85"
                          title={shot.error ?? ''}
                        >
                          {shot.error}
                        </span>
                      ) : (
                        <span className="inline-flex min-w-0 flex-1 items-center gap-2 text-text-muted/75">
                          {shot.videoUrl && (
                            <span className="inline-flex items-center gap-0.5 text-emerald-200/80">
                              <Film className="h-2.5 w-2.5" />视频
                            </span>
                          )}
                          {shot.audioUrl && (
                            <span className="inline-flex items-center gap-0.5 text-cyan-200/80">
                              <Music className="h-2.5 w-2.5" />配音
                            </span>
                          )}
                          {shot.audio === 'native' && (
                            <span className="shrink-0 text-text-muted/55">原生音画</span>
                          )}
                          {shot.phase === 'pending' && !shot.videoUrl && (
                            <span className="text-text-muted/55">待出片</span>
                          )}
                        </span>
                      )}
                    </div>
                  </div>
                  <button
                    type="button"
                    title="重试该镜头"
                    disabled={isBatchRunning || (!shot.firstFrameUrl && !(shot.audio === 'tts'))}
                    onClick={(event) => {
                      event.stopPropagation();
                      void handleRetryShot(shot);
                    }}
                    className="nodrag inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-white/[0.06] text-text-muted transition-colors hover:bg-white/[0.12] hover:text-text-dark disabled:opacity-40"
                  >
                    <RefreshCw className="h-3 w-3" />
                  </button>
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
            <span className="shrink-0 text-[11px] text-text-muted">配音音色</span>
            <select
              value={voice}
              disabled={isBatchRunning}
              onChange={(event) => updateNodeData(id, { voice: event.target.value })}
              className="h-7 min-w-0 flex-1 rounded-md border border-white/10 bg-white/[0.05] px-2 text-xs text-text-dark outline-none focus:border-white/25"
            >
              {R18_TTS_VOICE_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value} className="bg-neutral-800">
                  {opt.label}
                </option>
              ))}
            </select>
          </div>
          <div className="flex items-center justify-between gap-2">
            <span className="text-[10.5px] leading-4 text-text-muted/70">
              顺序出片：每镜头约 3~5 分钟（{readyShots.length} 镜就绪
              {readyShots.length < total ? `，${total - readyShots.length} 镜缺首帧` : ''}）
              <br />
              剧情/定妆镜头走 h3-clean（无 NSFW LoRA）；完成后子节点直连「视频合成」
            </span>
            <button
              type="button"
              disabled={batchDisabled}
              title={
                readyShots.length === 0
                  ? '上游分镜还没有已生成的首帧'
                  : batchDisabled
                    ? '全部镜头已出片'
                    : `批量出片（配音 ${ttsDone}/${total} + 视频 ${videoDone}/${total}）`
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
                <Clapperboard className="h-4 w-4" />
              )}
            </button>
          </div>
        </div>
      )}
    </div>
  );
});

NSFWVideoBatchNode.displayName = 'NSFWVideoBatchNode';
