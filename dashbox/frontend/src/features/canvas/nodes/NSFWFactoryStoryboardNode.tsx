// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
/**
 * R18 制作工厂 · 工序 3 分镜表 —— 上游剧本 episodes 经 flattenFactoryScenes 展平
 * 为全局连续镜号的分镜行（镜号/景别/运镜/台词/时长）。选中节点可逐行微调
 * shotSize/cameraMove/durationSec 与 imagePrompt/dialogue；「确认分镜」置
 * confirmed=true（下游数字资产按镜头提取、镜头工序开拍的前置条件）。
 */
import { memo, useCallback } from 'react';
import type { NodeProps } from '@xyflow/react';
import { CheckCircle2, ClipboardList, Flame } from 'lucide-react';

import {
  CANVAS_NODE_TYPES,
  type NSFWFactoryScriptNodeData,
  type NSFWFactoryStoryboardNodeData,
  type NSFWFactoryStoryboardRow,
} from '@/features/canvas/domain/canvasNodes';
import {
  CANVAS_NODE_OPS_PANEL_CLASS,
} from '@/features/canvas/ui/nodeFrameStyles';
import { useCanvasStore } from '@/stores/canvasStore';
import {
  NSFWFactoryShell,
  flattenFactoryScenes,
  useFactoryUpstream,
} from './nsfwFactoryShared';

type NSFWFactoryStoryboardNodeProps = NodeProps & {
  id: string;
  data: NSFWFactoryStoryboardNodeData;
  selected?: boolean;
};

const NODE_W = 560;
const NODE_H = 460;
const PANEL_HEIGHT = 170;
const PANEL_GAP = 12;

const ROW_GRID_CLASS = 'grid grid-cols-[2.4rem_3.4rem_4.2rem_1fr_2.8rem] items-center gap-1';
const CELL_INPUT_CLASS =
  'nodrag h-6 w-full rounded border border-white/10 bg-black/25 px-1 text-[10px] text-text-dark outline-none focus:border-white/25';

