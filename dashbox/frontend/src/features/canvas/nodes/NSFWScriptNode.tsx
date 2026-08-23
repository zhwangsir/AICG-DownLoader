// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
/**
 * R18 剧本节点 —— 梗概 + 角色卡 → LLM（本地 uncensored，经 NewAPI/local_gateway）
 * 结构化分镜 scenes JSON：每个镜头带类型路由（plot 剧情 / action 成人动作 /
 * portrait 定妆特写）、预设 id、首帧图提示词（含触发词）、I2V 运动提示词、
 * 对白/旁白与时长。
 *
 * - 同步端点 /model-library/r18-script/plan（照 ai-staging-prop 先例）
 * - 产物 planResult 由下游「R18 分镜节点」经连线消费（useUpstreamNodes 直读）
 * - 菜单入口仅 R18 开启后出现；节点本体未开启时锁定（可连线、禁生成）
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
  ArrowUp,
  Flame,
  Loader2,
  ShieldAlert,
} from 'lucide-react';

import {
  CANVAS_NODE_TYPES,
  type NSFWScriptNodeData,
} from '@/features/canvas/domain/canvasNodes';
import { resolveNodeDisplayName } from '@/features/canvas/domain/nodeDisplay';
import { NodeHeader, NODE_HEADER_FLOATING_POSITION_CLASS } from '@/features/canvas/ui/NodeHeader';
import { NodeGenerationOverlay } from '@/features/canvas/ui/NodeGenerationOverlay';
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
import {
  gatewayErrorMessage,
  useNsfwStatus,
  useR18ScriptPlan,
  type R18SceneData,
} from '@/lib/queries/model-library';

type NSFWScriptNodeProps = NodeProps & {
  id: string;
  data: NSFWScriptNodeData;
  selected?: boolean;
};

const DEFAULT_WIDTH = 460;
const DEFAULT_HEIGHT = 420;
const OPERATIONS_PANEL_HEIGHT = 300;
const OPERATIONS_PANEL_GAP = 12;

const DURATION_OPTIONS: ReadonlyArray<{ value: number; label: string }> = [
  { value: 60, label: '60s' },
  { value: 90, label: '90s' },
  { value: 120, label: '2min' },
  { value: 180, label: '3min' },
];

const ASPECT_OPTIONS: ReadonlyArray<{ value: NSFWScriptNodeData['aspect']; label: string }> = [
  { value: '9:16', label: '竖 9:16' },
  { value: '16:9', label: '横 16:9' },
  { value: '1:1', label: '方 1:1' },
];

const KIND_BADGES: Record<R18SceneData['kind'], { label: string; className: string }> = {
  plot: { label: '剧情', className: 'bg-slate-500/25 text-slate-200' },
  action: { label: '动作', className: 'bg-rose-500/30 text-rose-100' },
  portrait: { label: '定妆', className: 'bg-amber-500/25 text-amber-100' },
};

/** 解析角色卡自由文本（每行「名字：描述」）→ 结构化 characters。 */
export function parseCharactersText(text: string): { name: string; description?: string }[] {
  return text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const sep = line.indexOf('：') >= 0 ? '：' : ':';
      const idx = line.indexOf(sep);
      if (idx <= 0) return { name: line, description: '' };
      return {
        name: line.slice(0, idx).trim(),
        description: line.slice(idx + 1).trim(),
      };
    })
    .filter((c) => c.name.length > 0);
}

