// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
/**
 * R18 制作工厂 · 工序 6 音频制作 —— 对分镜表 audio=tts 且有台词/旁白的镜头
 * 逐句配音（r18-tts，emotion 取分镜行、source 按对白/旁白自动路由），
 * 已有 ttsUrls 的镜号跳过（断点续跑）；另配置 BGM 音轨 url 与混音音量
 * （供工序⑦合成消费）。允许镜头工序直连合成时本工序可跳过。
 */
import { memo, useCallback, useEffect, useMemo } from 'react';
import type { NodeProps } from '@xyflow/react';
import { AlertTriangle, Flame, Loader2, Music } from 'lucide-react';

import {
  CANVAS_NODE_TYPES,
  type NSFWFactoryAudioNodeData,
  type NSFWFactoryShotNodeData,
  type NSFWFactoryStoryboardNodeData,
} from '@/features/canvas/domain/canvasNodes';
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
import { gatewayErrorMessage, R18_TTS_VOICE_OPTIONS, useR18Tts } from '@/lib/queries/model-library';
import {
  FACTORY_NODE_W,
  NSFWFactoryShell,
  useFactoryUpstream,
} from './nsfwFactoryShared';

type NSFWFactoryAudioNodeProps = NodeProps & {
  id: string;
  data: NSFWFactoryAudioNodeData;
  selected?: boolean;
};

const NODE_W = FACTORY_NODE_W;
const NODE_H = 460;
const PANEL_HEIGHT = 240;
const PANEL_GAP = 12;

