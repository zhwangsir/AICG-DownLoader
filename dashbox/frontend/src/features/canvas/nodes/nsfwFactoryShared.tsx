// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
/**
 * R18 制作工厂 8 工序节点共享基建：
 *
 * - `NSFWFactoryShell`：统一包壳（左右 Handle、NodeHeader、R18 未开启锁定态、
 *   节点外框 width/height + 工序徽标），各工序只填 children + opsPanel。
 * - `useFactoryUpstream`：沿入边 BFS 回溯取最近指定类型上游节点的 data，
 *   订阅 store 的 nodes/edges 变化重算，连线增删即时反映。
 * - `flattenFactoryScenes`：把分集 episodes 展平为全局连续镜号的分镜表行。
 *
 * 工序产物全存各自 node.data（updateNodeData 写入），下游通过连线和本 hook 读取。
 */
import { memo, useMemo, type ReactNode } from 'react';
import { Handle, Position } from '@xyflow/react';
import { Flame, ShieldAlert } from 'lucide-react';

import type {
  CanvasNodeType,
  NSFWFactoryStoryboardRow,
  NodeDisplayData,
} from '@/features/canvas/domain/canvasNodes';
import { resolveNodeDisplayName } from '@/features/canvas/domain/nodeDisplay';
import { NodeHeader, NODE_HEADER_FLOATING_POSITION_CLASS } from '@/features/canvas/ui/NodeHeader';
import {
  CANVAS_NODE_INPUT_BODY_FRAME_CLASS,
  CANVAS_NODE_INPUT_SURFACE_CLASS,
  canvasNodeFrameClass,
} from '@/features/canvas/ui/nodeFrameStyles';
import { useCanvasStore } from '@/stores/canvasStore';
import { useNsfwStatus, type R18SceneData } from '@/lib/queries/model-library';

export const FACTORY_NODE_W = 460;
export const FACTORY_NODE_H = 460;

export const FACTORY_THEME_OPTIONS: ReadonlyArray<{ value: string; label: string }> = [
  { value: '都市情感', label: '都市情感' },
  { value: '古风暧昧', label: '古风暧昧' },
  { value: '校园纯爱', label: '校园纯爱' },
  { value: '奇幻冒险', label: '奇幻冒险' },
  { value: '职场诱惑', label: '职场诱惑' },
];

export const FACTORY_CUSTOM_THEME_VALUE = '__custom__';

export const FACTORY_SIZE_PRESETS: ReadonlyArray<{ value: string; label: string }> = [
  { value: '832x1216', label: '竖 2:3' },
  { value: '1216x832', label: '横 3:2' },
  { value: '1024x1024', label: '方 1:1' },
];

export const FACTORY_DURATION_OPTIONS = [60, 90, 120, 180] as const;

export const FACTORY_ASPECT_OPTIONS: ReadonlyArray<{
  value: '9:16' | '16:9' | '1:1';
  label: string;
}> = [
  { value: '9:16', label: '竖 9:16' },
  { value: '16:9', label: '横 16:9' },
  { value: '1:1', label: '方 1:1' },
];

export const FACTORY_COLOR_PROFILES: ReadonlyArray<{
  value: 'none' | 'warm' | 'cool' | 'film';
  label: string;
}> = [
  { value: 'none', label: '原色' },
  { value: 'warm', label: '暖色' },
  { value: 'cool', label: '冷色' },
  { value: 'film', label: '胶片' },
];

export const FACTORY_TRANSITIONS: ReadonlyArray<{ value: 'none' | 'fade'; label: string }> = [
  { value: 'none', label: '硬切' },
  { value: 'fade', label: '淡入淡出' },
];

/** chips 选中/未选中态样式（各工序操作面板共用）。 */
export const FACTORY_CHIP_ON_CLASS =
  'nodrag h-7 rounded-md px-2 text-[11px] transition-colors bg-white/[0.13] text-text-dark ring-1 ring-white/24';
export const FACTORY_CHIP_OFF_CLASS =
  'nodrag h-7 rounded-md px-2 text-[11px] transition-colors bg-white/[0.07] text-text-muted/95 hover:bg-white/[0.11] hover:text-text-dark';

type NSFWFactoryShellProps = {
  id: string;
  /** 节点类型（标题回退与工序徽标解析用）。 */
  type: CanvasNodeType;
  data: NodeDisplayData;
  width: number;
  height: number;
  /** 工序序号 1-8（节点头工序徽标）。 */
  stageNo: number;
  stageName: string;
  selected?: boolean;
  children: ReactNode;
  opsPanel?: ReactNode;
};

/**
 * 工厂节点统一外壳。R18 未开启时渲染锁定态（保留 Handle 可连线、禁生成）；
 * 开启后渲染 NodeHeader + 外框 + children，selected 时额外渲染 opsPanel。
 */
