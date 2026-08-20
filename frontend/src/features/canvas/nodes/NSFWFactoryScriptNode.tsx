// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
/**
 * R18 制作工厂 · 工序 2 剧本工程 —— 梗概 + 角色卡 → LLM 分集剧本（episodes）。
 * 规格参数（style_hint=themeNote / duration_sec / aspect）取自上游工序①立项定位；
 * 支持分集（episode_count 1/2/3），产物 episodes 持久化在 node.data，下游资产/
 * 分镜工序沿连线消费。多集时 LLM 逐集生成并保持上下文连贯。
 */
import { memo, useCallback } from 'react';
import type { NodeProps } from '@xyflow/react';
import { AlertTriangle, ArrowUp, Flame, Loader2 } from 'lucide-react';

import {
  CANVAS_NODE_TYPES,
  type NSFWFactoryInitNodeData,
  type NSFWFactoryScriptNodeData,
} from '@/features/canvas/domain/canvasNodes';
import { NodeGenerationOverlay } from '@/features/canvas/ui/NodeGenerationOverlay';
import {
  CANVAS_NODE_OPS_PANEL_CLASS,
} from '@/features/canvas/ui/nodeFrameStyles';
import {
  NODE_GENERATE_BUTTON_BASE_CLASS,
  NODE_GENERATE_BUTTON_DISABLED_CLASS,
  NODE_GENERATE_BUTTON_ENABLED_CLASS,
} from '@/features/canvas/ui/nodeControlStyles';
import { useCanvasStore } from '@/stores/canvasStore';
import { gatewayErrorMessage, useR18ScriptPlan } from '@/lib/queries/model-library';
import { parseCharactersText } from '@/features/canvas/nodes/NSFWScriptNode';
import {
  FACTORY_CHIP_OFF_CLASS,
  FACTORY_CHIP_ON_CLASS,
  FACTORY_NODE_W,
  NSFWFactoryShell,
  useFactoryUpstream,
} from './nsfwFactoryShared';

type NSFWFactoryScriptNodeProps = NodeProps & {
  id: string;
  data: NSFWFactoryScriptNodeData;
  selected?: boolean;
};

const NODE_W = FACTORY_NODE_W;
const NODE_H = 460;
const PANEL_HEIGHT = 300;
const PANEL_GAP = 12;
const EPISODE_COUNT_OPTIONS = [1, 2, 3] as const;