export const NSFWScriptNode = memo(({ id, data, selected }: NSFWScriptNodeProps) => {
  const updateNodeInternals = useUpdateNodeInternals();
  const updateNodeData = useCanvasStore((state) => state.updateNodeData);
  const setSelectedNode = useCanvasStore((state) => state.setSelectedNode);

  const { data: nsfwStatusData, isLoading: nsfwLoading } = useNsfwStatus();
  const nsfwEnabled = nsfwStatusData?.data?.nsfw_enabled === true;

  const planScript = useR18ScriptPlan();

  const resolvedTitle = useMemo(
    () => resolveNodeDisplayName(CANVAS_NODE_TYPES.nsfwScript, data),
    [data],
  );

  const synopsis = typeof data.synopsis === 'string' ? data.synopsis : '';
  const charactersText = typeof data.charactersText === 'string' ? data.charactersText : '';
  const styleHint = typeof data.styleHint === 'string' ? data.styleHint : '';
  const durationSec = typeof data.durationSec === 'number' ? data.durationSec : 90;
  const aspect: NSFWScriptNodeData['aspect'] = data.aspect ?? '9:16';
  const planResult = data.planResult ?? null;
  const isGenerating = data.isGenerating === true;
  const generationError =
    typeof data.generationError === 'string' && data.generationError.length > 0
      ? data.generationError
      : null;

  useEffect(() => {
    updateNodeInternals(id);
  }, [id, updateNodeInternals]);

  const submitDisabled = !nsfwEnabled || isGenerating || synopsis.trim().length === 0;

  /** 编辑回写 scenes[i].image_prompt（分镜节点取的是同一份 planResult 引用，
   *  剧本节点是唯一权威编辑入口，改词后下游重新生成首帧即可）。 */
  const updateSceneField = useCallback(
    (sceneNo: number, field: 'image_prompt' | 'video_prompt', value: string) => {
      if (!planResult) return;
      const scenes = planResult.scenes.map((s) =>
        s.scene_no === sceneNo ? { ...s, [field]: value } : s,
      );
      updateNodeData(id, { planResult: { ...planResult, scenes } });
    },
    [id, planResult, updateNodeData],
  );

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
        ...(styleHint.trim() ? { style_hint: styleHint.trim() } : {}),
        duration_sec: durationSec,
        aspect,
      });
      const plan = result.ok ? result.data : null;
      if (plan && Array.isArray(plan.scenes) && plan.scenes.length > 0) {
        updateNodeData(id, {
          planResult: plan,
          isGenerating: false,
          generationStartedAt: null,
          generationError: null,
        });
        return;
      }
      updateNodeData(id, {
        isGenerating: false,
        generationStartedAt: null,
        generationError: '分镜规划返回为空',
      });
    } catch (error) {
      updateNodeData(id, {
        isGenerating: false,
        generationStartedAt: null,
        generationError: gatewayErrorMessage(error, '分镜规划失败'),
      });
    }
  }, [
    aspect,
    charactersText,
    durationSec,
    id,
    isGenerating,
    planScript,
    styleHint,
    submitDisabled,
    synopsis,
    updateNodeData,
  ]);

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
        {!planResult && !isGenerating && (
          <div className="flex h-full w-full flex-col items-center justify-center gap-2 text-text-muted/55">
            <Flame className="h-8 w-8 text-amber-300/50" aria-hidden />
            <span className="px-6 text-center text-[12px] leading-5">
              R18 短剧分镜规划
              <br />
              在下方面板输入梗概与角色卡，生成结构化分镜
            </span>
          </div>
        )}

        {planResult && (
          <div className="flex h-full w-full flex-col overflow-hidden px-3 pb-2 pt-7">
            <div className="mb-1.5 flex shrink-0 items-center gap-2">
              <span className="truncate text-[13px] font-semibold text-text-dark">
                {planResult.title || resolvedTitle}
              </span>
              <span className="shrink-0 rounded bg-white/[0.08] px-1.5 py-0.5 text-[10px] tabular-nums text-text-muted">
                {planResult.scenes.length} 镜头 ·{' '}
                {planResult.scenes.reduce((sum, s) => sum + (s.duration_sec || 0), 0)}s
              </span>
            </div>
            <div className="min-h-0 flex-1 space-y-1.5 overflow-y-auto pr-0.5">
              {planResult.scenes.map((scene) => {
                const badge = KIND_BADGES[scene.kind] ?? KIND_BADGES.plot;
                return (
                  <div
                    key={scene.scene_no}
                    className="rounded-md border border-white/[0.07] bg-white/[0.035] px-2 py-1.5"
                  >
                    <div className="flex items-center gap-1.5">
                      <span className="shrink-0 text-[10px] tabular-nums text-text-muted">
                        #{scene.scene_no}
                      </span>
                      <span
                        className={`shrink-0 rounded px-1.5 py-px text-[10px] font-medium ${badge.className}`}
                      >
                        {badge.label}
                      </span>
                      {scene.preset_id && (
                        <span className="shrink-0 truncate rounded bg-rose-500/15 px-1.5 py-px text-[10px] text-rose-200/90">
                          {scene.preset_id}
                        </span>
                      )}
                      <span className="shrink-0 text-[10px] tabular-nums text-text-muted">
                        {scene.duration_sec}s
                      </span>
                      <span className="min-w-0 flex-1 truncate text-[11px] text-text-dark/85">
                        {scene.title || scene.shot_description}
                      </span>
                    </div>
                    <div className="mt-1 line-clamp-2 text-[11px] leading-4 text-text-muted">
                      {scene.shot_description}
                    </div>
                    {(scene.dialogue || scene.narration) && (
                      <div className="mt-1 truncate text-[10.5px] leading-4 text-cyan-200/70">
                        {scene.dialogue || scene.narration}
                      </div>
                    )}
                    {selected && !isGenerating && (
                      <textarea
                        value={scene.image_prompt}
                        onChange={(event) =>
                          updateSceneField(scene.scene_no, 'image_prompt', event.target.value)
                        }
                        title="首帧提示词（可编辑，分镜节点重新生成即生效）"
                        placeholder="image_prompt"
                        className="nodrag mt-1 h-12 w-full resize-none rounded border border-white/[0.08] bg-black/25 px-1.5 py-1 text-[10px] leading-[13px] text-text-muted outline-none focus:border-white/25"
                      />
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {isGenerating && (
          <NodeGenerationOverlay
            startedAt={data.generationStartedAt ?? null}
            durationMs={data.generationDurationMs}
            hasBackground={false}
          />
        )}

        {!isGenerating && generationError && (
          <div className="nodrag absolute inset-x-5 top-1/2 z-10 flex -translate-y-1/2 flex-col items-center text-center">
            <div className="inline-flex items-center gap-1.5 text-[12px] font-semibold text-red-200">
              <AlertTriangle className="h-3.5 w-3.5 shrink-0 text-red-300/90" />
              <span>分镜规划失败</span>
            </div>
            <div
              className="mt-1 max-h-20 max-w-full overflow-y-auto break-words text-[11px] leading-4 text-red-100/76 [overflow-wrap:anywhere]"
              title={generationError}
            >
              {generationError}
            </div>
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
          <textarea
            value={synopsis}
            onChange={(event) => updateNodeData(id, { synopsis: event.target.value })}
            placeholder="剧情梗概：场景、人物关系、想要的表现方向…（必填）"
            className="min-h-0 w-full flex-1 resize-none whitespace-pre-wrap break-words border-none bg-transparent px-1 py-1 text-sm leading-6 text-text-dark outline-none placeholder:text-text-muted/45"
          />
          <textarea
            value={charactersText}
            onChange={(event) => updateNodeData(id, { charactersText: event.target.value })}
            placeholder="角色卡（可选，每行「名字：外貌/体型/服装描述」）&#10;林薇：28岁女性，黑色长直发，纤细体型，白色丝绸睡裙"
            className="h-14 w-full resize-none rounded-md border border-white/10 bg-white/[0.05] px-2 py-1.5 text-xs leading-5 text-text-dark outline-none placeholder:text-text-muted/45 focus:border-white/25"
          />
          <input
            value={styleHint}
            onChange={(event) => updateNodeData(id, { styleHint: event.target.value })}
            placeholder="画风/场景要求（可选，如：酒店夜景、暖色灯光、电影感）"
            className="w-full rounded-md border border-white/10 bg-white/[0.05] px-2 py-1.5 text-xs text-text-dark outline-none placeholder:text-text-muted/45 focus:border-white/25"
          />
          <div className="flex items-center justify-between gap-2">
            <div className="flex items-center gap-1">
              {DURATION_OPTIONS.map((opt) => (
                <button
                  key={opt.value}
                  type="button"
                  onClick={() => updateNodeData(id, { durationSec: opt.value })}
                  className={`h-7 rounded-md px-2 text-[11px] transition-colors ${
                    durationSec === opt.value
                      ? 'bg-white/[0.13] text-text-dark ring-1 ring-white/24'
                      : 'bg-white/[0.07] text-text-muted/95 hover:bg-white/[0.11] hover:text-text-dark'
                  }`}
                >
                  {opt.label}
                </button>
              ))}
              <span className="mx-1 h-4 w-px bg-white/10" />
              {ASPECT_OPTIONS.map((opt) => (
                <button
                  key={opt.value}
                  type="button"
                  onClick={() => updateNodeData(id, { aspect: opt.value })}
                  className={`h-7 rounded-md px-2 text-[11px] transition-colors ${
                    aspect === opt.value
                      ? 'bg-white/[0.13] text-text-dark ring-1 ring-white/24'
                      : 'bg-white/[0.07] text-text-muted/95 hover:bg-white/[0.11] hover:text-text-dark'
                  }`}
                >
                  {opt.label}
                </button>
              ))}
            </div>
            <button
              type="button"
              disabled={submitDisabled}
              title={synopsis.trim().length === 0 ? '先输入剧情梗概' : '生成分镜剧本'}
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
      )}
    </div>
  );
});

NSFWScriptNode.displayName = 'NSFWScriptNode';