export const NSFWFactoryShell = memo(
  ({
    id,
    type,
    data,
    width,
    height,
    stageNo,
    stageName,
    selected = false,
    children,
    opsPanel,
  }: NSFWFactoryShellProps) => {
    const updateNodeData = useCanvasStore((state) => state.updateNodeData);
    const setSelectedNode = useCanvasStore((state) => state.setSelectedNode);
    const { data: nsfwStatusData, isLoading: nsfwLoading } = useNsfwStatus();
    const nsfwEnabled = nsfwStatusData?.data?.nsfw_enabled === true;

    const resolvedTitle = useMemo(
      () => resolveNodeDisplayName(type, data),
      [data, type],
    );

    // ── R18 未开启：锁定态（保留连线把手，禁止操作）──
    if (!nsfwLoading && !nsfwEnabled) {
      return (
        <div
          className={`relative flex h-full w-full flex-col items-center justify-center gap-2 rounded-[var(--node-radius)] border border-amber-400/30 bg-amber-950/25 ${CANVAS_NODE_INPUT_SURFACE_CLASS}`}
          style={{ width, height }}
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
        style={{ width, height }}
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
          metaText={`工序 ${stageNo}/8 · ${stageName}`}
          editable
          onTitleChange={(nextTitle) => updateNodeData(id, { displayName: nextTitle })}
        />

        <div
          className={`relative flex h-full w-full flex-col overflow-hidden rounded-[var(--node-radius)] border transition-colors ${
            CANVAS_NODE_INPUT_SURFACE_CLASS
          } ${canvasNodeFrameClass({ selected })} ${CANVAS_NODE_INPUT_BODY_FRAME_CLASS}`}
        >
          {children}
        </div>

        {selected && nsfwEnabled && opsPanel}
      </div>
    );
  },
);

NSFWFactoryShell.displayName = 'NSFWFactoryShell';

/**
 * 沿入边 BFS 回溯（最多 8 层）取最近一个 `node.type === type` 的上游节点 data。
 * 订阅 store 的 nodes/edges 变化重算；找不到返回 null。
 */
export function useFactoryUpstream<T>(id: string, type: CanvasNodeType): T | null {
  const nodes = useCanvasStore((state) => state.nodes);
  const edges = useCanvasStore((state) => state.edges);
  return useMemo(() => {
    const nodeMap = new Map(nodes.map((n) => [n.id, n]));
    const visited = new Set<string>([id]);
    let frontier: string[] = [id];
    for (let depth = 0; depth < 8 && frontier.length > 0; depth += 1) {
      const next: string[] = [];
      for (const curId of frontier) {
        for (const edge of edges) {
          if (edge.target !== curId) continue;
          const srcId = edge.source;
          if (visited.has(srcId)) continue;
          visited.add(srcId);
          const srcNode = nodeMap.get(srcId);
          if (!srcNode) continue;
          if (srcNode.type === type) {
            return (srcNode.data as unknown) as T;
          }
          next.push(srcId);
        }
      }
      frontier = next;
    }
    return null;
  }, [edges, id, nodes, type]);
}

/**
 * 把分集 episodes 展平为全局连续镜号的分镜表行（snake_case → camelCase）。
 * emotion 取 scene.emotion ?? '平静'；presetId/audio/kind 直传。
 */
export function flattenFactoryScenes(
  episodes: Array<{ episodeNo: number; title: string; scenes: R18SceneData[] }>,
): NSFWFactoryStoryboardRow[] {
  const rows: NSFWFactoryStoryboardRow[] = [];
  let globalShotNo = 0;
  for (const ep of episodes) {
    for (const scene of ep.scenes) {
      globalShotNo += 1;
      rows.push({
        shotNo: globalShotNo,
        episodeNo: ep.episodeNo,
        kind: scene.kind,
        shotSize: scene.shot_size ?? '',
        cameraMove: scene.camera_move ?? '',
        imagePrompt: scene.image_prompt,
        videoPrompt: scene.video_prompt,
        dialogue: scene.dialogue,
        narration: scene.narration,
        emotion: scene.emotion ?? '平静',
        durationSec: scene.duration_sec,
        presetId: scene.preset_id,
        audio: scene.audio,
        actionDesc: scene.action_desc ?? '',
        expression: scene.expression ?? '',
        sceneDesc: scene.scene_desc ?? '',
      });
    }
  }
  return rows;
}

/** 相对 URL → 浏览器可访问绝对地址（参考图/首帧上送后端用）。 */
export function factoryToAbsoluteUrl(url: string): string {
  if (/^https?:\/\//i.test(url) || url.startsWith('data:')) return url;
  return `${window.location.origin}${url.startsWith('/') ? '' : '/'}${url}`;
}

/** 从「名字：台词」前缀提取说话人名字（资产图匹配用），无前缀返回 null。 */
export function matchDialogueSpeaker(dialogue: string): string | null {
  const match = dialogue.match(/^\s*([^：:，,。.!?！？\s]{1,12})\s*[：:]/);
  return match ? match[1] : null;
}