export const NSFWFactoryStoryboardNode = memo(
  ({ id, data, selected }: NSFWFactoryStoryboardNodeProps) => {
    const updateNodeData = useCanvasStore((state) => state.updateNodeData);

    const script = useFactoryUpstream<NSFWFactoryScriptNodeData>(
      id,
      CANVAS_NODE_TYPES.nsfwFactoryScript,
    );

    const rows = Array.isArray(data.rows) ? data.rows : [];
    const confirmed = data.confirmed === true;
    const episodes = script?.episodes ?? [];

    const latest = useCallback((): NSFWFactoryStoryboardNodeData => {
      const node = useCanvasStore.getState().nodes.find((n) => n.id === id);
      return (node?.data as NSFWFactoryStoryboardNodeData | undefined) ?? data;
    }, [data, id]);

    const handleBuildRows = useCallback(() => {
      const nextRows = flattenFactoryScenes(episodes);
      updateNodeData(id, { rows: nextRows, confirmed: false });
    }, [episodes, id, updateNodeData]);

    const handleConfirm = useCallback(() => {
      if (rows.length === 0) return;
      updateNodeData(id, { confirmed: true });
    }, [id, rows.length, updateNodeData]);

    const updateRow = useCallback(
      (shotNo: number, patch: Partial<NSFWFactoryStoryboardRow>) => {
        const cur = latest();
        updateNodeData(id, {
          rows: cur.rows.map((r) => (r.shotNo === shotNo ? { ...r, ...patch } : r)),
        });
      },
      [id, latest, updateNodeData],
    );

    const opsPanel = (
      <div
        className={`nodrag absolute left-1/2 z-10 flex -translate-x-1/2 flex-col gap-2 rounded-[var(--node-radius)] p-3 ${CANVAS_NODE_OPS_PANEL_CLASS}`}
        style={{ top: `calc(100% + ${PANEL_GAP}px)`, height: PANEL_HEIGHT, width: NODE_W }}
        onClick={(event) => event.stopPropagation()}
      >
        <div className="flex-1 text-[11px] leading-5 text-text-muted/80">
          从上游剧本（{episodes.length} 集 ·{' '}
          {episodes.reduce((sum, e) => sum + e.scenes.length, 0)} 镜头）展平生成分镜表；
          生成后可在节点体逐行微调景别/运镜/时长与提示词，再「确认分镜」放行下游
          数字资产（按镜头提取角色/场景）与镜头拍摄工序。
        </div>
        <button
          type="button"
          disabled={episodes.length === 0}
          title={episodes.length === 0 ? '上游暂无剧本' : '从剧本生成分镜表（覆盖当前表格）'}
          onClick={(event) => {
            event.stopPropagation();
            handleBuildRows();
          }}
          className="nodrag flex h-9 shrink-0 items-center justify-center gap-1.5 rounded-md bg-amber-400/20 text-[12px] font-semibold text-amber-100 transition-colors hover:bg-amber-400/30 disabled:cursor-not-allowed disabled:opacity-45"
        >
          <ClipboardList className="h-3.5 w-3.5" />
          从剧本生成分镜表
        </button>
      </div>
    );

    return (
      <NSFWFactoryShell
        id={id}
        type={CANVAS_NODE_TYPES.nsfwFactoryStoryboard}
        data={data}
        width={NODE_W}
        height={NODE_H}
        stageNo={3}
        stageName="分镜表"
        selected={selected}
        opsPanel={opsPanel}
      >
        {rows.length === 0 ? (
          <div className="flex h-full w-full flex-col items-center justify-center gap-2 text-text-muted/55">
            <Flame className="h-8 w-8 text-amber-300/50" aria-hidden />
            <span className="px-6 text-center text-[12px] leading-5">
              工序③ 分镜表
              <br />
              连接工序②剧本工程后，选中节点点击「从剧本生成分镜表」
            </span>
          </div>
        ) : (
          <div className="flex h-full w-full flex-col overflow-hidden px-3 pb-2 pt-7">
            <div className="mb-1 flex shrink-0 items-center gap-2">
              <span className="text-[12px] font-semibold text-text-dark">
                分镜表 · {rows.length} 镜
              </span>
              <span className="text-[10px] tabular-nums text-text-muted">
                总时长 {rows.reduce((sum, r) => sum + r.durationSec, 0)}s
              </span>
              {confirmed ? (
                <span className="inline-flex items-center gap-1 rounded bg-emerald-400/15 px-1.5 py-0.5 text-[10px] font-medium text-emerald-100/90">
                  <CheckCircle2 className="h-3 w-3" />
                  分镜已确认
                </span>
              ) : (
                <span className="rounded bg-amber-500/15 px-1.5 py-0.5 text-[10px] text-amber-200/85">
                  待确认
                </span>
              )}
            </div>

            <div className="min-h-0 flex-1 overflow-y-auto pr-0.5">
              <div
                className={`${ROW_GRID_CLASS} sticky top-0 z-[1] bg-[#282828] px-1 py-1 text-[10px] text-text-muted/70`}
              >
                <span>镜号</span>
                <span>景别</span>
                <span>运镜</span>
                <span>台词 / 旁白</span>
                <span className="text-right">时长</span>
              </div>
              {rows.map((row) => {
                return (
                  <div key={row.shotNo} className="border-b border-white/[0.05]">
                    <div className={`${ROW_GRID_CLASS} px-1 py-1`}>
                      <span className="truncate text-[10px] tabular-nums text-text-muted">
                        #{row.shotNo}
                      </span>
                      {selected ? (
                        <>
                          <input
                            value={row.shotSize}
                            onChange={(event) =>
                              updateRow(row.shotNo, { shotSize: event.target.value })
                            }
                            placeholder="景别"
                            title="景别（如：近景/中景/特写）"
                            className={CELL_INPUT_CLASS}
                          />
                          <input
                            value={row.cameraMove}
                            onChange={(event) =>
                              updateRow(row.shotNo, { cameraMove: event.target.value })
                            }
                            placeholder="运镜"
                            title="运镜（如：推近/平移/固定）"
                            className={CELL_INPUT_CLASS}
                          />
                        </>
                      ) : (
                        <>
                          <span className="truncate text-[10.5px] text-text-dark/85" title={row.shotSize}>
                            {row.shotSize || '—'}
                          </span>
                          <span
                            className="truncate text-[10.5px] text-text-dark/85"
                            title={row.cameraMove}
                          >
                            {row.cameraMove || '—'}
                          </span>
                        </>
                      )}
                      <span className="flex min-w-0 items-center gap-1">
                        <span
                          className="min-w-0 flex-1 truncate text-[10.5px] text-cyan-200/70"
                          title={row.dialogue || row.narration}
                        >
                          {row.dialogue || row.narration || '—'}
                        </span>
                      </span>
                      {selected ? (
                        <input
                          type="number"
                          min={1}
                          max={60}
                          value={row.durationSec}
                          onChange={(event) =>
                            updateRow(row.shotNo, {
                              durationSec: Math.max(1, Number(event.target.value) || 1),
                            })
                          }
                          title="时长（秒）"
                          className={`${CELL_INPUT_CLASS} text-right tabular-nums`}
                        />
                      ) : (
                        <span className="truncate text-right text-[10px] tabular-nums text-text-muted">
                          {row.durationSec}s
                        </span>
                      )}
                    </div>
                    {selected && (
                      <div className="flex flex-col gap-1 px-1 pb-1.5">
                        <textarea
                          value={row.imagePrompt}
                          onChange={(event) =>
                            updateRow(row.shotNo, { imagePrompt: event.target.value })
                          }
                          placeholder="首帧提示词 image_prompt"
                          title="首帧提示词（可编辑）"
                          className="nodrag h-10 w-full resize-none rounded border border-white/[0.08] bg-black/25 px-1.5 py-1 text-[10px] leading-[13px] text-text-muted outline-none focus:border-white/25"
                        />
                        <textarea
                          value={row.dialogue}
                          onChange={(event) =>
                            updateRow(row.shotNo, { dialogue: event.target.value })
                          }
                          placeholder="台词（「名字：内容」前缀供工序④提取角色资产）"
                          title="台词（可编辑）"
                          className="nodrag h-8 w-full resize-none rounded border border-white/[0.08] bg-black/25 px-1.5 py-1 text-[10px] leading-[13px] text-text-muted outline-none focus:border-white/25"
                        />
                      </div>
                    )}
                  </div>
                );
              })}
            </div>

            <button
              type="button"
              disabled={confirmed}
              title={confirmed ? '分镜已确认' : '确认分镜，放行下游镜头工序'}
              onClick={(event) => {
                event.stopPropagation();
                handleConfirm();
              }}
              className="nodrag mt-1.5 flex h-9 shrink-0 items-center justify-center gap-1.5 rounded-md bg-amber-400/20 text-[12px] font-semibold text-amber-100 transition-colors hover:bg-amber-400/30 disabled:cursor-not-allowed disabled:opacity-45"
            >
              <CheckCircle2 className="h-3.5 w-3.5" />
              {confirmed ? '已确认' : '确认分镜'}
            </button>
          </div>
        )}
      </NSFWFactoryShell>
    );
  },
);

NSFWFactoryStoryboardNode.displayName = 'NSFWFactoryStoryboardNode';
