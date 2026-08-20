// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useTranslation } from "react-i18next";
import {
  FileText,
  Film,
  Flame,
  Globe,
  Image,
  LayoutGrid,
  Music,
  Orbit,
  Sparkles,
  Type,
  Upload,
  Video,
  type LucideIcon,
} from "lucide-react";

import {
  CANVAS_NODE_TYPES,
  type CanvasNodeType,
} from "@/features/canvas/domain/canvasNodes";
import { nodeCatalog } from "@/features/canvas/application/nodeCatalog";
import type { MenuIconKey } from "@/features/canvas/domain/nodeRegistry";
import { useNsfwStatus } from "@/lib/queries/model-library";

export const canvasMenuIconMap: Record<MenuIconKey, LucideIcon> = {
  upload: Upload,
  sparkles: Sparkles,
  layout: LayoutGrid,
  text: Type,
  video: Video,
  audio: Music,
  script: FileText,
  pano360: Globe,
  threeDWorld: Orbit,
  videoCompose: Film,
  nsfw: Flame,
};

export const CANVAS_ADD_NODE_TYPES: readonly CanvasNodeType[] = [
  CANVAS_NODE_TYPES.textAnnotation,
  CANVAS_NODE_TYPES.beatContext,
  CANVAS_NODE_TYPES.imageGen,
  CANVAS_NODE_TYPES.video,
  CANVAS_NODE_TYPES.videoCompose,
  CANVAS_NODE_TYPES.audio,
  CANVAS_NODE_TYPES.script,
  CANVAS_NODE_TYPES.upload,
  CANVAS_NODE_TYPES.pano360Viewer,
  CANVAS_NODE_TYPES.threeDWorld,
];

export const CANVAS_MENU_ICON_CELL_CLASS =
  "flex min-w-[58px] max-w-[96px] flex-col items-center gap-1.5 rounded-xl px-2.5 py-2 text-center transition-colors";

export const CANVAS_MENU_ROW_CLASS =
  "flex w-full items-center gap-3 rounded-xl py-2 pl-[17px] pr-2 text-left transition-colors";

interface CanvasMenuSectionHeaderProps {
  label: string;
  className?: string;
}

export function CanvasMenuSectionHeader({
  label,
  className = "",
}: CanvasMenuSectionHeaderProps) {
  return (
    <div className={`text-[15px] font-semibold leading-none text-white/62 ${className}`}>
      {label}
    </div>
  );
}

interface CanvasAddNodeGridProps {
  onSelectNode: (type: CanvasNodeType, clientPosition?: { x: number; y: number }) => void;
  onItemPointerEnter?: () => void;
  transitionDelayForIndex?: (index: number) => string | undefined;
  /** R18 制作流水线一键插入（8 工序左到右自动连线）。 */
  onSpawnR18Pipeline?: () => void;
}

export function CanvasAddNodeGrid({
  onSelectNode,
  onItemPointerEnter,
  transitionDelayForIndex,
  onSpawnR18Pipeline,
}: CanvasAddNodeGridProps) {
  const { t } = useTranslation();
  // R18 开启后菜单附加 3 个 R18 入口（2026-08-19 深度精简）：
  // 图片/视频单件工具 + 短剧工厂（单节点快进）。旧分步三件套（剧本/分镜/
  // 出片）与工厂 8 个单工序节点已隐藏——一键流水线按钮覆盖常规用法，
  // 单工序仍可从前一工序节点的「+」handle 上下文 spawn（白名单保留）。
  // 未开启时普通用户不可见（与模型库 NSFW 门禁同口径）。
  const { data: nsfwStatusData } = useNsfwStatus();
  const nsfwEnabled = nsfwStatusData?.data?.nsfw_enabled === true;
  const nodeTypes = nsfwEnabled
    ? [
        ...CANVAS_ADD_NODE_TYPES,
        CANVAS_NODE_TYPES.nsfwImageGen,
        CANVAS_NODE_TYPES.nsfwVideoGen,
        CANVAS_NODE_TYPES.nsfwDramaStudio,
      ]
    : CANVAS_ADD_NODE_TYPES;

  return (
    <div className="grid grid-cols-4 justify-items-center gap-x-2 gap-y-5">
      {nodeTypes.map((type, index) => {
        const definition = nodeCatalog.getDefinition(type);
        if (!definition) return null;
        const Icon = canvasMenuIconMap[definition.menuIcon] ?? Image;
        return (
          <button
            key={type}
            type="button"
            onMouseEnter={onItemPointerEnter}
            className={`${CANVAS_MENU_ICON_CELL_CLASS} hover:bg-white/[0.075]`}
            style={{ transitionDelay: transitionDelayForIndex?.(index) }}
            onClick={(event) => onSelectNode(type, { x: event.clientX, y: event.clientY })}
          >
            <div className="flex h-10 w-10 items-center justify-center rounded-full bg-cyan-300/[0.12]">
              <Icon className="h-4 w-4 text-cyan-200" />
            </div>
            <span className="max-w-full overflow-hidden text-ellipsis whitespace-nowrap text-[13px] leading-5 text-white/82">
              {t(definition.menuLabelKey)}
            </span>
          </button>
        );
      })}
      {nsfwEnabled && onSpawnR18Pipeline && (
        <button
          type="button"
          onMouseEnter={onItemPointerEnter}
          className="col-span-4 flex w-full items-center gap-3 rounded-xl border border-amber-300/25 bg-amber-400/[0.08] px-3.5 py-2.5 text-left transition-colors hover:bg-amber-400/[0.14]"
          onClick={(event) => {
            event.stopPropagation();
            onSpawnR18Pipeline();
          }}
        >
          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-amber-300/[0.16]">
            <Flame className="h-4 w-4 text-amber-200" />
          </div>
          <div className="min-w-0">
            <div className="text-[13px] font-medium text-amber-100/95">
              {t("node.menu.nsfwFactoryPipeline")}
            </div>
            <div className="truncate text-[11px] text-amber-100/55">
              {t("node.menu.nsfwFactoryPipelineHint")}
            </div>
          </div>
        </button>
      )}
    </div>
  );
}
