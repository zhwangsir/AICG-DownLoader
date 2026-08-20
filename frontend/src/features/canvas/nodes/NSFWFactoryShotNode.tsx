// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
/**
 * R18 制作工厂 · 工序 5 镜头视频 —— 消费已确认的分镜表逐镜拍摄：
 * 每镜先生成首帧（reference_url 取说话角色的资产图，缺省画风锚定图），
 * 再 I2V 出片（action 用其预设，plot/portrait 走 h3-clean；wan22 前缀路由
 * wan，尺寸经 shotVideoSize 对齐）。outputs 已有的镜号跳过（断点续跑），
 * 刷新中断后挂载检测置 interrupted 显示「继续拍摄」。
 */
import { memo, useCallback, useEffect, useMemo } from 'react';
import type { NodeProps } from '@xyflow/react';
import { AlertTriangle, Clapperboard, Flame, Loader2, RotateCcw } from 'lucide-react';

import {
  CANVAS_NODE_TYPES,
  type NSFWFactoryAssetNodeData,
  type NSFWFactoryInitNodeData,
  type NSFWFactoryShotNodeData,
  type NSFWFactoryStoryboardNodeData,
} from '@/features/canvas/domain/canvasNodes';
import { resolveImageDisplayUrl } from '@/features/canvas/application/imageData';
import { useCanvasStore } from '@/stores/canvasStore';
import { readUrl } from '@/lib/url-params';
import { gatewayErrorMessage, useGenerateImage, useGenerateVideo } from '@/lib/queries/model-library';
import {
  shotLengthFrames,
  shotVideoSize,
} from '@/features/canvas/nodes/NSFWVideoBatchNode';
import {
  FACTORY_NODE_W,
  NSFWFactoryShell,
  factoryToAbsoluteUrl,
  matchDialogueSpeaker,
  useFactoryUpstream,
} from './nsfwFactoryShared';

type NSFWFactoryShotNodeProps = NodeProps & {
  id: string;
  data: NSFWFactoryShotNodeData;
  selected?: boolean;
};

const NODE_W = FACTORY_NODE_W;
const NODE_H = 460;
const PLOT_PRESET = 'h3-clean';

