// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
/**
 * 历史默认名迁移（nodeDisplay + canvasStore hydrate 路径）：
 *
 * 2026-08-19 工厂工序调序（原③资产/④分镜 → ③分镜/④资产）前创建的节点把旧
 * 默认名持久化在 data.displayName，与新链同名不同物（旧「工厂③数字资产」徽标
 * 已是工序 4/8）。hydrate 时按 type+旧名精确命中重置回现行默认名。
 */
import { beforeEach, describe, expect, it } from 'vitest';

import { useCanvasStore } from '@/stores/canvasStore';
import { CANVAS_NODE_TYPES } from '@/features/canvas/domain/canvasNodes';
import {
  isLegacyDefaultDisplayName,
  resolveNodeDisplayName,
} from '@/features/canvas/domain/nodeDisplay';

const MUTATION = {
  userEditsSinceHydrate: 0,
  lastMutationSource: null,
  pendingClearIntent: false,
} as const;

/** 经公开 hydrate 入口灌入单节点画布（内部走 normalizeNodes 迁移路径）。 */
function hydrateSingleNode(id: string, type: string, displayName?: string): void {
  useCanvasStore.getState().hydrateCanvasDraft({
    nodes: [
      {
        id,
        type,
        position: { x: 0, y: 0 },
        ...(displayName ? { data: { displayName } } : { data: {} }),
      } as never,
    ],
    edges: [],
    history: null,
    mutation: MUTATION,
  });
}

function singleNodeDisplayName(): string {
  const nodes = useCanvasStore.getState().nodes;
  expect(nodes).toHaveLength(1);
  return resolveNodeDisplayName(nodes[0].type, nodes[0].data);
}

describe('isLegacyDefaultDisplayName（纯函数）', () => {
  it('type + 旧序默认名精确命中', () => {
    expect(isLegacyDefaultDisplayName(CANVAS_NODE_TYPES.nsfwFactoryAsset, '工厂③数字资产')).toBe(true);
    expect(isLegacyDefaultDisplayName(CANVAS_NODE_TYPES.nsfwFactoryStoryboard, '工厂④分镜表')).toBe(true);
  });

  it('type 与旧名交叉不命中（防误伤对方类型）', () => {
    expect(isLegacyDefaultDisplayName(CANVAS_NODE_TYPES.nsfwFactoryStoryboard, '工厂③数字资产')).toBe(false);
    expect(isLegacyDefaultDisplayName(CANVAS_NODE_TYPES.nsfwFactoryAsset, '工厂④分镜表')).toBe(false);
  });

  it('新序名 / 自定义名 / 非字符串 / 未知类型不命中', () => {
    expect(isLegacyDefaultDisplayName(CANVAS_NODE_TYPES.nsfwFactoryAsset, '工厂④数字资产')).toBe(false);
    expect(isLegacyDefaultDisplayName(CANVAS_NODE_TYPES.nsfwFactoryAsset, '我的资产库')).toBe(false);
    expect(isLegacyDefaultDisplayName(CANVAS_NODE_TYPES.nsfwFactoryAsset, undefined)).toBe(false);
    expect(isLegacyDefaultDisplayName(CANVAS_NODE_TYPES.textAnnotation, '工厂③数字资产')).toBe(false);
  });
});

describe('hydrateCanvasDraft 旧默认名迁移', () => {
  beforeEach(() => {
    useCanvasStore.setState({ nodes: [], edges: [] });
  });

  it('旧序名重置为现行默认名（资产：工厂③数字资产 → 工厂④数字资产）', () => {
    hydrateSingleNode('a', CANVAS_NODE_TYPES.nsfwFactoryAsset, '工厂③数字资产');
    expect(singleNodeDisplayName()).toBe('工厂④数字资产');
  });

  it('旧序名重置为现行默认名（分镜：工厂④分镜表 → 工厂③分镜表）', () => {
    hydrateSingleNode('b', CANVAS_NODE_TYPES.nsfwFactoryStoryboard, '工厂④分镜表');
    expect(singleNodeDisplayName()).toBe('工厂③分镜表');
  });

  it('新序名与用户自定义名不动', () => {
    hydrateSingleNode('c', CANVAS_NODE_TYPES.nsfwFactoryStoryboard, '工厂③分镜表');
    expect(singleNodeDisplayName()).toBe('工厂③分镜表');

    hydrateSingleNode('d', CANVAS_NODE_TYPES.nsfwFactoryAsset, '我的资产库');
    expect(singleNodeDisplayName()).toBe('我的资产库');
  });

  it('非工厂类型同名文本不动（自定义名不受迁移影响）', () => {
    hydrateSingleNode('e', CANVAS_NODE_TYPES.textAnnotation, '工厂③数字资产');
    expect(singleNodeDisplayName()).toBe('工厂③数字资产');
  });
});
