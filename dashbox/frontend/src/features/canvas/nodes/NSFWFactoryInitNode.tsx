// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
/**
 * R18 制作工厂 · 工序 1 立项定位 —— 全流水线规格源头（纯配置节点，无生成动作）：
 * 题材（预设 + 自定义）、画风备注（下游剧本的 style_hint）、底模 checkpoint、
 * 首帧尺寸、单集时长、画幅。theme + checkpoint 均就绪后显示「规格就绪」徽标；
 * 下游工序（剧本/资产/镜头）经 useFactoryUpstream 回溯消费这份规格。
 */
import { memo } from 'react';
import type { NodeProps } from '@xyflow/react';
import { BadgeCheck } from 'lucide-react';

import {
  CANVAS_NODE_TYPES,
  type NSFWFactoryInitNodeData,
} from '@/features/canvas/domain/canvasNodes';
import { ModelNamePicker } from '@/components/settings/model-name-picker';
import { useCanvasStore } from '@/stores/canvasStore';
import {
  FACTORY_ASPECT_OPTIONS,
  FACTORY_CHIP_OFF_CLASS,
  FACTORY_CHIP_ON_CLASS,
  FACTORY_CUSTOM_THEME_VALUE,
  FACTORY_DURATION_OPTIONS,
  FACTORY_SIZE_PRESETS,
  FACTORY_THEME_OPTIONS,
  NSFWFactoryShell,
} from './nsfwFactoryShared';

type NSFWFactoryInitNodeProps = NodeProps & {
  id: string;
  data: NSFWFactoryInitNodeData;
  selected?: boolean;
};

const NODE_W = 460;
const NODE_H = 420;

const LABEL_CLASS = 'shrink-0 text-[10px] text-text-muted/70';
const INPUT_CLASS =
  'nodrag h-8 w-full rounded-md border border-white/10 bg-white/[0.05] px-2 text-[11.5px] text-text-dark outline-none placeholder:text-text-muted/45 focus:border-white/25';

export const NSFWFactoryInitNode = memo(({ id, data, selected }: NSFWFactoryInitNodeProps) => {
  const updateNodeData = useCanvasStore((state) => state.updateNodeData);

  const theme = data.theme ?? '';
  const themeNote = data.themeNote ?? '';
  const checkpoint = data.checkpoint ?? '';
  const size = data.size ?? '832x1216';
  const durationSec = data.durationSec ?? 90;
  const aspect = data.aspect ?? '9:16';

  const themeSelectValue = FACTORY_THEME_OPTIONS.some((opt) => opt.value === theme)
    ? theme
    : FACTORY_CUSTOM_THEME_VALUE;
  const specReady = theme.trim().length > 0 && checkpoint.trim().length > 0;

  return (
    <NSFWFactoryShell
      id={id}
      type={CANVAS_NODE_TYPES.nsfwFactoryInit}
      data={data}
      width={NODE_W}
      height={NODE_H}
      stageNo={1}
      stageName="立项定位"
      selected={selected}
    >
      <div className="flex h-full w-full flex-col gap-2.5 overflow-y-auto px-3 pb-3 pt-7">
        <div className="flex flex-col gap-1">
          <span className={LABEL_CLASS}>题材定位</span>
          <div className="flex items-center gap-1.5">
            <select
              value={themeSelectValue}
              onChange={(event) =>
                updateNodeData(id, {
                  theme:
                    event.target.value === FACTORY_CUSTOM_THEME_VALUE
                      ? ''
                      : event.target.value,
                })
              }
              className={`${INPUT_CLASS} min-w-0 flex-1 px-1.5`}
            >
              {FACTORY_THEME_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value} className="bg-neutral-800">
                  {opt.label}
                </option>
              ))}
              <option value={FACTORY_CUSTOM_THEME_VALUE} className="bg-neutral-800">
                自定义…
              </option>
            </select>
            {themeSelectValue === FACTORY_CUSTOM_THEME_VALUE && (
              <input
                value={theme}
                onChange={(event) => updateNodeData(id, { theme: event.target.value })}
                placeholder="输入自定义题材"
                className={`${INPUT_CLASS} min-w-0 flex-1`}
              />
            )}
          </div>
        </div>

        <div className="flex flex-col gap-1">
          <span className={LABEL_CLASS}>画风 / 场景备注（作为剧本 style_hint）</span>
          <input
            value={themeNote}
            onChange={(event) => updateNodeData(id, { themeNote: event.target.value })}
            placeholder="如：酒店夜景、暖色灯光、电影感（可选）"
            className={INPUT_CLASS}
          />
        </div>

        <div className="flex flex-col gap-1">
          <span className={LABEL_CLASS}>底模（checkpoint，SDXL 出图）</span>
          <ModelNamePicker
            value={checkpoint}
            onChange={(next) => updateNodeData(id, { checkpoint: next })}
            expectedTypes={['checkpoints']}
            ariaLabel="工厂底模"
            getOptionDisabledReason={(entry) =>
              entry.sdxl_incompatible ? (entry.sdxl_incompatible_reason ?? '不兼容 SDXL 工作流') : null
            }
          />
        </div>

        <div className="flex flex-col gap-1">
          <span className={LABEL_CLASS}>首帧尺寸</span>
          <div className="flex items-center gap-1">
            {FACTORY_SIZE_PRESETS.map((preset) => (
              <button
                key={preset.value}
                type="button"
                title={preset.value}
                onClick={() => updateNodeData(id, { size: preset.value })}
                className={size === preset.value ? FACTORY_CHIP_ON_CLASS : FACTORY_CHIP_OFF_CLASS}
              >
                {preset.label}
              </button>
            ))}
          </div>
        </div>

        <div className="flex flex-col gap-1">
          <span className={LABEL_CLASS}>单集时长</span>
          <div className="flex items-center gap-1">
            {FACTORY_DURATION_OPTIONS.map((v) => (
              <button
                key={v}
                type="button"
                onClick={() => updateNodeData(id, { durationSec: v })}
                className={durationSec === v ? FACTORY_CHIP_ON_CLASS : FACTORY_CHIP_OFF_CLASS}
              >
                {v >= 120 ? `${v / 60}min` : `${v}s`}
              </button>
            ))}
          </div>
        </div>

        <div className="flex flex-col gap-1">
          <span className={LABEL_CLASS}>画幅</span>
          <div className="flex items-center gap-1">
            {FACTORY_ASPECT_OPTIONS.map((opt) => (
              <button
                key={opt.value}
                type="button"
                onClick={() => updateNodeData(id, { aspect: opt.value })}
                className={aspect === opt.value ? FACTORY_CHIP_ON_CLASS : FACTORY_CHIP_OFF_CLASS}
              >
                {opt.label}
              </button>
            ))}
          </div>
        </div>

        <div className="mt-auto flex shrink-0 items-center gap-1.5 border-t border-white/[0.07] pt-2">
          {specReady ? (
            <span className="inline-flex items-center gap-1 rounded bg-emerald-400/15 px-2 py-1 text-[11px] font-medium text-emerald-100/90">
              <BadgeCheck className="h-3.5 w-3.5" />
              规格就绪 · {theme} · {size} · {durationSec}s · {aspect}
            </span>
          ) : (
            <span className="text-[10.5px] text-text-muted/60">
              {theme.trim().length === 0 ? '还需选择/输入题材' : '还需选择底模'}，齐备后规格就绪
            </span>
          )}
        </div>
      </div>
    </NSFWFactoryShell>
  );
});

NSFWFactoryInitNode.displayName = 'NSFWFactoryInitNode';