export const NSFWFactoryShotNode = memo(({ id, data, selected }: NSFWFactoryShotNodeProps) => {
  const updateNodeData = useCanvasStore((state) => state.updateNodeData);

  const storyboard = useFactoryUpstream<NSFWFactoryStoryboardNodeData>(
    id,
    CANVAS_NODE_TYPES.nsfwFactoryStoryboard,
  );
  const init = useFactoryUpstream<NSFWFactoryInitNodeData>(id, CANVAS_NODE_TYPES.nsfwFactoryInit);
  const asset = useFactoryUpstream<NSFWFactoryAssetNodeData>(id, CANVAS_NODE_TYPES.nsfwFactoryAsset);
  const generateImage = useGenerateImage();
  const generateVideo = useGenerateVideo();

  const rows = useMemo(() => storyboard?.rows ?? [], [storyboard]);
  const confirmed = storyboard?.confirmed === true;
  const outputs = data.outputs ?? {};
  const isRunning = data.isRunning === true;
  const error = data.error ?? null;
  const interrupted = data.interrupted === true;

  const latest = useCallback((): NSFWFactoryShotNodeData => {
    const node = useCanvasStore.getState().nodes.find((n) => n.id === id);
    return (node?.data as NSFWFactoryShotNodeData | undefined) ?? data;
  }, [data, id]);

  const handleRun = useCallback(async () => {
    const d = latest();
    if (d.isRunning) return;
    if (!confirmed) {
      updateNodeData(id, { error: '请先在工序③分镜表「确认分镜」' });
      return;
    }
    if (rows.length === 0) {
      updateNodeData(id, { error: '上游分镜表为空' });
      return;
    }
    const projectId = readUrl().project;
    if (!projectId) {
      updateNodeData(id, { error: '缺少项目上下文（project 参数）' });
      return;
    }
    if (!init?.checkpoint?.trim()) {
      updateNodeData(id, { error: '上游工序①未选择底模' });
      return;
    }
    const characterAssets = new Map<string, string>();
    for (const it of asset?.items ?? []) {
      if (it.kind === 'character' && it.imageUrl) characterAssets.set(it.name, it.imageUrl);
    }
    const anchorUrl = asset?.styleAnchorUrl ?? null;
    updateNodeData(id, { isRunning: true, error: null, interrupted: false });
    try {
      for (const row of rows) {
        if (latest().outputs[row.shotNo]?.videoUrl) continue; // 断点续跑
        // 1) 首帧：优先说话角色资产图锚定，缺省画风锚定图
        const speaker = matchDialogueSpeaker(row.dialogue);
        const refUrl = (speaker ? characterAssets.get(speaker) : undefined) || anchorUrl;
        const frameResult = await generateImage.mutateAsync({
          prompt: row.imagePrompt,
          checkpoint: init.checkpoint.trim(),
          size: init.size ?? '832x1216',
          project_id: projectId,
          ...(refUrl ? { reference_url: factoryToAbsoluteUrl(refUrl) } : {}),
        });
        const frameUrl = frameResult.ok ? (frameResult.data.url ?? '') : '';
        if (!frameUrl) throw new Error(`镜头 S${row.shotNo} 首帧生成返回为空`);
        // 2) 视频：action 用其预设，其余 h3-clean；wan22 前缀路由 wan
        const presetId = row.kind === 'action' && row.presetId ? row.presetId : PLOT_PRESET;
        const route: 'wan' | 'h3' = presetId.startsWith('wan22') ? 'wan' : 'h3';
        const { width, height } = shotVideoSize(init.size ?? '832x1216', route);
        const videoResult = await generateVideo.mutateAsync({
          preset_id: presetId,
          prompt:
            row.videoPrompt?.trim() ||
            'subtle motion, gentle breathing, slow camera push in, cinematic',
          first_frame_url: factoryToAbsoluteUrl(frameUrl),
          width,
          height,
          length: shotLengthFrames(row.durationSec || 5, route),
          project_id: projectId,
        });
        const videoUrl = videoResult.ok ? (videoResult.data.url ?? '') : '';
        if (!videoUrl) throw new Error(`镜头 S${row.shotNo} 视频生成失败`);
        const cur = latest();
        updateNodeData(id, {
          outputs: { ...cur.outputs, [row.shotNo]: { frameUrl, videoUrl } },
        });
      }
    } catch (e) {
      updateNodeData(id, { error: gatewayErrorMessage(e, '拍摄中断') });
    } finally {
      updateNodeData(id, { isRunning: false });
    }
  }, [asset, confirmed, generateImage, generateVideo, id, init, latest, rows, updateNodeData]);

  // 挂载时检测中断（刷新恢复）：isRunning 持久化为 true 说明上次没跑完
  useEffect(() => {
    if (data.isRunning === true) {
      updateNodeData(id, { isRunning: false, interrupted: true });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const total = rows.length;
  const done = rows.filter((row) => outputs[row.shotNo]?.videoUrl).length;
  const progressPct = total === 0 ? 0 : Math.round((done / total) * 100);

  return (
    <NSFWFactoryShell
      id={id}
      type={CANVAS_NODE_TYPES.nsfwFactoryShot}
      data={data}
      width={NODE_W}
      height={NODE_H}
      stageNo={5}
      stageName="镜头视频"
      selected={selected}
    >
      {total === 0 ? (
        <div className="flex h-full w-full flex-col items-center justify-center gap-2 text-text-muted/55">
          <Flame className="h-8 w-8 text-amber-300/50" aria-hidden />
          <span className="px-6 text-center text-[12px] leading-5">
            工序⑤ 镜头视频
            <br />
            连接工序③分镜表（或工序④数字资产）并确认分镜后开拍
          </span>
        </div>
      ) : (
        <div className="flex h-full w-full flex-col overflow-hidden px-3 pb-2 pt-7">
          <div className="mb-1.5 flex shrink-0 items-center gap-2 text-[11px] text-text-muted">
            {isRunning && <Loader2 className="h-3 w-3 animate-spin text-amber-300/80" />}
            <span className="tabular-nums">
              镜头 {done}/{total}
            </span>
            {!confirmed && (
              <span className="rounded bg-amber-500/15 px-1.5 py-px text-[10px] text-amber-200/85">
                分镜未确认
              </span>
            )}
          </div>
          <div className="mb-2 h-1.5 shrink-0 overflow-hidden rounded-full bg-white/[0.06]">
            <div
              className="h-full rounded-full bg-amber-400/70 transition-all"
              style={{ width: `${progressPct}%` }}
            />
          </div>

          <div className="min-h-0 flex-1 space-y-1 overflow-y-auto pr-0.5">
            {rows.map((row) => {
              const out = outputs[row.shotNo];
              return (
                <div
                  key={row.shotNo}
                  className="flex items-center gap-2 rounded-md border border-white/[0.07] bg-white/[0.035] px-2 py-1"
                >
                  {out?.frameUrl ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img
                      src={resolveImageDisplayUrl(out.frameUrl)}
                      alt=""
                      className="h-9 w-14 shrink-0 rounded border border-white/10 object-cover"
                      draggable={false}
                    />
                  ) : (
                    <div className="flex h-9 w-14 shrink-0 items-center justify-center rounded border border-dashed border-white/12 text-[9px] text-text-muted/50">
                      S{row.shotNo}
                    </div>
                  )}
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-1 text-[10px] text-text-muted">
                      <span className="shrink-0 tabular-nums">#{row.shotNo}</span>
                      <span className="min-w-0 flex-1 truncate text-text-dark/80" title={row.dialogue || row.narration}>
                        {row.dialogue || row.narration || row.imagePrompt.slice(0, 40)}
                      </span>
                    </div>
                    <div className="mt-0.5 flex items-center gap-1.5 text-[9.5px]">
                      <span className={out?.videoUrl ? 'text-emerald-200/80' : 'text-text-muted/50'}>
                        {out?.videoUrl ? '✓ 已出片' : isRunning ? '排队/拍摄中' : '待拍摄'}
                      </span>
                      <span className="shrink-0 text-text-muted/50">
                        {row.kind === 'action' ? '动作' : row.kind === 'portrait' ? '定妆' : '剧情'} ·{' '}
                        {row.durationSec}s
                      </span>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>

          {(interrupted || error) && (
            <button
              type="button"
              onClick={(event) => {
                event.stopPropagation();
                void handleRun();
              }}
              className="nodrag mt-1.5 flex h-9 shrink-0 items-center justify-center gap-1.5 rounded-md bg-amber-400/20 text-[12px] font-semibold text-amber-100 transition-colors hover:bg-amber-400/30"
            >
              <RotateCcw className="h-3.5 w-3.5" />
              {error ? '重试（从失败镜头继续）' : '继续拍摄（断点接续）'}
            </button>
          )}
          {!interrupted && !error && (
            <button
              type="button"
              disabled={isRunning || !confirmed}
              title={
                !confirmed
                  ? '请先在工序③确认分镜'
                  : done > 0
                    ? '继续拍摄未完成镜头（已有产物跳过）'
                    : '逐镜头生成首帧 + 视频出片'
              }
              onClick={(event) => {
                event.stopPropagation();
                void handleRun();
              }}
              className="nodrag mt-1.5 flex h-9 shrink-0 items-center justify-center gap-1.5 rounded-md bg-amber-400/20 text-[12px] font-semibold text-amber-100 transition-colors hover:bg-amber-400/30 disabled:cursor-not-allowed disabled:opacity-45"
            >
              {isRunning ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Clapperboard className="h-3.5 w-3.5" />
              )}
              {done > 0 ? '继续拍摄（断点接续）' : '开拍'}
            </button>
          )}
          {error && (
            <div className="mt-1 shrink-0 truncate text-[10.5px] text-red-300/85" title={error}>
              <AlertTriangle className="mr-1 inline h-3 w-3" />
              {error}
            </div>
          )}
        </div>
      )}
    </NSFWFactoryShell>
  );
});

NSFWFactoryShotNode.displayName = 'NSFWFactoryShotNode';