export const NSFWFactoryScriptNode = memo(({ id, data, selected }: NSFWFactoryScriptNodeProps) => {
  const updateNodeData = useCanvasStore((state) => state.updateNodeData);

  const init = useFactoryUpstream<NSFWFactoryInitNodeData>(id, CANVAS_NODE_TYPES.nsfwFactoryInit);
  const planScript = useR18ScriptPlan();

  const synopsis = data.synopsis ?? '';
  const charactersText = data.charactersText ?? '';
  const episodeCount = data.episodeCount ?? 1;
  const planTitle = data.planTitle ?? '';
  const episodes = Array.isArray(data.episodes) ? data.episodes : [];
  const isGenerating = data.isGenerating === true;
  const generationError =
    typeof data.generationError === 'string' && data.generationError.length > 0
      ? data.generationError
      : null;

  const durationSec = init?.durationSec ?? 90;
  const aspect = init?.aspect ?? '9:16';
  const themeNote = init?.themeNote ?? '';

  const submitDisabled = isGenerating || synopsis.trim().length === 0;

  const handleSubmit = useCallback(async () => {
    if (submitDisabled || isGenerating) return;
    updateNodeData(id, {
      isGenerating: true,
      generationStartedAt: Date.now(),
      generationError: null,
    });
    try {
      const result = await planScript.mutateAsync({
        synopsis: synopsis.trim(),
        characters: parseCharactersText(charactersText),
        ...(themeNote.trim() ? { style_hint: themeNote.trim() } : {}),
        duration_sec: durationSec,
        aspect,
        episode_count: episodeCount,
      });
      const plan = result.ok ? result.data : null;
      const nextEpisodes = plan?.episodes?.length
        ? plan.episodes.map((e, i) => ({
            episodeNo: e.episode_no ?? i + 1,
            title: e.title ?? `第${e.episode_no ?? i + 1}集`,
            scenes: Array.isArray(e.scenes) ? e.scenes : [],
          }))
        : plan && Array.isArray(plan.scenes) && plan.scenes.length > 0
          ? [{ episodeNo: plan.episode_no ?? 1, title: plan.title ?? '', scenes: plan.scenes }]
          : [];
      if (nextEpisodes.length === 0) {
        updateNodeData(id, {
          isGenerating: false,
          generationStartedAt: null,
          generationError: '剧本规划返回为空',
        });
        return;
      }
      updateNodeData(id, {
        planTitle: plan?.title ?? '',
        episodes: nextEpisodes,
        isGenerating: false,
        generationStartedAt: null,
        generationError: null,
      });
    } catch (error) {
      updateNodeData(id, {
        isGenerating: false,
        generationStartedAt: null,
        generationError: gatewayErrorMessage(error, '剧本规划失败'),
      });
    }
  }, [
    aspect,
    charactersText,
    durationSec,
    episodeCount,
    id,
    isGenerating,
    planScript,
    submitDisabled,
    synopsis,
    themeNote,
    updateNodeData,
  ]);

  const opsPanel = (
    <div
      className={`nodrag absolute left-1/2 z-10 flex -translate-x-1/2 flex-col gap-2 rounded-[var(--node-radius)] p-3 ${CANVAS_NODE_OPS_PANEL_CLASS}`}
      style={{
        top: `calc(100% + ${PANEL_GAP}px)`,
        height: PANEL_HEIGHT,
        width: Math.max(NODE_W, 540),
      }}
      onClick={(event) => event.stopPropagation()}
    >
      <textarea
        value={synopsis}
        onChange={(event) => updateNodeData(id, { synopsis: event.target.value })}
        placeholder="剧情梗概：场景、人物关系、想要的表现方向…（必填）"
        className="min-h-0 w-full flex-1 resize-none whitespace-pre-wrap break-words border-none bg-transparent px-1 py-1 text-sm leading-6 text-text-dark outline-none placeholder:text-text-muted/45"
      />
      <textarea
        value={charactersText}
        onChange={(event) => updateNodeData(id, { charactersText: event.target.value })}
        placeholder={'角色卡（可选，每行「名字：外貌/体型/服装描述」）\n林薇：28岁女性，黑色长直发，纤细体型，白色丝绸睡裙'}
        className="h-14 w-full resize-none rounded-md border border-white/10 bg-white/[0.05] px-2 py-1.5 text-xs leading-5 text-text-dark outline-none placeholder:text-text-muted/45 focus:border-white/25"
      />
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-1">
          <span className="shrink-0 text-[10.5px] text-text-muted/80">分集</span>
          {EPISODE_COUNT_OPTIONS.map((v) => (
            <button
              key={v}
              type="button"
              onClick={() => updateNodeData(id, { episodeCount: v })}
              className={episodeCount === v ? FACTORY_CHIP_ON_CLASS : FACTORY_CHIP_OFF_CLASS}
            >
              {v} 集
            </button>
          ))}
          <span className="ml-1 truncate text-[10px] text-text-muted/55">
            {init ? `规格 ${durationSec}s · ${aspect}` : '未连接工序①，用默认规格'}
          </span>
        </div>
        <button
          type="button"
          disabled={submitDisabled}
          title={synopsis.trim().length === 0 ? '先输入剧情梗概' : '生成分集剧本'}
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
          {isGenerating ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <ArrowUp className="h-4 w-4" />
          )}
        </button>
      </div>
    </div>
  );

  return (
    <NSFWFactoryShell
      id={id}
      type={CANVAS_NODE_TYPES.nsfwFactoryScript}
      data={data}
      width={NODE_W}
      height={NODE_H}
      stageNo={2}
      stageName="剧本工程"
      selected={selected}
      opsPanel={opsPanel}
    >
      {episodes.length === 0 && !isGenerating ? (
        <div className="flex h-full w-full flex-col items-center justify-center gap-2 text-text-muted/55">
          <Flame className="h-8 w-8 text-amber-300/50" aria-hidden />
          <span className="px-6 text-center text-[12px] leading-5">
            工序② 剧本工程
            <br />
            连接工序①立项定位，选中节点在下方面板输入梗概与角色卡生成分集剧本
          </span>
        </div>
      ) : (
        <div className="flex h-full w-full flex-col overflow-hidden px-3 pb-2 pt-7">
          <div className="mb-1.5 flex shrink-0 items-center gap-2">
            <span className="truncate text-[13px] font-semibold text-text-dark">
              {planTitle || '剧本'}
            </span>
            <span className="shrink-0 rounded bg-white/[0.08] px-1.5 py-0.5 text-[10px] tabular-nums text-text-muted">
              {episodes.length} 集 ·{' '}
              {episodes.reduce((sum, e) => sum + e.scenes.length, 0)} 镜头
            </span>
            {init && (
              <span className="shrink-0 truncate rounded bg-amber-500/15 px-1.5 py-0.5 text-[10px] text-amber-200/85">
                {init.theme || '未设题材'} · {init.durationSec}s · {init.aspect}
              </span>
            )}
          </div>
          <div className="min-h-0 flex-1 space-y-1.5 overflow-y-auto pr-0.5">
            {episodes.map((ep) => (
              <div
                key={ep.episodeNo}
                className="rounded-md border border-white/[0.07] bg-white/[0.035] px-2 py-1.5"
              >
                <div className="flex items-center gap-1.5">
                  <span className="shrink-0 rounded bg-amber-500/20 px-1.5 py-px text-[10px] font-medium text-amber-100">
                    第{ep.episodeNo}集
                  </span>
                  <span className="min-w-0 flex-1 truncate text-[12px] font-semibold text-text-dark">
                    {ep.title || `第${ep.episodeNo}集`}
                  </span>
                  <span className="shrink-0 text-[10px] tabular-nums text-text-muted">
                    {ep.scenes.length} 镜头 ·{' '}
                    {ep.scenes.reduce((sum, s) => sum + (s.duration_sec || 0), 0)}s
                  </span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {isGenerating && (
        <NodeGenerationOverlay
          startedAt={data.generationStartedAt ?? null}
          durationMs={300_000}
          hasBackground={false}
        />
      )}

      {!isGenerating && generationError && (
        <div className="nodrag absolute inset-x-5 top-1/2 z-10 flex -translate-y-1/2 flex-col items-center text-center">
          <div className="inline-flex items-center gap-1.5 text-[12px] font-semibold text-red-200">
            <AlertTriangle className="h-3.5 w-3.5 shrink-0 text-red-300/90" />
            <span>剧本规划失败</span>
          </div>
          <div
            className="mt-1 max-h-20 max-w-full overflow-y-auto break-words text-[11px] leading-4 text-red-100/76 [overflow-wrap:anywhere]"
            title={generationError}
          >
            {generationError}
          </div>
        </div>
      )}
    </NSFWFactoryShell>
  );
});

NSFWFactoryScriptNode.displayName = 'NSFWFactoryScriptNode';
