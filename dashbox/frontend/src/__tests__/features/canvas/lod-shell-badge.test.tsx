// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
/**
 * 低缩放档 shell 的可辨识性修复（修复报告 §5.2-2）：
 *
 * ① 工序徽标：shell 无标题，工厂 8 工序在低缩放下是一排同款灰块；新增
 *   LOD_SHELL_STAGE_BADGES 徽标（与节点头 metaText「工序 N/8」同源的静态
 *   类型级信息），字号按节点宽 ~10% 内联取值——缩放换算到屏幕恒为可读大小。
 * ② 尺寸兜底：SHELL_FALLBACK_SIZES 补齐 R18 全系；`??` 改 `> 0` 判定——
 *   RF 对未渲染过的节点给 0（非 nullish），`0 ?? 460 === 0` 曾使 shell
 *   渲染 0×0 彻底不可见（2026-08-20 浏览器实测踩过）。
 * ③ busy 角标兼容工厂 isRunning / R18 批次 isBatchRunning。
 */
import { render } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

// ---- @xyflow/react：只需 useStore 读 transform[2]，Handle 渲染成空节点 ----
let currentZoom = 1;
vi.mock('@xyflow/react', () => ({
  Handle: () => null,
  Position: { Left: 'left', Right: 'right', Top: 'top', Bottom: 'bottom' },
  useStore: (selector: (state: { transform: [number, number, number] }) => unknown) =>
    selector({ transform: [0, 0, currentZoom] }),
}));

// 画布 store：用例里节点均不选中（选中会击穿 shell 显示完整组件）。
vi.mock('@/stores/canvasStore', () => ({
  useCanvasStore: (selector: (state: { selectedNodeId: string | null }) => unknown) =>
    selector({ selectedNodeId: null }),
}));

const {
  LOD_SHELL_STAGE_BADGES,
  resolveShellSize,
  withLodShell,
} = await import('@/features/canvas/nodes/LodShellNode');

function FullStub() {
  return <div data-testid="full-node">full</div>;
}

function renderShellNode(type: string, data: Record<string, unknown>, width?: number) {
  const Wrapped = withLodShell(type, FullStub);
  return render(
    <Wrapped
      id="node-1"
      type={type}
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      data={data as any}
      selected={false}
      width={width}
      height={width}
      dragging={false}
      zIndex={0}
      isConnectable
      positionAbsoluteX={0}
      positionAbsoluteY={0}
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      {...({} as any)}
    />,
  );
}

beforeEach(() => {
  currentZoom = 1;
});

describe('resolveShellSize（零尺寸兜底）', () => {
  it('工厂类型兜底尺寸正确（分镜表 560×460）', () => {
    expect(resolveShellSize('nsfwFactoryStoryboardNode', undefined, undefined))
      .toEqual({ width: 560, height: 460 });
    expect(resolveShellSize('nsfwFactoryInitNode', undefined, undefined))
      .toEqual({ width: 460, height: 420 });
  });

  it('0（RF 未渲染节点）与 undefined 都落兜底——?? 兜不住 0 的回归', () => {
    expect(resolveShellSize('nsfwFactoryStoryboardNode', 0, 0))
      .toEqual({ width: 560, height: 460 });
    expect(resolveShellSize('unknownType', undefined, undefined))
      .toEqual({ width: 400, height: 300 }); // DEFAULT_SHELL_SIZE
  });

  it('正数实测值优先直通', () => {
    expect(resolveShellSize('nsfwFactoryStoryboardNode', 640, 500))
      .toEqual({ width: 640, height: 500 });
  });
});

describe('LOD_SHELL_STAGE_BADGES（工序徽标映射）', () => {
  it('工厂 8 工序一一对应，序号连续且与现行工序序一致', () => {
    expect(Object.fromEntries(Object.entries(LOD_SHELL_STAGE_BADGES))).toEqual({
      nsfwFactoryInitNode: '①立项',
      nsfwFactoryScriptNode: '②剧本',
      nsfwFactoryStoryboardNode: '③分镜',
      nsfwFactoryAssetNode: '④资产',
      nsfwFactoryShotNode: '⑤镜头',
      nsfwFactoryAudioNode: '⑥音频',
      nsfwFactoryComposeNode: '⑦合成',
      nsfwFactoryQcNode: '⑧质检',
    });
  });

  it('非工厂类型无徽标', () => {
    expect(LOD_SHELL_STAGE_BADGES.imageGenNode).toBeUndefined();
    expect(LOD_SHELL_STAGE_BADGES.nsfwDramaStudioNode).toBeUndefined();
  });
});

describe('withLodShell 渲染（低缩放档）', () => {
  it('工厂节点 shell 渲染工序徽标，字号按节点宽 ~10% 取值', () => {
    currentZoom = 0.26;
    const { container } = renderShellNode('nsfwFactoryStoryboardNode', {});
    const badge = container.querySelector('.dc-lod-shell__badge');
    expect(badge).not.toBeNull();
    expect(badge?.textContent).toBe('③分镜');
    // 分镜表兜底宽 560 → 字号 56（屏幕上 0.26×56 ≈ 15px，可读）
    expect(badge?.getAttribute('style')).toContain('font-size: 56px');
  });

  it('width=0 的未渲染节点 shell 按类型兜底尺寸渲染（不再 0×0 不可见）', () => {
    currentZoom = 0.26;
    const { container } = renderShellNode('nsfwFactoryStoryboardNode', {}, 0);
    const shell = container.querySelector('.dc-lod-shell');
    expect(shell).not.toBeNull();
    expect(shell?.getAttribute('style')).toContain('width: 560px');
    expect(shell?.getAttribute('style')).toContain('height: 460px');
  });

  it('非工厂节点 shell 无徽标', () => {
    currentZoom = 0.26;
    const { container } = renderShellNode('imageGenNode', {});
    expect(container.querySelector('.dc-lod-shell__badge')).toBeNull();
    expect(container.querySelector('.dc-lod-shell')).not.toBeNull();
  });

  it('busy 角标兼容工厂 isRunning 与 R18 批次 isBatchRunning', () => {
    currentZoom = 0.26;
    const factory = renderShellNode('nsfwFactoryComposeNode', { isRunning: true });
    expect(factory.container.querySelector('.dc-lod-shell__busy')).not.toBeNull();

    const batch = renderShellNode('nsfwVideoBatchNode', { isBatchRunning: true });
    expect(batch.container.querySelector('.dc-lod-shell__busy')).not.toBeNull();
  });

  it('正常缩放档渲染完整组件（无 shell）', () => {
    currentZoom = 0.5;
    const { container } = renderShellNode('nsfwFactoryStoryboardNode', {});
    expect(container.querySelector('.dc-lod-shell')).toBeNull();
    expect(container.querySelector('[data-testid="full-node"]')).not.toBeNull();
  });
});
