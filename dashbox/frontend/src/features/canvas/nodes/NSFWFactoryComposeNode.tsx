// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
/**
 * R18 制作工厂 · 工序 7 后期合成 —— 消费上游镜头 outputs（可选工序⑥音频的
 * ttsUrls/BGM）：调色、转场、片头/片尾卡、字幕烧录（逐镜头文本交后端按
 * 真实时长重建 SRT，与 TTS 同一时间轴）、BGM 混音。shots 只取已出片镜头，
 * audio_mode 按行策略（native 优先，有 tts_url 走 tts，否则 none）。
 * 产物 composeUrl + 时长 + 回传 SRT 持久化在 node.data（SRT 供工序⑧QC）。
 */
import { memo, useCallback, useEffect, useMemo } from 'react';
import type { NodeProps } from '@xyflow/react';
import { AlertTriangle, Clapperboard, Flame, Loader2 } from 'lucide-react';

import {
  CANVAS_NODE_TYPES,
  type NSFWFactoryAudioNodeData,
  type NSFWFactoryComposeNodeData,
  type NSFWFactoryScriptNodeData,
  type NSFWFactoryShotNodeData,
  type NSFWFactoryStoryboardNodeData,
} from '@/features/canvas/domain/canvasNodes';
import { resolveImageDisplayUrl } from '@/features/canvas/application/imageData';
import {
  CANVAS_NODE_OPS_PANEL_CLASS,
} from '@/features/canvas/ui/nodeFrameStyles';
import {
  NODE_GENERATE_BUTTON_BASE_CLASS,
  NODE_GENERATE_BUTTON_DISABLED_CLASS,
  NODE_GENERATE_BUTTON_ENABLED_CLASS,
} from '@/features/canvas/ui/nodeControlStyles';
import { useCanvasStore } from '@/stores/canvasStore';
import { readUrl } from '@/lib/url-params';
import { gatewayErrorMessage, useR18Compose } from '@/lib/queries/model-library';
import {
  FACTORY_CHIP_OFF_CLASS,
  FACTORY_CHIP_ON_CLASS,
  FACTORY_COLOR_PROFILES,
  FACTORY_NODE_W,
  FACTORY_TRANSITIONS,
  NSFWFactoryShell,
  useFactoryUpstream,
} from './nsfwFactoryShared';

type NSFWFactoryComposeNodeProps = NodeProps & {
  id: string;
  data: NSFWFactoryComposeNodeData;
  selected?: boolean;
};

const NODE_W = FACTORY_NODE_W;
const NODE_H = 460;
const PANEL_HEIGHT = 280;
const PANEL_GAP = 12;

