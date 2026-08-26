import type { Node, Edge } from "reactflow";
import type { ScriptGenerateOptions } from "../NodeDetailPanel";

export interface DramaNodeData {
  label: string;
  type: string;
  detail: string;
  imageUrl?: string;
  videoUrl?: string;
  audioUrl?: string;
  subtitleText?: string;
  loading?: boolean;
  loadingText?: string;
  hasGenerated?: boolean;
  onGenerate?: (options?: ScriptGenerateOptions) => void | Promise<void>;
  generateLabel?: string;
  /** 是否可以生成（流程控制：前置条件是否满足） */
  canGenerate?: boolean;
  /** 流程锁定原因（前置条件未满足时的提示） */
  lockReason?: string;
  /** 是否为创意输入节点（内嵌 textarea） */
  isScriptInput?: boolean;
  /** 是否为重新创作入口节点（剧本生成后展示的小型重置节点） */
  isEditInput?: boolean;
  /** 质检摘要文本 */
  qualitySummary?: string;
  /** 质检问题预览 */
  qualityIssues?: string;
  /** 可编辑的提示词（角色节点用） */
  editablePrompts?: { positive: string; negative: string };
  /** 提示词编辑后重新生成回调 */
  onEditPrompts?: (positive: string, negative: string) => void;
  /** 结构化元信息（标签+值），用于在节点内展示关键字段 */
  meta?: { label: string; value: string }[];
  /** 类型标签/状态徽章 */
  tags?: string[];
  /** 长文本预览（场景描述、角色描述等） */
  preview?: string;
  /** 节点状态文本（替代默认的“已完成/等待开始”） */
  statusText?: string;
  /** 节点字段内联更新回调：field 当前支持 label / preview */
  onUpdateField?: (field: "label" | "preview", value: string) => void;
  /** 是否在节点头部显示智能体辅助标识 */
  showAgentAssist?: boolean;
  /** 点击“去详情页编辑”时回调，用于 script input 等需要右侧编辑的场景 */
  onOpenDetail?: () => void;
}

export const NODE_WIDTH = 280;

function isFutureNode(data: DramaNodeData): boolean {
  return !data.hasGenerated && !data.loading && !data.isScriptInput && !data.isEditInput;
}

function baseNodeHeight(node: Node<DramaNodeData>): number {
  const { data } = node;
  const future = isFutureNode(data);
  let h = future ? 86 : 108; // 头部 + 基础内边距
  if (node.id === "start") h = data.isEditInput ? 110 : 380;
  else if (data.isScriptInput) h = 380;
  else {
    if (data.preview) h += future ? 28 : 42; // 2 行 / 3 行预览
    if (data.meta && data.meta.length) {
      const count = future ? Math.min(data.meta.length, 2) : data.meta.length;
      h += 20 * Math.ceil(count / 2) + (future ? 6 : 8);
    }
    if (data.tags && data.tags.length) h += future ? 20 : 24;
    if (data.imageUrl) h += 132;
    else if (["character", "storyboard", "video"].includes(data.type)) h += future ? 78 : 110; // MediaPlaceholder
    if (data.videoUrl) h += 108;
    if (data.audioUrl) h += 44;
    if (data.subtitleText) h += 64;
    if (data.qualitySummary) h += 48;
    if (data.qualityIssues) h += 30;
    if (data.editablePrompts) h += 30;
    if (data.generateLabel && !data.loading) h += future ? 32 : 42;
    // isFutureNode 要求 !loading，此处 future 恒为 false，固定 18
    if (data.loading) h += 18;
  }
  return Math.max(h, future ? 120 : 138);
}

export function nodeHeight(node: Node<DramaNodeData>): number {
  return baseNodeHeight(node);
}

/**
 * 紧凑流程布局：
 * - 创意输入 → 剧本 横向主流程
 * - 角色节点在剧本上方垂直堆叠（与剧本同 X，避免横向扩张）
 * - 场景链（分镜 → 视频 → 配音 → 字幕）在剧本右侧横向延伸，以 centerY 垂直居中
 * - 成片在字幕链右侧，垂直居中对齐场景链
 * - 质检节点在剧本下方，视觉质检在视频链下方
 */
