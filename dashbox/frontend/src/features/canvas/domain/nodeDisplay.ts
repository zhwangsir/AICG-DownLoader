// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import {
  CANVAS_NODE_TYPES,
  type CanvasNodeData,
  type CanvasNodeType,
  type ExportImageNodeResultKind,
} from './canvasNodes';

export const DEFAULT_NODE_DISPLAY_NAME: Record<CanvasNodeType, string> = {
  [CANVAS_NODE_TYPES.upload]: '上传资源',
  [CANVAS_NODE_TYPES.imageEdit]: 'AI 图片',
  [CANVAS_NODE_TYPES.imageGen]: '图片节点',
  [CANVAS_NODE_TYPES.exportImage]: '结果图片',
  [CANVAS_NODE_TYPES.beatContext]: '镜头上下文',
  [CANVAS_NODE_TYPES.textAnnotation]: '文本',
  [CANVAS_NODE_TYPES.group]: '分组',
  [CANVAS_NODE_TYPES.storyboardSplit]: '分格抽取结果',
  [CANVAS_NODE_TYPES.storyboardGen]: '多版本宫格',
  [CANVAS_NODE_TYPES.video]: '视频',
  [CANVAS_NODE_TYPES.audio]: '音频',
  [CANVAS_NODE_TYPES.videoStory]: '视频故事',
  [CANVAS_NODE_TYPES.videoCompose]: '视频合成',
  [CANVAS_NODE_TYPES.script]: '脚本生成器',
  [CANVAS_NODE_TYPES.pano360Viewer]: '360° 全景查看器',
  [CANVAS_NODE_TYPES.threeDWorld]: '3D 世界',
  [CANVAS_NODE_TYPES.skill]: '技能',
  [CANVAS_NODE_TYPES.nsfwImageGen]: 'R18 图片',
  [CANVAS_NODE_TYPES.nsfwVideoGen]: 'R18 视频',
  [CANVAS_NODE_TYPES.nsfwScript]: 'R18 剧本',
  [CANVAS_NODE_TYPES.nsfwStoryboard]: 'R18 分镜',
  [CANVAS_NODE_TYPES.nsfwVideoBatch]: 'R18 出片',
  [CANVAS_NODE_TYPES.nsfwDramaStudio]: 'R18 短剧工厂',
  [CANVAS_NODE_TYPES.nsfwFactoryInit]: '工厂①立项定位',
  [CANVAS_NODE_TYPES.nsfwFactoryScript]: '工厂②剧本工程',
  [CANVAS_NODE_TYPES.nsfwFactoryStoryboard]: '工厂③分镜表',
  [CANVAS_NODE_TYPES.nsfwFactoryAsset]: '工厂④数字资产',
  [CANVAS_NODE_TYPES.nsfwFactoryShot]: '工厂⑤镜头视频',
  [CANVAS_NODE_TYPES.nsfwFactoryAudio]: '工厂⑥音频制作',
  [CANVAS_NODE_TYPES.nsfwFactoryCompose]: '工厂⑦后期合成',
  [CANVAS_NODE_TYPES.nsfwFactoryQc]: '工厂⑧质检预览',
};

export const EXPORT_RESULT_DISPLAY_NAME: Record<ExportImageNodeResultKind, string> = {
  generic: '结果图片',
  storyboardGenOutput: '宫格输出',
  storyboardSplitExport: '分格导出',
  storyboardFrameEdit: '单格结果',
  matte: '抠图结果',
  upscale: '高清放大',
};

function resolveExportResultDefault(data: Partial<CanvasNodeData>): string {
  const resultKind = (data as { resultKind?: ExportImageNodeResultKind }).resultKind ?? 'generic';
  return EXPORT_RESULT_DISPLAY_NAME[resultKind];
}

export function getDefaultNodeDisplayName(type: CanvasNodeType, data: Partial<CanvasNodeData>): string {
  if (type === CANVAS_NODE_TYPES.exportImage) {
    return resolveExportResultDefault(data);
  }
  return DEFAULT_NODE_DISPLAY_NAME[type];
}

export function resolveNodeDisplayName(type: CanvasNodeType, data: Partial<CanvasNodeData>): string {
  const customTitle = typeof data.displayName === 'string' ? data.displayName.trim() : '';
  if (customTitle) {
    return customTitle;
  }

  if (type === CANVAS_NODE_TYPES.group) {
    const legacyLabel = typeof (data as { label?: string }).label === 'string'
      ? (data as { label?: string }).label?.trim()
      : '';
    if (legacyLabel) {
      return legacyLabel;
    }
  }

  return getDefaultNodeDisplayName(type, data);
}

export function isNodeUsingDefaultDisplayName(type: CanvasNodeType, data: Partial<CanvasNodeData>): boolean {
  const customTitle = typeof data.displayName === 'string' ? data.displayName.trim() : '';
  if (!customTitle) {
    return true;
  }
  return customTitle === getDefaultNodeDisplayName(type, data);
}

/**
 * 历史默认名迁移表（type + 旧默认名精确匹配）。
 *
 * displayName 是创建节点时写入并随画布 JSON 持久化的默认名；工序调序/默认名
 * 改名后，存量节点仍显示旧默认名（如 2026-08-19 工厂调序前的「工厂③数字资产」，
 * 现行序为 ③分镜表/④数字资产）。hydrate 时命中即重置回现行默认名——重置而非
 * 保留，使调序/改名只需改 DEFAULT_NODE_DISPLAY_NAME，不会产生新的存量漂移。
 * 未来再有默认名变更，往本表补一行旧名即可。
 */
export const LEGACY_DEFAULT_DISPLAY_NAMES: Partial<Record<CanvasNodeType, readonly string[]>> = {
  // 2026-08-19 工厂工序调序（原③资产/④分镜 → ③分镜/④资产）前的旧默认名
  [CANVAS_NODE_TYPES.nsfwFactoryAsset]: ['工厂③数字资产'],
  [CANVAS_NODE_TYPES.nsfwFactoryStoryboard]: ['工厂④分镜表'],
};

/** displayName 是否为该类型的某个历史默认名（用户自定义名不受影响）。 */
export function isLegacyDefaultDisplayName(
  type: CanvasNodeType,
  displayName: unknown,
): boolean {
  const legacyNames = LEGACY_DEFAULT_DISPLAY_NAMES[type];
  if (!legacyNames || typeof displayName !== 'string') {
    return false;
  }
  return legacyNames.includes(displayName.trim());
}