export const NSFWFactoryComposeNode = memo(({ id, data, selected }: NSFWFactoryComposeNodeProps) => {
  const updateNodeData = useCanvasStore((state) => state.updateNodeData);

  const shot = useFactoryUpstream<NSFWFactoryShotNodeData>(id, CANVAS_NODE_TYPES.nsfwFactoryShot);
  const audio = useFactoryUpstream<NSFWFactoryAudioNodeData>(id, CANVAS_NODE_TYPES.nsfwFactoryAudio);
  const storyboard = useFactoryUpstream<NSFWFactoryStoryboardNodeData>(
    id,
    CANVAS_NODE_TYPES.nsfwFactoryStoryboard,
  );
  const script = useFactoryUpstream<NSFWFactoryScriptNodeData>(
    id,
    CANVAS_NODE_TYPES.nsfwFactoryScript,
  );
  const composeFinal = useR18Compose();

  const rows = useMemo(() => storyboard?.rows ?? [], [storyboard]);
  const shotOutputs = shot?.outputs ?? {};
  const composeUrl = data.composeUrl ?? null;
  const isRunning = data.isRunning === true;
  const error = data.error ?? null;

  const colorProfile = data.colorProfile ?? 'none';
  const transition = data.transition ?? 'fade';
  const openingText = data.openingText ?? '';
  const closingText = data.closingText ?? '';
  const burnSubtitle = data.burnSubtitle !== false;

  const readyRows = useMemo(
    () => rows.filter((row) => shotOutputs[row.shotNo]?.videoUrl),
    [rows, shotOutputs],
  );

  const latest = useCallback((): NSFWFactoryComposeNodeData => {
    const node = useCanvasStore.getState().nodes.find((n) => n.id === id);
    return (node?.data as NSFWFactoryComposeNodeData | undefined) ?? data;
  }, [data, id]);

  const handleCompose = useCallback(async () => {
    const d = latest();
    if (d.isRunning) return;
    const projectId = readUrl().project;
    if (!projectId) {
      updateNodeData(id, { error: '缺少项目上下文（project 参数）' });
      return;
    }
    if (readyRows.length === 0) {
      updateNodeData(id, { error: '没有任何已完成的镜头视频，无法合成' });
      return;
    }
    const ttsUrls = audio?.ttsUrls ?? {};
    const bgmUrl = (audio?.bgmUrl ?? '').trim();
    updateNodeData(id, { isRunning: true, error: null });
    try {
      const result = await composeFinal.mutateAsync({
        project_id: projectId,
        ...(script?.planTitle?.trim() ? { title: script.planTitle.trim() } : {}),
        shots: readyRows.map((row) => {
          const tts = ttsUrls[row.shotNo];
          return {
            video_url: shotOutputs[row.shotNo].videoUrl,
            ...(tts ? { tts_url: tts } : {}),
            audio_mode: row.audio === 'native' ? 'native' : tts ? 'tts' : 'none',
          };
        }),
        // 逐镜头字幕文本：后端按真实时长+片头卡+xfade 重叠重建 SRT（与 TTS 同一时间轴）
        ...(burnSubtitle
          ? { subtitles: readyRows.map((row) => (row.dialogue || row.narration || '').trim()) }
          : {}),
        ...(bgmUrl
          ? { bgm_url: bgmUrl, bgm_volume: audio?.bgmVolume ?? 0.35 }
          : {}),
        ...((audio?.envSfxUrl ?? '').trim()
          ? { sfx_url: (audio?.envSfxUrl ?? '').trim(), sfx_volume: audio?.envSfxVolume ?? 0.25 }
          : {}),
        color_profile: colorProfile,
        transition: d.transition ?? 'fade',
        transition_sec: 0.5,
        ...(openingText.trim() ? { opening: { text: openingText.trim(), duration_sec: 2 } } : {}),
        ...(closingText.trim() ? { closing: { text: closingText.trim(), duration_sec: 2 } } : {}),
      });
      const out = result.ok ? result.data : null;
      if (!out?.url) throw new Error('合成返回为空');
      updateNodeData(id, {
        composeUrl: out.url,
        composeDurationSec: out.duration_sec ?? null,
        srt: out.srt ?? '',
        isRunning: false,
      });
    } catch (e) {
      updateNodeData(id, { isRunning: false, error: gatewayErrorMessage(e, '合成失败') });
    }
  }, [
    audio,
    burnSubtitle,
    closingText,
    colorProfile,
    composeFinal,
    id,
    latest,
    openingText,
    readyRows,
    rows,
    script,
    shotOutputs,
    updateNodeData,
  ]);

  // 挂载时检测中断（刷新恢复）
  useEffect(() => {
    if (data.isRunning === true) {
      updateNodeData(id, { isRunning: false });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const opsPanel = (
    <div
      className={`nodrag absolute left-1/2 z-10 flex -translate-x-1/2 flex-col gap-2 overflow-hidden rounded-[var(--node-radius)] p-3 ${CANVAS_NODE_OPS_PANEL_CLASS}`}
      style={{ top: `calc(100% + ${PANEL_GAP}px)`, height: PANEL_HEIGHT, width: Math.max(NODE_W, 540) }}
      onClick={(event) => event.stopPropagation()}
    >
      <div className="flex items-center gap-1">
        <span className="shrink-0 text-[10px] text-text-muted/70">调色</span>
        {FACTORY_COLOR_PROFILES.map((opt) => (
          <button
            key={opt.value}
            type="button"
            onClick={() => updateNodeData(id, { colorProfile: opt.value })}
            className={colorProfile === opt.value ? FACTORY_CHIP_ON_CLASS : FACTORY_CHIP_OFF_CLASS}
          >
            {opt.label}
          </button>
        ))}
        <span className="mx-1 h-4 w-px shrink-0 bg-white/10" />
        <span className="shrink-0 text-[10px] text-text-muted/70">转场</span>
        {FACTORY_TRANSITIONS.map((opt) => (
          <button
            key={opt.value}
            type="button"
            onClick={() => updateNodeData(id, { transition: opt.value })}
            className={transition === opt.value ? FACTORY_CHIP_ON_CLASS : FACTORY_CHIP_OFF_CLASS}
          >
            {opt.label}
          </button>
        ))}
      </div>
      <div className="flex gap-2">
        <input
          value={openingText}
          onChange={(event) => updateNodeData(id, { openingText: event.target.value })}
          placeholder="片头卡文字（可选，2s）"
          className="nodrag h-8 min-w-0 flex-1 rounded-md border border-white/10 bg-white/[0.05] px-2 text-[11px] text-text-dark outline-none placeholder:text-text-muted/45 focus:border-white/25"
        />
        <input
          value={closingText}
          onChange={(event) => updateNodeData(id, { closingText: event.target.value })}
          placeholder="片尾卡文字（可选，2s）"
          className="nodrag h-8 min-w-0 flex-1 rounded-md border border-white/10 bg-white/[0.05] px-2 text-[11px] text-text-dark outline-none placeholder:text-text-muted/45 focus:border-white/25"
        />
      </div>
      <div className="flex flex-1 items-center justify-between gap-2">
        <label className="flex shrink-0 cursor-pointer items-center gap-1.5 text-[11px] text-text-muted/85" title="按分镜台词/旁白生成 SRT 并烧录进画面">
          <input
            type="checkbox"
            checked={burnSubtitle}
            onChange={(event) => updateNodeData(id, { burnSubtitle: event.target.checked })}
            className="h-3 w-3 accent-amber-400"
          />
          烧录字幕
        </label>
        <span className="min-w-0 flex-1 truncate text-[10px] text-text-muted/60">
          已出片 {readyRows.length}/{rows.length} 镜
          {audio?.bgmUrl ? ` · BGM 音量 ${(audio.bgmVolume ?? 0.35).toFixed(2)}` : ' · 无 BGM'}
          {(audio?.envSfxUrl ?? '').trim() ? ` · 环境音效 ${(audio?.envSfxVolume ?? 0.25).toFixed(2)}` : ''}
        </span>
        <button
          type="button"
          disabled={isRunning || readyRows.length === 0}
          title={readyRows.length === 0 ? '上游无已出片镜头' : '合成成片'}
          onClick={(event) => {
            event.stopPropagation();
            void handleCompose();
          }}
          className={`${NODE_GENERATE_BUTTON_BASE_CLASS} ${
            isRunning || readyRows.length === 0
              ? NODE_GENERATE_BUTTON_DISABLED_CLASS
              : NODE_GENERATE_BUTTON_ENABLED_CLASS
          }`}
        >
          {isRunning ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <Clapperboard className="h-4 w-4" />
          )}
        </button>
      </div>
    </div>
  );

  return (
    <NSFWFactoryShell
      id={id}
      type={CANVAS_NODE_TYPES.nsfwFactoryCompose}
      data={data}
      width={NODE_W}
      height={NODE_H}
      stageNo={7}
      stageName="后期合成"
      selected={selected}
      opsPanel={opsPanel}
    >
      {readyRows.length === 0 && !composeUrl ? (
        <div className="flex h-full w-full flex-col items-center justify-center gap-2 text-text-muted/55">
          <Flame className="h-8 w-8 text-amber-300/50" aria-hidden />
          <span className="px-6 text-center text-[12px] leading-5">
            工序⑦ 后期合成
            <br />
            连接工序⑤镜头视频（或⑥音频），出片完成后选中节点合成成片
          </span>
        </div>
      ) : (
        <div className="flex h-full w-full flex-col overflow-hidden px-3 pb-2 pt-7">
          <div className="mb-1.5 flex shrink-0 items-center gap-2 text-[11px] text-text-muted">
            {isRunning && <Loader2 className="h-3 w-3 animate-spin text-amber-300/80" />}
            <span className="tabular-nums">
              待合成镜头 {readyRows.length}/{rows.length}
            </span>
            {composeUrl && (
              <span className="rounded bg-emerald-400/15 px-1.5 py-px text-[10px] text-emerald-100/90">
                ✓ 已合成
              </span>
            )}
            <span className="truncate rounded bg-white/[0.06] px-1.5 py-px text-[10px]">
              {FACTORY_COLOR_PROFILES.find((c) => c.value === colorProfile)?.label} ·{' '}
              {FACTORY_TRANSITIONS.find((t) => t.value === transition)?.label}
              {burnSubtitle ? ' · 字幕' : ''}
            </span>
          </div>

          {composeUrl ? (
            <div className="min-h-0 flex-1 overflow-y-auto pr-0.5">
              <div className="overflow-hidden rounded-md border border-amber-400/25">
                {/* eslint-disable-next-line jsx-a11y/media-has-caption */}
                <video
                  src={composeUrl.startsWith('data:') ? composeUrl : resolveImageDisplayUrl(composeUrl)}
                  controls
                  preload="metadata"
                  className="max-h-64 w-full bg-black"
                />
                <div className="flex items-center justify-between px-2 py-1 text-[10px] text-text-muted">
                  <span className="truncate">
                    成片 · {data.composeDurationSec ? `${data.composeDurationSec}s` : ''}
                  </span>
                  <a
                    href={composeUrl.startsWith('data:') ? composeUrl : resolveImageDisplayUrl(composeUrl)}
                    download
                    className="text-amber-200/85 hover:text-amber-100"
                    onClick={(event) => event.stopPropagation()}
                  >
                    下载 mp4
                  </a>
                </div>
              </div>
            </div>
          ) : (
            <div className="flex min-h-0 flex-1 items-center justify-center text-[11px] text-text-muted/55">
              {readyRows.length} 个镜头已就绪，选中节点在下方面板点击「合成成片」
            </div>
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

NSFWFactoryComposeNode.displayName = 'NSFWFactoryComposeNode';