export function getLayoutedElements(nodes: Node<DramaNodeData>[], edges: Edge[]) {
  const gapX = 56;
  const gapY = 44;
  const centerY = 420;
  const verticalGroupGap = 110; // 角色/剧本质检与剧本的纵向间距
  const visualQualityGap = 72; // 视觉质检与视频链的纵向间距（更紧凑）

  const colStart = 40;
  const colScript = colStart + NODE_WIDTH + gapX;
  const colScene = colScript + NODE_WIDTH + gapX;
  const colVideo = colScene + NODE_WIDTH + gapX;
  const colVoice = colVideo + NODE_WIDTH + gapX;
  const colSubtitle = colVoice + NODE_WIDTH + gapX;
  const colEdit = colSubtitle + NODE_WIDTH + gapX;

  const startNode = nodes.find((n) => n.id === "start");
  const scriptNode = nodes.find((n) => n.id === "script");
  const characterNodes = nodes.filter((n) => n.id.startsWith("char-"));
  const sceneNodes = nodes.filter((n) => n.id.startsWith("scene-"));
  const videoNodes = nodes.filter((n) => n.id.startsWith("video-"));
  const voiceNodes = nodes.filter((n) => n.id.startsWith("voice-"));
  const subtitleNodes = nodes.filter((n) => n.id.startsWith("subtitle-"));
  const editNode = nodes.find((n) => n.id === "edit-final");
  const qualityNode = nodes.find((n) => n.id === "quality-final");
  const visualQualityNode = nodes.find((n) => n.id === "visual-quality-final");

  const positions = new Map<string, { x: number; y: number }>();

  if (startNode) {
    positions.set(startNode.id, {
      x: colStart,
      y: centerY - nodeHeight(startNode) / 2,
    });
  }

  if (scriptNode) {
    positions.set(scriptNode.id, {
      x: colScript,
      y: centerY - nodeHeight(scriptNode) / 2,
    });
  }

  // 角色节点：在剧本节点上方以 2 列网格水平居中排列，避免与右侧场景链/剧本重叠
  if (characterNodes.length && scriptNode) {
    const cols = 2;
    const scriptCenterX = colScript + NODE_WIDTH / 2;
    const gridWidth = cols * NODE_WIDTH + (cols - 1) * gapX;
    const gridStartX = scriptCenterX - gridWidth / 2;
    const scriptTopY = centerY - nodeHeight(scriptNode) / 2;
    const charTopY = scriptTopY - verticalGroupGap;
    const rowHeights: number[] = [];
    characterNodes.forEach((node, idx) => {
      const row = Math.floor(idx / cols);
      rowHeights[row] = Math.max(rowHeights[row] || 0, nodeHeight(node));
    });
    const totalGridHeight = rowHeights.reduce((sum, h, i) => sum + h + (i < rowHeights.length - 1 ? gapY : 0), 0);
    characterNodes.forEach((node, idx) => {
      const col = idx % cols;
      const row = Math.floor(idx / cols);
      const rowOffset = rowHeights.slice(0, row).reduce((sum, h) => sum + h + gapY, 0);
      positions.set(node.id, {
        x: gridStartX + col * (NODE_WIDTH + gapX),
        y: charTopY - totalGridHeight + rowOffset,
      });
    });
  }

  // 场景链：按 scene_id 排序后在剧本右侧以 centerY 垂直居中
  const sortedScenes = [...sceneNodes].sort((a, b) => {
    const aid = Number(a.id.split("-")[1]) || 0;
    const bid = Number(b.id.split("-")[1]) || 0;
    return aid - bid;
  });
  const sceneYById = new Map<number, number>();
  if (sortedScenes.length) {
    const totalHeight = sortedScenes.reduce(
      (sum, n) => sum + nodeHeight(n) + gapY,
      0
    ) - gapY;
    let sceneY = centerY - totalHeight / 2;
    sortedScenes.forEach((node) => {
      positions.set(node.id, { x: colScene, y: sceneY });
      const sid = Number(node.id.split("-")[1]) || 0;
      sceneYById.set(sid, sceneY);
      sceneY += nodeHeight(node) + gapY;
    });
  }

  // 视频 / 配音 / 字幕：与对应场景同 Y
  videoNodes.forEach((node) => {
    const sid = Number(node.id.split("-")[1]) || 0;
    positions.set(node.id, { x: colVideo, y: sceneYById.get(sid) ?? centerY });
  });
  voiceNodes.forEach((node) => {
    const sid = Number(node.id.split("-")[1]) || 0;
    positions.set(node.id, { x: colVoice, y: sceneYById.get(sid) ?? centerY });
  });
  subtitleNodes.forEach((node) => {
    const sid = Number(node.id.split("-")[1]) || 0;
    positions.set(node.id, { x: colSubtitle, y: sceneYById.get(sid) ?? centerY });
  });

  // 成片：取字幕链垂直中点
  if (editNode) {
    if (subtitleNodes.length) {
      const firstSid = Number(subtitleNodes[0].id.split("-")[1]) || 0;
      const lastSid = Number(subtitleNodes[subtitleNodes.length - 1].id.split("-")[1]) || 0;
      const firstY = sceneYById.get(firstSid) ?? centerY;
      const lastY = sceneYById.get(lastSid) ?? centerY;
      const midY = (firstY + lastY) / 2 + nodeHeight(subtitleNodes[0]) / 2 - nodeHeight(editNode) / 2;
      positions.set(editNode.id, { x: colEdit, y: midY });
    } else {
      positions.set(editNode.id, {
        x: colEdit,
        y: centerY - nodeHeight(editNode) / 2,
      });
    }
  }

  // 质检节点：位于剧本下方
  if (qualityNode && scriptNode) {
    positions.set(qualityNode.id, {
      x: colScript,
      y: centerY + nodeHeight(scriptNode) / 2 + verticalGroupGap,
    });
  }

  // 视觉质检节点：位于视频链下方
  if (visualQualityNode && videoNodes.length) {
    const sortedVideos = [...videoNodes].sort((a, b) => {
      const aid = Number(a.id.split("-")[1]) || 0;
      const bid = Number(b.id.split("-")[1]) || 0;
      return aid - bid;
    });
    const lastVideo = sortedVideos[sortedVideos.length - 1];
    const lastVideoY = sceneYById.get(Number(lastVideo.id.split("-")[1]) || 0) ?? centerY;
    positions.set(visualQualityNode.id, {
      x: colVideo,
      y: lastVideoY + nodeHeight(lastVideo) + visualQualityGap,
    });
  }

  const layoutedNodes = nodes.map((node) => {
    const pos = positions.get(node.id) ?? { x: 0, y: 0 };
    return {
      ...node,
      // 预置尺寸：React Flow v11 依赖 ResizeObserver 完成测量后才渲染节点与边，
      // 在 rAF 被节流的环境（后台标签页/自动化浏览器）中测量永不完成会导致边消失。
      // 预置 width/height 让节点立即被视为已测量，真实环境下 RO 后续仍会校正。
      width: NODE_WIDTH,
      height: nodeHeight(node),
      position: {
        x: pos.x,
        y: pos.y,
      },
    };
  });

  return { nodes: layoutedNodes, edges };
}
