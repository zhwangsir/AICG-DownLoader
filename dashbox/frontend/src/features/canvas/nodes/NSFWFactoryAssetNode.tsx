// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
/**
 * R18 制作工厂 · 工序 4 数字资产 —— 分镜表（工序③）确认后按具体镜头提取
 * 资产清单：角色按分镜行对白「名字：」前缀出场序去重（描述回填上游剧本
 * 角色卡），场景按行 scene_desc 去重。逐项生成资产参考图（checkpoint/尺寸
 * 取工序①）。首个角色生成后其图作为 styleAnchorUrl，后续生成经 IPAdapter
 * reference_url 锚定画风一致；同名同 kind 已有产物的项跳过（断点续跑），
 * 逐项 writing generating→imageUrl/error。未确认分镜时禁止生成。
 */
import { memo, useCallback, useMemo } from 'react';
import type { NodeProps } from '@xyflow/react';
import { AlertTriangle, Flame, Images, Loader2 } from 'lucide-react';

import {
  CANVAS_NODE_TYPES,
  type NSFWFactoryAssetItem,
  type NSFWFactoryAssetNodeData,
  type NSFWFactoryInitNodeData,
  type NSFWFactoryScriptNodeData,
  type NSFWFactoryStoryboardNodeData,
  type NSFWFactoryStoryboardRow,
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
import { gatewayErrorMessage, useGenerateImage } from '@/lib/queries/model-library';
import { parseCharactersText } from '@/features/canvas/nodes/NSFWScriptNode';
import {
  FACTORY_NODE_W,
  NSFWFactoryShell,
  factoryToAbsoluteUrl,
  matchDialogueSpeaker,
  useFactoryUpstream,
} from './nsfwFactoryShared';

type NSFWFactoryAssetNodeProps = NodeProps & {
  id: string;
  data: NSFWFactoryAssetNodeData;
  selected?: boolean;
};

const NODE_W = FACTORY_NODE_W;
const NODE_H = 460;
const PANEL_HEIGHT = 260;
const PANEL_GAP = 12;

type PendingAsset = { kind: 'character' | 'scene'; name: string; desc: string };

/** 角色 + 场景（分镜行去重）→ 待生成资产清单。
 *
 * 角色描述回退链（人物介绍命中率关键）：
 * ①角色卡精确匹配 → ②包含匹配（对白「林薇姐」↔ 角色卡「林薇」）→
 * ③出场行 image_prompt（剧本规划 LLM 生成的英文提示词自带角色外貌锚点）。
 * 后端送 SDXL 前还会把含中文的描述译写为英文 tag。 */
export function buildPendingAssets(
  rows: NSFWFactoryStoryboardRow[],
  script: NSFWFactoryScriptNodeData | null,
): PendingAsset[] {
  const cardMap = new Map(
    parseCharactersText(script?.charactersText ?? '').map((c) => [c.name, (c.description ?? '').trim()]),
  );
  const names = new Set<string>();
  for (const row of rows) {
    const speaker = matchDialogueSpeaker(row.dialogue ?? '');
    if (speaker) names.add(speaker);
  }
  const resolveCharacterDesc = (name: string): string => {
    const exact = cardMap.get(name);
    if (exact) return exact;
    for (const [cardName, desc] of cardMap) {
      if (desc && (cardName.includes(name) || name.includes(cardName))) return desc;
    }
    const hitRow = rows.find(
      (r) => matchDialogueSpeaker(r.dialogue ?? '') === name && (r.imagePrompt ?? '').trim(),
    );
    return hitRow ? hitRow.imagePrompt.trim() : '';
  };
  const out: PendingAsset[] = [];
  for (const name of names) {
    out.push({ kind: 'character', name, desc: resolveCharacterDesc(name) });
  }
  const seenScenes = new Set<string>();
  for (const row of rows) {
    const desc = (row.sceneDesc ?? '').trim();
    if (!desc || seenScenes.has(desc)) continue;
    seenScenes.add(desc);
    out.push({ kind: 'scene', name: desc.slice(0, 12), desc });
  }
  return out;
}

/** 资产英文提示词：角色=角色卡描述、场景=scene_desc，统一拼质量词。 */
function assetPrompt(item: PendingAsset): string {
  if (item.kind === 'character') {
    return `${item.desc || item.name}, masterpiece, best quality, detailed, solo portrait, character reference sheet`;
  }
  return `${item.desc}, masterpiece, best quality, detailed environment, establishing shot, scenery`;
}

export const NSFWFactoryAssetNode = memo(({ id, data, selected }: NSFWFactoryAssetNodeProps) => {
  const updateNodeData = useCanvasStore((state) => state.updateNodeData);

  // 资产按已确认分镜的具体镜头提取：分镜表是嫡系上游（工序③）；
  // 剧本经分镜表上游链 BFS 仍可达，仅用于回填角色卡描述。
  const storyboard = useFactoryUpstream<NSFWFactoryStoryboardNodeData>(
    id,
    CANVAS_NODE_TYPES.nsfwFactoryStoryboard,
  );
  const script = useFactoryUpstream<NSFWFactoryScriptNodeData>(id, CANVAS_NODE_TYPES.nsfwFactoryScript);
  const init = useFactoryUpstream<NSFWFactoryInitNodeData>(id, CANVAS_NODE_TYPES.nsfwFactoryInit);
  const generateImage = useGenerateImage();

  const rows = useMemo(() => storyboard?.rows ?? [], [storyboard]);
  const confirmed = storyboard?.confirmed === true;

  const items = Array.isArray(data.items) ? data.items : [];
  const isGenerating = data.isGenerating === true;
  const generationError =
    typeof data.generationError === 'string' && data.generationError.length > 0
      ? data.generationError
      : null;

  const pendingAssets = useMemo(() => buildPendingAssets(rows, script), [rows, script]);
  const characters = useMemo(
    () => pendingAssets.filter((it) => it.kind === 'character'),
    [pendingAssets],
  );
  const scenes = useMemo(() => pendingAssets.filter((it) => it.kind === 'scene'), [pendingAssets]);

  /** 从 store 读最新 data（逐项更新防闭包过期）。 */
  const latest = useCallback((): NSFWFactoryAssetNodeData => {
    const node = useCanvasStore.getState().nodes.find((n) => n.id === id);
    return (node?.data as NSFWFactoryAssetNodeData | undefined) ?? data;
  }, [data, id]);

  const handleGenerateAll = useCallback(async () => {
    const d = latest();
    if (d.isGenerating) return;
    const projectId = readUrl().project;
    if (!projectId) {
      updateNodeData(id, { generationError: '缺少项目上下文（project 参数）' });
      return;
    }
    if (!init?.checkpoint?.trim()) {
      updateNodeData(id, { generationError: '上游工序①未选择底模，无法生成资产' });
      return;
    }
    if (rows.length === 0) {
      updateNodeData(id, { generationError: '上游分镜表为空，请先生成工序③分镜表' });
      return;
    }
    if (!confirmed) {
      updateNodeData(id, { generationError: '请先在工序③分镜表「确认分镜」后再生成资产' });
      return;
    }
    if (pendingAssets.length === 0) {
      updateNodeData(id, {
        generationError: '分镜表无角色/场景可提取（对白需「名字：」前缀，行需场景描述）',
      });
      return;
    }
    // 重建清单但保留同名同 kind 的已有产物（断点续跑）
    const keyOf = (it: { kind: string; name: string }) => `${it.kind}:${it.name}`;
    const existing = new Map(d.items.map((it) => [keyOf(it), it]));
    const nextItems: NSFWFactoryAssetItem[] = pendingAssets.map(
      (p) =>
        existing.get(keyOf(p)) ?? {
          kind: p.kind,
          name: p.name,
          desc: p.desc,
          imageUrl: '',
          generating: false,
          error: null,
        },
    );
    updateNodeData(id, { items: nextItems, isGenerating: true, generationError: null });
    let anchor = d.styleAnchorUrl;
    try {
      for (const item of nextItems) {
        if (item.imageUrl) continue;
        const itemKey = keyOf(item);
        const patchItem = (patch: Partial<NSFWFactoryAssetItem>) => {
          const cur = latest();
          updateNodeData(id, {
            items: cur.items.map((it) => (keyOf(it) === itemKey ? { ...it, ...patch } : it)),
          });
        };
        patchItem({ generating: true, error: null });
        try {
          const result = await generateImage.mutateAsync({
            prompt: assetPrompt({ kind: item.kind, name: item.name, desc: item.desc }),
            checkpoint: init.checkpoint.trim(),
            size: init.size ?? '832x1216',
            project_id: projectId,
            ...(anchor ? { reference_url: factoryToAbsoluteUrl(anchor) } : {}),
          });
          const url = result.ok ? (result.data.url ?? '') : '';
          if (!url) throw new Error(`资产「${item.name}」生成返回为空`);
          patchItem({ generating: false, imageUrl: url });
          if (!anchor && item.kind === 'character') {
            anchor = url;
            updateNodeData(id, { styleAnchorUrl: url });
          }
        } catch (e) {
          patchItem({ generating: false, error: gatewayErrorMessage(e, '资产生成失败') });
        }
      }
    } finally {
      updateNodeData(id, { isGenerating: false });
    }
  }, [confirmed, generateImage, id, init, latest, pendingAssets, rows, updateNodeData]);

  const doneCount = items.filter((it) => it.imageUrl).length;

  const opsPanel = (
    <div
      className={`nodrag absolute left-1/2 z-10 flex -translate-x-1/2 flex-col gap-2 overflow-hidden rounded-[var(--node-radius)] p-3 ${CANVAS_NODE_OPS_PANEL_CLASS}`}
      style={{
        top: `calc(100% + ${PANEL_GAP}px)`,
        height: PANEL_HEIGHT,
        width: Math.max(NODE_W, 540),
      }}
      onClick={(event) => event.stopPropagation()}
    >
      <div className="min-h-0 flex-1 space-y-1.5 overflow-y-auto pr-0.5 text-[11px]">
        <div className="flex items-center gap-1.5">
          <span className="text-[10px] text-text-muted/70">
            上游分镜 {rows.length} 镜
          </span>
          {rows.length > 0 &&
            (confirmed ? (
              <span className="rounded bg-emerald-400/15 px-1.5 py-px text-[10px] text-emerald-100/85">
                已确认
              </span>
            ) : (
              <span className="rounded bg-amber-500/15 px-1.5 py-px text-[10px] text-amber-200/85">
                待确认（生成前置条件）
              </span>
            ))}
        </div>
        <div>
          <span className="text-[10px] text-text-muted/70">角色清单（{characters.length}）</span>
          <div className="mt-1 flex flex-wrap gap-1">
            {characters.length === 0 ? (
              <span className="text-text-muted/55">分镜对白暂无「名字：」前缀角色</span>
            ) : (
              characters.map((c) => (
                <span
                  key={c.name}
                  className="rounded bg-amber-500/15 px-1.5 py-0.5 text-[10.5px] text-amber-100/90"
                  title={c.desc || c.name}
                >
                  {c.name}
                </span>
              ))
            )}
          </div>
        </div>
        <div>
          <span className="text-[10px] text-text-muted/70">场景清单（{scenes.length}）</span>
          <div className="mt-1 flex flex-wrap gap-1">
            {scenes.length === 0 ? (
              <span className="text-text-muted/55">分镜行暂无场景描述</span>
            ) : (
              scenes.map((s) => (
                <span
                  key={s.name}
                  className="rounded bg-cyan-500/15 px-1.5 py-0.5 text-[10.5px] text-cyan-100/90"
                  title={s.desc}
                >
                  {s.name}
                </span>
              ))
            )}
          </div>
        </div>
      </div>
      <div className="flex shrink-0 items-center justify-between gap-2">
        <span className="truncate text-[10px] text-text-muted/60">
          {init?.checkpoint ? `底模 ${init.checkpoint}` : '上游工序①未选底模'}
          {data.styleAnchorUrl ? ' · 已有画风锚定' : ''}
        </span>
        <button
          type="button"
          disabled={isGenerating}
          title="批量生成全部角色/场景资产图"
          onClick={(event) => {
            event.stopPropagation();
            void handleGenerateAll();
          }}
          className={`${NODE_GENERATE_BUTTON_BASE_CLASS} ${
            isGenerating
              ? NODE_GENERATE_BUTTON_DISABLED_CLASS
              : NODE_GENERATE_BUTTON_ENABLED_CLASS
          }`}
        >
          {isGenerating ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <Images className="h-4 w-4" />
          )}
        </button>
      </div>
    </div>
  );

  return (
    <NSFWFactoryShell
      id={id}
      type={CANVAS_NODE_TYPES.nsfwFactoryAsset}
      data={data}
      width={NODE_W}
      height={NODE_H}
      stageNo={4}
      stageName="数字资产"
      selected={selected}
      opsPanel={opsPanel}
    >
      {items.length === 0 ? (
        <div className="flex h-full w-full flex-col items-center justify-center gap-2 text-text-muted/55">
          <Flame className="h-8 w-8 text-amber-300/50" aria-hidden />
          <span className="px-6 text-center text-[12px] leading-5">
            工序④ 数字资产
            <br />
            连接工序③分镜表，确认分镜后按具体镜头提取角色/场景并批量生成资产参考图
          </span>
        </div>
      ) : (
        <div className="flex h-full w-full flex-col overflow-hidden px-3 pb-2 pt-7">
          <div className="mb-1.5 flex shrink-0 items-center gap-2 text-[11px] text-text-muted">
            <span className="tabular-nums">
              资产 {doneCount}/{items.length}
            </span>
            {data.styleAnchorUrl && (
              <span className="rounded bg-amber-500/15 px-1.5 py-px text-[10px] text-amber-200/85">
                画风已锚定
              </span>
            )}
            {isGenerating && <Loader2 className="h-3 w-3 animate-spin text-amber-300/80" />}
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto pr-0.5">
            <div className="grid grid-cols-4 gap-2">
              {items.map((item) => (
                <div
                  key={`${item.kind}:${item.name}`}
                  className="flex flex-col items-center gap-1 rounded-md border border-white/[0.07] bg-white/[0.035] p-1.5"
                  title={`${item.kind === 'character' ? '角色' : '场景'} · ${item.name}${item.desc ? `：${item.desc}` : ''}`}
                >
                  {item.imageUrl ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img
                      src={resolveImageDisplayUrl(item.imageUrl)}
                      alt={item.name}
                      className="h-16 w-16 rounded border border-white/10 object-cover"
                      draggable={false}
                    />
                  ) : item.generating ? (
                    <div className="flex h-16 w-16 items-center justify-center rounded border border-dashed border-white/15">
                      <Loader2 className="h-4 w-4 animate-spin text-amber-300/70" />
                    </div>
                  ) : (
                    <div className="flex h-16 w-16 items-center justify-center rounded border border-dashed border-white/12 px-1 text-center text-[9px] leading-3 text-text-muted/50">
                      {item.error ? '失败' : item.kind === 'character' ? '角色' : '场景'}
                    </div>
                  )}
                  <span className="w-full truncate text-center text-[10px] text-text-dark/80">
                    {item.name}
                  </span>
                </div>
              ))}
            </div>
          </div>
          {generationError && (
            <div
              className="mt-1 shrink-0 truncate text-[10.5px] text-red-300/85"
              title={generationError}
            >
              <AlertTriangle className="mr-1 inline h-3 w-3" />
              {generationError}
            </div>
          )}
        </div>
      )}
    </NSFWFactoryShell>
  );
});

NSFWFactoryAssetNode.displayName = 'NSFWFactoryAssetNode';