export const NSFWFactoryAudioNode = memo(({ id, data, selected }: NSFWFactoryAudioNodeProps) => {
  const updateNodeData = useCanvasStore((state) => state.updateNodeData);

  const shot = useFactoryUpstream<NSFWFactoryShotNodeData>(id, CANVAS_NODE_TYPES.nsfwFactoryShot);
  const storyboard = useFactoryUpstream<NSFWFactoryStoryboardNodeData>(
    id,
    CANVAS_NODE_TYPES.nsfwFactoryStoryboard,
  );
  const synthesizeTts = useR18Tts();

  const rows = storyboard?.rows ?? [];
  const shotOutputs = shot?.outputs ?? {};
  const voice = data.voice || R18_TTS_VOICE_OPTIONS[0].value;
  const ttsUrls = data.ttsUrls ?? {};
  const bgmUrl = data.bgmUrl ?? '';
  const bgmVolume = typeof data.bgmVolume === 'number' ? data.bgmVolume : 0.35;
  const envSfxUrl = data.envSfxUrl ?? '';
  const envSfxVolume = typeof data.envSfxVolume === 'number' ? data.envSfxVolume : 0.25;
  const isRunning = data.isRunning === true;
  const error = data.error ?? null;

  const latest = useCallback((): NSFWFactoryAudioNodeData => {
    const node = useCanvasStore.getState().nodes.find((n) => n.id === id);
    return (node?.data as NSFWFactoryAudioNodeData | undefined) ?? data;
  }, [data, id]);

  const ttsTargets = useMemo(
    () =>
      rows.filter(
        (row) => row.audio === 'tts' && (row.dialogue || row.narration || '').trim().length > 0,
      ),
    [rows],
  );

  const handleGenerateAll = useCallback(async () => {
    const d = latest();
    if (d.isRunning) return;
    if (ttsTargets.length === 0) {
      updateNodeData(id, { error: '分镜表无需要 TTS 配音的台词镜头' });
      return;
    }
    const projectId = readUrl().project;
    if (!projectId) {
      updateNodeData(id, { error: '缺少项目上下文（project 参数）' });
      return;
    }
    updateNodeData(id, { isRunning: true, error: null });
    try {
      for (const row of ttsTargets) {
        if (latest().ttsUrls[row.shotNo]) continue; // 已有配音跳过
        const result = await synthesizeTts.mutateAsync({
          text: (row.dialogue || row.narration || '').trim(),
          voice: d.voice,
          emotion: row.emotion || '平静',
          source: row.dialogue?.trim() ? 'dialogue' : 'narration',
          project_id: projectId,
        });
        const url = result.ok ? (result.data.url ?? '') : '';
        if (!url) throw new Error(`镜头 S${row.shotNo} 配音失败`);
        const cur = latest();
        updateNodeData(id, { ttsUrls: { ...cur.ttsUrls, [row.shotNo]: url } });
      }
    } catch (e) {
      updateNodeData(id, { error: gatewayErrorMessage(e, '配音中断') });
    } finally {
      updateNodeData(id, { isRunning: false });
    }
  }, [id, latest, synthesizeTts, ttsTargets, updateNodeData]);

  // 挂载时检测中断（刷新恢复）：isRunning 持久化为 true 说明上次没跑完
  useEffect(() => {
    if (data.isRunning === true) {
      updateNodeData(id, { isRunning: false });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const doneCount = ttsTargets.filter((row) => ttsUrls[row.shotNo]).length;
  const videoDoneCount = rows.filter((row) => shotOutputs[row.shotNo]?.videoUrl).length;

  const opsPanel = (
    <div
      className={`nodrag absolute left-1/2 z-10 flex -translate-x-1/2 flex-col gap-2 rounded-[var(--node-radius)] p-3 ${CANVAS_NODE_OPS_PANEL_CLASS}`}
      style={{ top: `calc(100% + ${PANEL_GAP}px)`, height: PANEL_HEIGHT, width: Math.max(NODE_W, 540) }}
      onClick={(event) => event.stopPropagation()}
    >
      <div className="flex items-center gap-2">
        <select
          value={voice}
          disabled={isRunning}
          onChange={(event) => updateNodeData(id, { voice: event.target.value })}
          title="配音音色"
          className="nodrag h-8 min-w-0 flex-1 rounded-md border border-white/10 bg-white/[0.05] px-1.5 text-[11px] text-text-dark outline-none focus:border-white/25"
        >
          {R18_TTS_VOICE_OPTIONS.map((opt) => (
            <option key={opt.value} value={opt.value} className="bg-neutral-800">
              {opt.label}
            </option>
          ))}
        </select>
        <button
          type="button"
          disabled={isRunning || ttsTargets.length === 0}
          title={ttsTargets.length === 0 ? '分镜表无台词镜头' : '逐镜头生成全部 TTS 配音（已有跳过）'}
          onClick={(event) => {
            event.stopPropagation();
            void handleGenerateAll();
          }}
          className={`${NODE_GENERATE_BUTTON_BASE_CLASS} ${
            isRunning || ttsTargets.length === 0
              ? NODE_GENERATE_BUTTON_DISABLED_CLASS
              : NODE_GENERATE_BUTTON_ENABLED_CLASS
          }`}
        >
          {isRunning ? <Loader2 className="h-4 w-4 animate-spin" /> : <Music className="h-4 w-4" />}
        </button>
      </div>
      <div className="flex flex-col gap-1">
        <span className="text-[10px] text-text-muted/70">BGM 音轨 URL（合成时混音，可选）</span>
        <input
          value={bgmUrl}
          onChange={(event) => updateNodeData(id, { bgmUrl: event.target.value })}
          placeholder="如 /static/projects/xxx/bgm.mp3"
          className="nodrag h-8 w-full rounded-md border border-white/10 bg-white/[0.05] px-2 text-[11px] text-text-dark outline-none placeholder:text-text-muted/45 focus:border-white/25"
        />
      </div>
      <div className="flex items-center gap-2">
        <span className="shrink-0 text-[10.5px] text-text-muted/80">BGM 音量</span>
        <input
          type="range"
          min={0}
          max={1}
          step={0.05}
          value={bgmVolume}
          onChange={(event) => updateNodeData(id, { bgmVolume: Number(event.target.value) })}
          className="nodrag min-w-0 flex-1 accent-amber-400"
        />
        <span className="shrink-0 text-[10.5px] tabular-nums text-text-muted">
          {bgmVolume.toFixed(2)}
        </span>
      </div>
      <div className="flex flex-col gap-1">
        <span className="text-[10px] text-text-muted/70">环境音效 URL（雨声/街道等，合成时循环混音，可选）</span>
        <input
          value={envSfxUrl}
          onChange={(event) => updateNodeData(id, { envSfxUrl: event.target.value })}
          placeholder="如 /static/projects/xxx/rain.mp3"
          className="nodrag h-8 w-full rounded-md border border-white/10 bg-white/[0.05] px-2 text-[11px] text-text-dark outline-none placeholder:text-text-muted/45 focus:border-white/25"
        />
      </div>
      <div className="flex items-center gap-2">
        <span className="shrink-0 text-[10.5px] text-text-muted/80">环境音效音量</span>
        <input
          type="range"
          min={0}
          max={1}
          step={0.05}
          value={envSfxVolume}
          onChange={(event) => updateNodeData(id, { envSfxVolume: Number(event.target.value) })}
          className="nodrag min-w-0 flex-1 accent-amber-400"
        />
        <span className="shrink-0 text-[10.5px] tabular-nums text-text-muted">
          {envSfxVolume.toFixed(2)}
        </span>
      </div>
    </div>
  );

  return (
    <NSFWFactoryShell
      id={id}
      type={CANVAS_NODE_TYPES.nsfwFactoryAudio}
      data={data}
      width={NODE_W}
      height={NODE_H}
      stageNo={6}
      stageName="音频制作"
      selected={selected}
      opsPanel={opsPanel}
    >
      {rows.length === 0 ? (
        <div className="flex h-full w-full flex-col items-center justify-center gap-2 text-text-muted/55">
          <Flame className="h-8 w-8 text-amber-300/50" aria-hidden />
          <span className="px-6 text-center text-[12px] leading-5">
            工序⑥ 音频制作
            <br />
            连接工序⑤镜头视频（或工序③分镜表），选中节点批量生成配音并配置 BGM
          </span>
        </div>
      ) : (
        <div className="flex h-full w-full flex-col overflow-hidden px-3 pb-2 pt-7">
          <div className="mb-1.5 flex shrink-0 items-center gap-2 text-[11px] text-text-muted">
            {isRunning && <Loader2 className="h-3 w-3 animate-spin text-amber-300/80" />}
            <span className="tabular-nums">
              配音 {doneCount}/{ttsTargets.length} · 上游出片 {videoDoneCount}/{rows.length}
            </span>
          </div>
          <div className="min-h-0 flex-1 space-y-1 overflow-y-auto pr-0.5">
            {rows.map((row) => {
              const done = Boolean(ttsUrls[row.shotNo]);
              const isTts = row.audio === 'tts' && (row.dialogue || row.narration || '').trim().length > 0;
              return (
                <div
                  key={row.shotNo}
                  className="flex items-center gap-1.5 rounded-md border border-white/[0.07] bg-white/[0.035] px-2 py-1 text-[10.5px]"
                >
                  <span className="shrink-0 tabular-nums text-text-muted">#{row.shotNo}</span>
                  <span
                    className="min-w-0 flex-1 truncate text-text-dark/80"
                    title={row.dialogue || row.narration}
                  >
                    {row.dialogue || row.narration || '（无台词）'}
                  </span>
                  {isTts ? (
                    <span
                      className={`shrink-0 rounded px-1.5 py-px text-[9.5px] ${
                        done ? 'bg-cyan-500/20 text-cyan-100/90' : 'bg-white/[0.06] text-text-muted/60'
                      }`}
                    >
                      {done ? '✓ 已配音' : '待配音'}
                    </span>
                  ) : (
                    <span className="shrink-0 rounded bg-white/[0.04] px-1.5 py-px text-[9.5px] text-text-muted/55">
                      {row.audio === 'native' ? '原生音画' : '无音轨'}
                    </span>
                  )}
                </div>
              );
            })}
          </div>
          <div className="mt-1.5 flex shrink-0 items-center gap-1.5 border-t border-white/[0.07] pt-1.5 text-[10px]">
            <Music className="h-3 w-3 text-text-muted/70" />
            <span className="truncate text-text-muted/80" title={bgmUrl}>
              {bgmUrl ? `BGM 已配置 · 音量 ${bgmVolume.toFixed(2)}` : 'BGM 未配置（合成时无背景乐）'}
              {envSfxUrl ? ` · 环境音效 ${envSfxVolume.toFixed(2)}` : ''}
            </span>
          </div>
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

NSFWFactoryAudioNode.displayName = 'NSFWFactoryAudioNode';
