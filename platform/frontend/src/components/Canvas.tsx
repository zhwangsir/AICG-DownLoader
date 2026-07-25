import { useCallback, useEffect, useState } from "react";
import ReactFlow, {
  Background,
  Controls,
  MiniMap,
  addEdge,
  applyNodeChanges,
  applyEdgeChanges,
  type Node,
  type Edge,
  type OnNodesChange,
  type OnEdgesChange,
  type OnConnect,
  type NodeTypes,
  Handle,
  Position,
} from "reactflow";
import dagre from "dagre";
import "reactflow/dist/style.css";
import { Check, Lock } from "./ui/Icon";
import {
  generateScript,
  generateCharacter,
  generateStoryboard,
  generateStoryboardBatch,
  generateVideoAsync,
  generateVideoBatch,
  generateVoice,
  generateSubtitle,
  composeVideo,
  checkQuality,
  checkVisualQuality,
  type ScriptData,
  type CharacterData,
  type CharacterCardData,
  type SceneData,
  type StoryboardData,
  type VideoData,
  type VoiceData,
  type SubtitleData,
  type EditData,
  type QualityCheckData,
  type QualityVisualData,
  type EditSegmentInput,
  type ProgressEvent,
} from "../api/client";
import { useDramaStore } from "../store/useDramaStore";
import CharacterPreviewPanel from "./CharacterPreviewPanel";
import NodeDetailPanel from "./NodeDetailPanel";

const GENRE_OPTIONS = [
  "都市悬疑",
  "古风仙侠",
  "科幻未来",
  "校园青春",
  "职场商战",
  "武侠江湖",
  "末日废土",
  "温情治愈",
  "犯罪推理",
];

// 支持自定义题材的推荐列表（与下拉框解耦，用户可自由输入）
const GENRE_PRESETS = [
  ...GENRE_OPTIONS,
  "奇幻冒险",
  "家庭伦理",
  "历史穿越",
  "甜宠恋爱",
  "恐怖惊悚",
  "医疗救援",
  "体育竞技",
  "美食治愈",
  "商战复仇",
];

interface DramaNodeData {
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
  onGenerate?: (premise?: string, genre?: string) => void;
  generateLabel?: string;
  /** 是否可以生成（流程控制：前置条件是否满足） */
  canGenerate?: boolean;
  /** 流程锁定原因（前置条件未满足时的提示） */
  lockReason?: string;
  /** 是否为创意输入节点（内嵌 textarea） */
  isScriptInput?: boolean;
  /** 质检摘要文本 */
  qualitySummary?: string;
  /** 质检问题预览 */
  qualityIssues?: string;
  /** 可编辑的提示词（角色节点用） */
  editablePrompts?: { positive: string; negative: string };
  /** 提示词编辑后重新生成回调 */
  onEditPrompts?: (positive: string, negative: string) => void;
}

const nodeTypes: NodeTypes = {
  custom: DramaNode,
};

/** 深色主题内联样式片段 */
const btnStyle = {
  padding: "4px 10px",
  fontSize: "12px",
  background: "var(--accent-dim)",
  border: "1px solid var(--border)",
  color: "var(--text-primary)",
  borderRadius: "4px",
  cursor: "pointer",
  width: "100%",
} as const;

const inputStyle = {
  width: "100%",
  padding: "4px 6px",
  background: "var(--bg-primary)",
  border: "1px solid var(--border)",
  borderRadius: "4px",
  color: "var(--text-primary)",
  fontSize: "12px",
  fontFamily: "inherit",
} as const;

function DramaNode({ data }: { data: DramaNodeData }) {
  // 阻止节点内部交互元素触发 React Flow 拖拽
  const stopNodeDrag: React.MouseEventHandler<HTMLElement> = (e) => {
    e.stopPropagation();
  };
  // 全局生成锁：任意 Agent 生成中时，本节点所有可能触发生成的按钮禁用
  const globalLoading = useDramaStore((s) => s.globalLoading);
  const colorMap: Record<string, string> = {
    script: "var(--node-script)",
    character: "var(--node-character)",
    storyboard: "var(--node-storyboard)",
    video: "var(--node-video)",
    voice: "var(--node-voice)",
    subtitle: "var(--node-subtitle)",
    edit: "var(--node-edit)",
    quality: "var(--node-quality)",
    visual_quality: "var(--node-visual-quality)",
  };
  const color = colorMap[data.type] || "var(--accent-dim)";

  // 创意输入节点的本地输入状态
  const [premise, setPremise] = useState("都市悬疑，外卖员发现客户是凶手");
  const [genre, setGenre] = useState("都市悬疑");

  // 提示词编辑面板状态
  const [showEditPanel, setShowEditPanel] = useState(false);
  const [editPositive, setEditPositive] = useState("");
  const [editNegative, setEditNegative] = useState("");

  // 已生成节点也显示“重新生成”按钮；生成中时不显示按钮（由 loading 提示替代）
  const showGenerateBtn = !!data.generateLabel && !data.loading;
  // 流程前置条件未满足 或 全局生成锁 都会锁定按钮
  const isLocked = showGenerateBtn && (data.canGenerate === false || globalLoading);

  // 打开编辑面板时，用当前提示词填充
  const openEditPanel = () => {
    setEditPositive(data.editablePrompts?.positive || "");
    setEditNegative(data.editablePrompts?.negative || "");
    setShowEditPanel(true);
  };

  // 应用编辑并重新生成
  const applyEditAndRegenerate = () => {
    setShowEditPanel(false);
    data.onEditPrompts?.(editPositive, editNegative);
  };

  return (
    <div
      className="react-flow__node-custom"
      style={
        data.loading
          ? { boxShadow: `0 0 0 2px ${color}`, borderColor: color }
          : undefined
      }
    >
      <Handle type="target" position={Position.Left} />
      <div className="node-header">
        <span className="node-dot" style={{ background: color }}></span>
        {data.label}
      </div>
      <div className="node-body">{data.detail}</div>

      {/* 创意输入：textarea + 题材下拉 */}
      {data.isScriptInput && !data.hasGenerated && !data.loading && (
        <div style={{ marginTop: "6px", display: "flex", flexDirection: "column", gap: "4px" }}>
          <textarea
            value={premise}
            onChange={(e) => setPremise(e.target.value)}
            onMouseDown={stopNodeDrag}
            onClick={stopNodeDrag}
            placeholder="输入一句话创意..."
            style={{ ...inputStyle, minHeight: "48px", resize: "vertical" }}
            className="nodrag"
          />
          <input
            list="genre-presets"
            value={genre}
            onChange={(e) => setGenre(e.target.value)}
            onMouseDown={stopNodeDrag}
            onClick={stopNodeDrag}
            placeholder="输入题材或选择推荐项"
            style={inputStyle}
            className="nodrag"
          />
          <datalist id="genre-presets">
            {GENRE_PRESETS.map((g) => (
              <option key={g} value={g} />
            ))}
          </datalist>
        </div>
      )}

      {/* 图片预览 */}
      {data.imageUrl && (
        <img
          src={data.imageUrl}
          alt={data.label}
          loading="lazy"
          style={{
            width: "100%",
            maxHeight: "140px",
            objectFit: "cover",
            borderRadius: "4px",
            marginTop: "6px",
            display: "block",
          }}
        />
      )}

      {/* 视频预览 */}
      {data.videoUrl && (
        <video
          src={data.videoUrl}
          controls
          loop
          muted
          style={{
            width: "100%",
            maxHeight: "180px",
            borderRadius: "4px",
            marginTop: "6px",
            display: "block",
          }}
        />
      )}

      {/* 音频预览 */}
      {data.audioUrl && (
        <audio
          controls
          src={data.audioUrl}
          style={{ width: "100%", marginTop: "6px", display: "block" }}
        />
      )}

      {/* 字幕预览 */}
      {data.subtitleText && (
        <div
          style={{
            marginTop: "6px",
            padding: "4px 6px",
            background: "rgba(74,165,165,0.12)",
            borderRadius: "4px",
            fontSize: "11px",
            lineHeight: "1.4",
            maxHeight: "80px",
            overflow: "auto",
          }}
        >
          {data.subtitleText}
        </div>
      )}

      {/* 质检摘要 */}
      {data.qualitySummary && (
        <div
          style={{
            marginTop: "6px",
            padding: "4px 6px",
            background: "rgba(165,165,74,0.12)",
            borderRadius: "4px",
            fontSize: "11px",
            lineHeight: "1.4",
          }}
        >
          {data.qualitySummary}
        </div>
      )}
      {data.qualityIssues && (
        <div
          style={{
            marginTop: "4px",
            fontSize: "11px",
            color: "var(--text-secondary)",
            maxHeight: "70px",
            overflow: "auto",
          }}
        >
          {data.qualityIssues}
        </div>
      )}

      {/* Loading 提示 */}
      {data.loading && (
        <div
          style={{
            marginTop: "6px",
            display: "flex",
            alignItems: "center",
            gap: "6px",
            fontSize: "12px",
            color: "var(--accent)",
          }}
        >
          <span className="loading" style={{ width: "12px", height: "12px" }}></span>
          {data.loadingText || "生成中..."}
        </div>
      )}

      {/* 已生成标记 */}
      {data.hasGenerated && !data.loading && (
        <div
          style={{
            marginTop: "6px",
            fontSize: "11px",
            color: "var(--node-storyboard)",
            display: "flex",
            alignItems: "center",
            gap: "4px",
          }}
        >
          <Check size={12} strokeWidth={2.5} />
          <span>已生成</span>
        </div>
      )}

      {/* 编辑提示词按钮（仅已生成且有可编辑提示词时显示） */}
      {data.hasGenerated && !data.loading && data.editablePrompts && data.onEditPrompts && !showEditPanel && (
        <button
          style={{
            ...btnStyle,
            marginTop: "4px",
            background: "transparent",
            border: "1px solid var(--border)",
            fontSize: "11px",
          }}
          onClick={(e) => {
            e.stopPropagation();
            openEditPanel();
          }}
          onMouseDown={stopNodeDrag}
          onMouseOver={(e) => {
            (e.currentTarget as HTMLButtonElement).style.borderColor = "var(--accent)";
          }}
          onMouseOut={(e) => {
            (e.currentTarget as HTMLButtonElement).style.borderColor = "var(--border)";
          }}
          className="nodrag"
        >
          编辑提示词
        </button>
      )}

      {/* 提示词编辑面板 */}
      {showEditPanel && (
        <div
          onClick={stopNodeDrag}
          style={{
            marginTop: "6px",
            padding: "6px",
            background: "var(--bg-primary)",
            borderRadius: "4px",
            border: "1px solid var(--border)",
            display: "flex",
            flexDirection: "column",
            gap: "4px",
          }}
        >
          <div style={{ fontSize: "10px", color: "var(--text-secondary)" }}>
            正面提示词
          </div>
          <textarea
            value={editPositive}
            onChange={(e) => setEditPositive(e.target.value)}
            onMouseDown={stopNodeDrag}
            style={{
              ...inputStyle,
              minHeight: "48px",
              resize: "vertical",
              fontSize: "10px",
            }}
            className="nodrag"
          />
          <div style={{ fontSize: "10px", color: "var(--text-secondary)" }}>
            负面提示词
          </div>
          <textarea
            value={editNegative}
            onChange={(e) => setEditNegative(e.target.value)}
            onMouseDown={stopNodeDrag}
            style={{
              ...inputStyle,
              minHeight: "36px",
              resize: "vertical",
              fontSize: "10px",
            }}
            className="nodrag"
          />
          <div style={{ display: "flex", gap: "4px", marginTop: "2px" }}>
            <button
              style={{
                ...btnStyle,
                fontSize: "11px",
                padding: "3px 8px",
                width: "auto",
                flex: 1,
                ...(globalLoading
                  ? { opacity: 0.4, cursor: "not-allowed", background: "#333" }
                  : {}),
              }}
              disabled={globalLoading}
              onClick={(e) => {
                e.stopPropagation();
                applyEditAndRegenerate();
              }}
              onMouseDown={stopNodeDrag}
              className="nodrag"
            >
              {globalLoading ? "其他任务生成中..." : "应用并重新生成"}
            </button>
            <button
              style={{
                fontSize: "11px",
                padding: "3px 8px",
                background: "transparent",
                border: "1px solid var(--border)",
                color: "var(--text-secondary)",
                borderRadius: "4px",
                cursor: "pointer",
                width: "auto",
              }}
              onClick={(e) => {
                e.stopPropagation();
                setShowEditPanel(false);
              }}
              onMouseDown={stopNodeDrag}
              className="nodrag"
            >
              取消
            </button>
          </div>
        </div>
      )}

      {/* 流程锁定提示 */}
      {isLocked && data.lockReason && (
        <div
          style={{
            marginTop: "6px",
            fontSize: "10px",
            color: "#888",
            padding: "4px 6px",
            background: "rgba(255,255,255,0.03)",
            borderRadius: "4px",
            border: "1px solid #333",
            display: "flex",
            alignItems: "center",
            gap: "4px",
          }}
        >
          <Lock size={10} strokeWidth={2} />
          <span>{data.lockReason}</span>
        </div>
      )}

      {/* 一键生成按钮 */}
      {showGenerateBtn && (
        <button
          style={{
            ...btnStyle,
            marginTop: "6px",
            ...(isLocked
              ? { opacity: 0.4, cursor: "not-allowed", background: "#333" }
              : {}),
          }}
          disabled={isLocked}
          onClick={(e) => {
            e.stopPropagation();
            if (!isLocked) data.onGenerate?.(premise, genre);
          }}
          onMouseDown={stopNodeDrag}
          onMouseOver={(e) => {
            if (!isLocked)
              (e.currentTarget as HTMLButtonElement).style.background = "var(--accent)";
          }}
          onMouseOut={(e) => {
            if (!isLocked)
              (e.currentTarget as HTMLButtonElement).style.background = "var(--accent-dim)";
          }}
          className="nodrag"
        >
          {data.generateLabel}
        </button>
      )}

      <Handle type="source" position={Position.Right} />
    </div>
  );
}

/** 视频异步任务轮询：每 3 秒查询一次，直到完成或失败 */
async function pollVideoTask(pollUrl: string): Promise<ProgressEvent> {
  while (true) {
    await new Promise((resolve) => setTimeout(resolve, 3000));
    const resp = await fetch(pollUrl);
    if (!resp.ok) {
      throw new Error(`轮询失败: ${resp.status}`);
    }
    const evt: ProgressEvent = await resp.json();
    if (evt.status === "completed" || evt.status === "failed") {
      return evt;
    }
  }
}

const NODE_WIDTH = 280;

function nodeHeight(node: Node<DramaNodeData>): number {
  if (node.id === "start") return 280;
  if (node.data.videoUrl) return 240;
  if (node.data.imageUrl) return 220;
  if (node.data.audioUrl) return 170;
  if (node.data.qualitySummary) return 180;
  return 160;
}

function getLayoutedElements(nodes: Node<DramaNodeData>[], edges: Edge[]) {
  const g = new dagre.graphlib.Graph();
  g.setDefaultEdgeLabel(() => ({}));
  g.setGraph({ rankdir: "LR", nodesep: 60, ranksep: 120 });

  nodes.forEach((node) => {
    g.setNode(node.id, { width: NODE_WIDTH, height: nodeHeight(node) });
  });

  edges.forEach((edge) => {
    g.setEdge(edge.source, edge.target);
  });

  dagre.layout(g);

  const layoutedNodes = nodes.map((node) => {
    const layoutNode = g.node(node.id);
    return {
      ...node,
      position: {
        x: layoutNode.x - NODE_WIDTH / 2,
        y: layoutNode.y - nodeHeight(node) / 2,
      },
    };
  });

  return { nodes: layoutedNodes, edges };
}

export default function Canvas() {
  // 从 store 获取所有数据与方法
  const scriptData = useDramaStore((s) => s.scriptData);
  const storyboards = useDramaStore((s) => s.storyboards);
  const videos = useDramaStore((s) => s.videos);
  const voices = useDramaStore((s) => s.voices);
  const subtitles = useDramaStore((s) => s.subtitles);
  const editData = useDramaStore((s) => s.editData);
  const qualityData = useDramaStore((s) => s.qualityData);
  const visualQualityData = useDramaStore((s) => s.visualQualityData);
  const setScriptData = useDramaStore((s) => s.setScriptData);
  const addStoryboard = useDramaStore((s) => s.addStoryboard);
  const addVideo = useDramaStore((s) => s.addVideo);
  const addVoice = useDramaStore((s) => s.addVoice);
  const addSubtitle = useDramaStore((s) => s.addSubtitle);
  const setEditData = useDramaStore((s) => s.setEditData);
  const setQualityData = useDramaStore((s) => s.setQualityData);
  const setVisualQualityData = useDramaStore((s) => s.setVisualQualityData);
  const setStatusInfo = useDramaStore((s) => s.setStatusInfo);
  const globalLoading = useDramaStore((s) => s.globalLoading);
  const globalLoadingText = useDramaStore((s) => s.globalLoadingText);
  const startGlobalLoading = useDramaStore((s) => s.startGlobalLoading);
  const stopGlobalLoading = useDramaStore((s) => s.stopGlobalLoading);

  // 当前打开预览面板的角色 ID
  const [activePreviewCharacterId, setActivePreviewCharacterId] = useState<string | null>(null);
  // 当前打开的通用节点详情面板
  const [activeDetailNode, setActiveDetailNode] = useState<{
    id: string;
    type: string;
    onGenerate?: () => void;
  } | null>(null);

  const [nodes, setNodes] = useState<Node<DramaNodeData>[]>([
    {
      id: "start",
      type: "custom",
      position: { x: 100, y: 200 },
      data: {
        label: "创意输入",
        type: "script",
        detail: "输入一句话创意，一键生成剧本",
        isScriptInput: true,
        generateLabel: "生成剧本",
      },
    },
  ]);
  const [edges, setEdges] = useState<Edge[]>([]);

  // loading 状态管理
  const [loadingMap, setLoadingMap] = useState<
    Record<string, { loading: boolean; text: string }>
  >({});

  // 角色定妆照与提示词从持久化的 store 读取，刷新后状态不丢失
  const characterCards = useDramaStore((s) => s.characterCards);
  const addCharacterCard = useDramaStore((s) => s.addCharacterCard);
  const characterCardImages = Object.fromEntries(
    characterCards.map((c) => {
      const imgs = c.reference_images || {};
      const firstUrl = imgs.front || imgs.portrait || Object.values(imgs)[0] || "";
      return [c.character_id, firstUrl];
    })
  );
  const characterPrompts = Object.fromEntries(
    characterCards
      .filter((c) => c.used_prompts)
      .map((c) => [
        c.character_id,
        {
          positive: c.used_prompts!.positive_prompt,
          negative: c.used_prompts!.negative_prompt,
        },
      ])
  );

  const setLoading = useCallback((id: string, text: string) => {
    setLoadingMap((prev) => ({ ...prev, [id]: { loading: true, text } }));
  }, []);

  const clearLoading = useCallback((id: string) => {
    setLoadingMap((prev) => {
      const next = { ...prev };
      delete next[id];
      return next;
    });
  }, []);

  const onNodesChange: OnNodesChange = useCallback(
    (changes) => setNodes((nds) => applyNodeChanges(changes, nds)),
    []
  );

  const onEdgesChange: OnEdgesChange = useCallback(
    (changes) => setEdges((eds) => applyEdgeChanges(changes, eds)),
    []
  );

  const onConnect: OnConnect = useCallback(
    (connection) => setEdges((eds) => addEdge(connection, eds)),
    []
  );

  // 批量生成所有缺失分镜，利用多 GPU 并行
  const handleGenerateAllStoryboards = useCallback(async () => {
    if (globalLoading || !scriptData) return;
    const pending = scriptData.scenes.filter(
      (s) => !storyboards.some((sb) => sb.scene_id === s.scene_id)
    );
    if (pending.length === 0) {
      setStatusInfo("所有分镜已生成");
      return;
    }
    startGlobalLoading(`正在批量生成分镜（${pending.length} 个场景）...`);
    pending.forEach((s) => setLoading(`scene-${s.scene_id}`, "批量生成分镜中..."));
    setStatusInfo(`正在批量生成分镜（${pending.length} 个场景），多 GPU 并行执行...`);
    try {
      const resp = await generateStoryboardBatch({
        scenes: pending,
        characters: scriptData.characters,
        style: "写实电影感",
      });
      if (resp.success && resp.data) {
        resp.data.results.forEach((r) => addStoryboard(r));
        const failed = resp.data.failed_scenes;
        setStatusInfo(
          `分镜批量生成完成: ${resp.data.results.length} 成功` +
            (failed.length ? `, ${failed.length} 失败` : "")
        );
      } else {
        setStatusInfo(`分镜批量生成失败: ${resp.error || "未知错误"}`);
      }
    } catch (e) {
      setStatusInfo(`分镜批量生成出错: ${String(e)}`);
    } finally {
      pending.forEach((s) => clearLoading(`scene-${s.scene_id}`));
      stopGlobalLoading();
    }
  }, [
    globalLoading,
    scriptData,
    storyboards,
    setStatusInfo,
    startGlobalLoading,
    stopGlobalLoading,
    setLoading,
    clearLoading,
    addStoryboard,
  ]);

  // 批量生成所有缺失视频，利用多 GPU 并行
  const handleGenerateAllVideos = useCallback(async () => {
    if (globalLoading || !scriptData) return;
    const pending = scriptData.scenes
      .map((s) => {
        const sb = storyboards.find((x) => x.scene_id === s.scene_id);
        const vd = videos.find((x) => x.scene_id === s.scene_id);
        if (!sb || vd) return null;
        return {
          scene_id: s.scene_id,
          image_url: sb.image_url,
          prompt: s.prompt || sb.prompt_used,
          negative_prompt: s.negative_prompt,
        };
      })
      .filter(
        (x): x is { scene_id: number; image_url: string; prompt: string; negative_prompt: string } =>
          !!x
      );
    if (pending.length === 0) {
      setStatusInfo("所有视频已生成");
      return;
    }
    startGlobalLoading(`正在批量生成视频（${pending.length} 个场景）...`);
    pending.forEach((p) => setLoading(`video-${p.scene_id}`, "批量生成视频中..."));
    setStatusInfo(`正在批量生成视频（${pending.length} 个场景），多 GPU 并行执行...`);
    try {
      const resp = await generateVideoBatch({
        items: pending.map((p) => ({
          scene_id: p.scene_id,
          image_url: p.image_url,
          prompt: p.prompt,
          negative_prompt:
            p.negative_prompt ||
            "blurry, low quality, deformed, ugly, watermark, static",
          duration_seconds: 3,
        })),
      });
      if (resp.success && resp.data) {
        resp.data.results.forEach((r) => addVideo(r));
        const failed = resp.data.failed_scenes;
        setStatusInfo(
          `视频批量生成完成: ${resp.data.results.length} 成功` +
            (failed.length ? `, ${failed.length} 失败` : "")
        );
      } else {
        setStatusInfo(`视频批量生成失败: ${resp.error || "未知错误"}`);
      }
    } catch (e) {
      setStatusInfo(`视频批量生成出错: ${String(e)}`);
    } finally {
      pending.forEach((p) => clearLoading(`video-${p.scene_id}`));
      stopGlobalLoading();
    }
  }, [
    globalLoading,
    scriptData,
    storyboards,
    videos,
    setStatusInfo,
    startGlobalLoading,
    stopGlobalLoading,
    setLoading,
    clearLoading,
    addVideo,
  ]);

  // 剧本/分镜/视频/配音/字幕/成片/质检数据变化时重建节点图
  useEffect(() => {
    // ---- 一键生成处理函数（在 effect 内定义，保证闭包捕获最新数据） ----

    // 创意输入 → 生成剧本
    const handleGenerateScript = async (premise?: string, genre?: string) => {
      if (globalLoading) return;
      const p = premise || "";
      const g = genre || "都市悬疑";
      if (!p.trim()) {
        setStatusInfo("请输入创意");
        return;
      }
      startGlobalLoading("正在生成剧本...");
      setLoading("start", "正在生成剧本...");
      setStatusInfo("正在生成剧本...");
      try {
        const resp = await generateScript({
          premise: p,
          genre: g,
          episodes: 1,
          scenes_per_episode: 3,
        });
        if (resp.success && resp.data) {
          setScriptData(resp.data);
          setStatusInfo(
            `剧本已生成: ${resp.data.title} | ${resp.data.characters.length} 角色 | ${resp.data.scenes.length} 分镜`
          );
        } else {
          setStatusInfo(`剧本生成失败: ${resp.error || "未知错误"}`);
        }
      } catch (e) {
        setStatusInfo(`剧本生成出错: ${String(e)}`);
      } finally {
        clearLoading("start");
        stopGlobalLoading();
      }
    };

    // 角色 → 打开预览面板（默认流程）或直接生成（节点上自定义提示词重新生成）
    const handleGenerateCharacter = (
      char: CharacterData,
      customPositive?: string,
      customNegative?: string
    ) => {
      if (customPositive && customPositive.trim()) {
        // 节点上直接编辑提示词后重新生成：绕过预览，直接生成
        generateCharacterDirect(char, customPositive, customNegative || "");
        return;
      }
      // 正常流程：打开预览面板，先 AI 调研，再用户确认生成
      setActivePreviewCharacterId(char.character_id);
    };

    // 角色 → 直接生成定妆照（自定义提示词模式）
    const generateCharacterDirect = async (
      char: CharacterData,
      customPositive: string,
      customNegative: string
    ) => {
      if (globalLoading) return;
      const nodeId = `char-${char.character_id}`;
      startGlobalLoading(`正在生成 ${char.name} 的定妆照...`);
      setLoading(nodeId, "生成定妆照中...");
      setStatusInfo(`正在生成角色定妆照: ${char.name}...`);
      try {
        const resp = await generateCharacter({
          character: char,
          style: "写实电影感",
          consistency_level: "L3",
          custom_positive_prompt: customPositive,
          custom_negative_prompt: customNegative,
        });
        if (resp.success && resp.data) {
          applyCharacterResult(char.character_id, resp.data);
          setStatusInfo(`角色定妆照已生成: ${char.name}`);
        } else {
          setStatusInfo(`定妆照生成失败: ${resp.error || "未知错误"}`);
        }
      } catch (e) {
        setStatusInfo(`定妆照生成出错: ${String(e)}`);
      } finally {
        clearLoading(nodeId);
        stopGlobalLoading();
      }
    };

    // 应用角色生成结果到 store（持久化）
    const applyCharacterResult = (charId: string, data: CharacterCardData) => {
      addCharacterCard(data);
    };

    // 场景 → 生成分镜
    const handleGenerateStoryboard = async (scene: SceneData) => {
      if (globalLoading) return;
      const nodeId = `scene-${scene.scene_id}`;
      startGlobalLoading(`正在生成分镜: 场景 ${scene.scene_id}...`);
      setLoading(nodeId, "生成分镜中...");
      setStatusInfo(`正在生成分镜: 场景 ${scene.scene_id}...`);
      try {
        const resp = await generateStoryboard({
          scene,
          characters: scriptData?.characters || [],
          style: "写实电影感",
        });
        if (resp.success && resp.data) {
          addStoryboard(resp.data);
          setStatusInfo(`分镜关键帧已生成: 场景 ${scene.scene_id}`);
        } else {
          setStatusInfo(`分镜生成失败: ${resp.error || "未知错误"}`);
        }
      } catch (e) {
        setStatusInfo(`分镜生成出错: ${String(e)}`);
      } finally {
        clearLoading(nodeId);
        stopGlobalLoading();
      }
    };

    // 分镜 → 生成视频（异步 + 轮询）
    const handleGenerateVideo = async (
      sceneId: number,
      imageUrl: string,
      prompt: string,
      negativePrompt: string
    ) => {
      if (globalLoading) return;
      const nodeId = `video-${sceneId}`;
      startGlobalLoading(`正在生成视频: 场景 ${sceneId}...`);
      setLoading(nodeId, "生成视频中...");
      setStatusInfo(`正在生成视频: 场景 ${sceneId}...`);
      try {
        const task = await generateVideoAsync({
          scene_id: sceneId,
          image_url: imageUrl,
          prompt,
          negative_prompt:
            negativePrompt ||
            "blurry, low quality, deformed, ugly, watermark, static",
          duration_seconds: 3,
        });
        const evt = await pollVideoTask(task.poll_url);
        if (
          evt.status === "completed" &&
          evt.result &&
          typeof evt.result === "object" &&
          "video_url" in evt.result
        ) {
          const vd = evt.result as VideoData;
          addVideo(vd);
          setStatusInfo(
            `视频已生成: 场景 ${sceneId} (${vd.duration_seconds}s)`
          );
        } else {
          setStatusInfo(
            `视频生成失败: ${evt.error || "未知错误"}`
          );
        }
      } catch (e) {
        setStatusInfo(`视频生成出错: ${String(e)}`);
      } finally {
        clearLoading(nodeId);
        stopGlobalLoading();
      }
    };

    // 视频 → 生成配音（自动从场景台词提取对白）
    const handleGenerateVoice = async (scene: SceneData) => {
      if (globalLoading) return;
      const nodeId = `voice-${scene.scene_id}`;
      if (!scene.dialogue) {
        setStatusInfo(`场景 ${scene.scene_id} 没有台词，无法生成配音`);
        return;
      }
      startGlobalLoading(`正在生成配音: 场景 ${scene.scene_id}...`);
      setLoading(nodeId, "生成配音中...");
      setStatusInfo(`正在生成配音: 场景 ${scene.scene_id}...`);
      try {
        const chars = scriptData?.characters || [];
        const lines = scene.dialogue
          .split(/[，。！？\n]/)
          .map((t) => t.trim())
          .filter((t) => t.length > 1);
        const dialogues = lines.map((text, i) => {
          const speaker = chars[i % Math.max(chars.length, 1)];
          return {
            text,
            character_name: speaker?.name || `角色${i + 1}`,
            character_role: speaker?.role || "",
            character_age: speaker?.age ?? null,
            rate: "+0%",
          };
        });
        const resp = await generateVoice({
          scene_id: scene.scene_id,
          dialogues,
        });
        if (resp.success && resp.data) {
          addVoice(resp.data);
          setStatusInfo(
            `配音已生成: 场景 ${scene.scene_id} (${resp.data.total_lines} 条语音)`
          );
        } else {
          setStatusInfo(`配音生成失败: ${resp.error || "未知错误"}`);
        }
      } catch (e) {
        setStatusInfo(`配音生成出错: ${String(e)}`);
      } finally {
        clearLoading(nodeId);
        stopGlobalLoading();
      }
    };

    // 配音 → 生成字幕
    const handleGenerateSubtitle = async (
      sceneId: number,
      audioUrl: string
    ) => {
      if (globalLoading) return;
      const nodeId = `subtitle-${sceneId}`;
      startGlobalLoading(`正在生成字幕: 场景 ${sceneId}...`);
      setLoading(nodeId, "生成字幕中...");
      setStatusInfo(`正在生成字幕: 场景 ${sceneId}...`);
      try {
        const resp = await generateSubtitle({
          scene_id: sceneId,
          audio_url: audioUrl,
          language: "zh",
        });
        if (resp.success && resp.data) {
          addSubtitle(resp.data);
          setStatusInfo(
            `字幕已生成: 场景 ${sceneId} (${resp.data.segments.length} 段)`
          );
        } else {
          setStatusInfo(`字幕生成失败: ${resp.error || "未知错误"}`);
        }
      } catch (e) {
        setStatusInfo(`字幕生成出错: ${String(e)}`);
      } finally {
        clearLoading(nodeId);
        stopGlobalLoading();
      }
    };

    // 合成成片
    const handleComposeVideo = async () => {
      if (globalLoading) return;
      const nodeId = "edit-final";
      const readyScenes: EditSegmentInput[] = videos
        .map((v) => {
          const voice = voices.find((vo) => vo.scene_id === v.scene_id);
          const subtitle = subtitles.find((s) => s.scene_id === v.scene_id);
          if (!voice || voice.audio_urls.length === 0 || !subtitle) return null;
          return {
            scene_id: v.scene_id,
            video_url: v.video_url,
            audio_url: voice.audio_urls[0].audio_url,
            subtitle_url: subtitle.srt_url,
          };
        })
        .filter((s): s is EditSegmentInput => s !== null);

      if (readyScenes.length === 0) {
        setStatusInfo("没有完整素材的场景（需视频+配音+字幕）");
        return;
      }
      startGlobalLoading(`正在合成成片（${readyScenes.length} 个场景）...`);
      setLoading(nodeId, "合成成片中...");
      setStatusInfo(`正在合成成片（${readyScenes.length} 个场景）...`);
      try {
        const resp = await composeVideo({
          project_id: scriptData?.project_id || `project-${Date.now()}`,
          title: scriptData?.title || "未命名短剧",
          segments: readyScenes,
          transition: "fade",
          bgm_url: null,
          output_resolution: "1080x1920",
          output_fps: 30,
        });
        if (resp.success && resp.data) {
          setEditData(resp.data);
          setStatusInfo(
            `成片已合成: ${resp.data.title} | ${resp.data.segments_count} 场景 | ${resp.data.duration_seconds.toFixed(1)}s`
          );
        } else {
          setStatusInfo(`合成失败: ${resp.error || "未知错误"}`);
        }
      } catch (e) {
        setStatusInfo(`合成出错: ${String(e)}`);
      } finally {
        clearLoading(nodeId);
        stopGlobalLoading();
      }
    };

    // 一键质检
    const handleCheckQuality = async () => {
      if (globalLoading) return;
      const nodeId = "quality-final";
      if (!scriptData) return;
      startGlobalLoading("正在执行剧本质检...");
      setLoading(nodeId, "质检中...");
      setStatusInfo("正在执行剧本质检...");
      try {
        const resp = await checkQuality({
          project_id: scriptData.project_id || `project-${Date.now()}`,
          title: scriptData.title,
          characters: scriptData.characters,
          scenes: scriptData.scenes,
          subtitles,
        });
        if (resp.success && resp.data) {
          setQualityData(resp.data);
          const critical = resp.data.issues.filter(
            (i) => i.severity === "critical"
          ).length;
          setStatusInfo(
            `质检完成: 质量分 ${resp.data.score} | ${resp.data.issues.length} 问题 | ${critical} 严重`
          );
        } else {
          setStatusInfo(`质检失败: ${resp.error || "未知错误"}`);
        }
      } catch (e) {
        setStatusInfo(`质检出错: ${String(e)}`);
      } finally {
        clearLoading(nodeId);
        stopGlobalLoading();
      }
    };

    // 视觉质检
    const handleCheckVisualQuality = async () => {
      if (globalLoading) return;
      const nodeId = "visual-quality-final";
      const targetVideo = videos[0];
      if (!targetVideo) return;
      startGlobalLoading(`正在执行视觉质检: 场景 ${targetVideo.scene_id}...`);
      setLoading(nodeId, "视觉质检中...");
      setStatusInfo(`正在执行视觉质检: 场景 ${targetVideo.scene_id}...`);
      try {
        const resp = await checkVisualQuality({
          project_id: scriptData?.project_id || `project-${Date.now()}`,
          title: scriptData?.title || "未命名短剧",
          scene_id: targetVideo.scene_id,
          video_url: targetVideo.video_url,
          max_frames: 6,
        });
        if (resp.success && resp.data) {
          setVisualQualityData(resp.data);
          const critical = resp.data.issues.filter(
            (i) => i.severity === "critical"
          ).length;
          setStatusInfo(
            `视觉质检完成: 场景 ${resp.data.scene_id} | 质量分 ${resp.data.score} | ${critical} 严重`
          );
        } else {
          setStatusInfo(`视觉质检失败: ${resp.error || "未知错误"}`);
        }
      } catch (e) {
        setStatusInfo(`视觉质检出错: ${String(e)}`);
      } finally {
        clearLoading(nodeId);
        stopGlobalLoading();
      }
    };

    // ---- 构建节点 ----
    const loadingFor = (id: string) => loadingMap[id]?.loading || false;
    const loadingTextFor = (id: string) => loadingMap[id]?.text || "";

    const newNodes: Node<DramaNodeData>[] = [
      {
        id: "start",
        type: "custom",
        position: { x: 100, y: 200 },
        data: {
          label: "创意输入",
          type: "script",
          detail: scriptData
            ? `已生成: ${scriptData.title}`
            : "输入一句话创意，一键生成剧本",
          isScriptInput: true,
          hasGenerated: !!scriptData,
          generateLabel: scriptData ? "重新生成剧本" : "生成剧本",
          loading: loadingFor("start"),
          loadingText: loadingTextFor("start"),
          onGenerate: handleGenerateScript,
        },
      },
    ];

    const newEdges: Edge[] = [];

    if (scriptData) {
      // 剧本节点
      newNodes.push({
        id: "script",
        type: "custom",
        position: { x: 400, y: 100 },
        data: {
          label: `剧本: ${scriptData.title}`,
          type: "script",
          detail: `${scriptData.total_episodes} 集 | ${scriptData.scenes.length} 分镜 | ${scriptData.characters.length} 角色`,
          hasGenerated: true,
        },
      });
      newEdges.push({ id: "e-start-script", source: "start", target: "script" });

      // 角色节点
      scriptData.characters.forEach((char) => {
        const charId = `char-${char.character_id}`;
        const img = characterCardImages[char.character_id];
        const prompts = characterPrompts[char.character_id];
        newNodes.push({
          id: charId,
          type: "custom",
          position: { x: 700, y: 100 },
          data: {
            label: `角色: ${char.name}`,
            type: "character",
            detail: `${char.role} | ${char.age || "?"}岁`,
            imageUrl: img,
            hasGenerated: !!img,
            generateLabel: img ? "重新生成定妆照" : "生成定妆照",
            loading: loadingFor(charId),
            loadingText: loadingTextFor(charId),
            onGenerate: () => handleGenerateCharacter(char),
            // 已生成时提供提示词编辑能力
            ...(prompts
              ? {
                  editablePrompts: prompts,
                  onEditPrompts: (positive: string, negative: string) =>
                    handleGenerateCharacter(char, positive, negative),
                }
              : {}),
          },
        });
        newEdges.push({
          id: `e-script-${charId}`,
          source: "script",
          target: charId,
        });
      });

      // 分镜/视频/配音/字幕节点
      const storyboardMap = new Map(storyboards.map((s) => [s.scene_id, s]));
      const videoMap = new Map(videos.map((v) => [v.scene_id, v]));
      const voiceMap = new Map(voices.map((v) => [v.scene_id, v]));
      const subtitleMap = new Map(subtitles.map((s) => [s.scene_id, s]));

      // 流程控制：所有角色定妆照生成后才能生成分镜
      const allCharactersHaveImages = scriptData.characters.every(
        (c) => !!characterCardImages[c.character_id]
      );

      scriptData.scenes.slice(0, 5).forEach((scene) => {
        const sb = storyboardMap.get(scene.scene_id);
        const sceneId = `scene-${scene.scene_id}`;
        newNodes.push({
          id: sceneId,
          type: "custom",
          position: { x: 1000, y: 100 },
          data: {
            label: `分镜 ${scene.scene_id}: ${scene.shot_type}`,
            type: "storyboard",
            detail:
              scene.description.length > 40
                ? scene.description.slice(0, 40) + "..."
                : scene.description,
            imageUrl: sb?.image_url,
            hasGenerated: !!sb,
            generateLabel: sb ? "重新生成分镜" : "生成分镜",
            canGenerate: allCharactersHaveImages,
            lockReason: allCharactersHaveImages
              ? undefined
              : "请先生成所有角色定妆照",
            loading: loadingFor(sceneId),
            loadingText: loadingTextFor(sceneId),
            onGenerate: () => handleGenerateStoryboard(scene),
          },
        });
        newEdges.push({
          id: `e-script-${sceneId}`,
          source: "script",
          target: sceneId,
        });

        // 视频节点（分镜生成后出现）
        if (sb) {
          const vd = videoMap.get(scene.scene_id);
          const videoNodeId = `video-${scene.scene_id}`;
          newNodes.push({
            id: videoNodeId,
            type: "custom",
            position: { x: 1300, y: 100 },
            data: {
              label: `视频 ${scene.scene_id}`,
              type: "video",
              detail: vd
                ? `已生成 (${vd.duration_seconds}s)`
                : `Wan 2.2 I2V · ${scene.duration_seconds}s`,
              videoUrl: vd?.video_url,
              hasGenerated: !!vd,
              generateLabel: vd ? "重新生成视频" : "生成视频",
              loading: loadingFor(videoNodeId),
              loadingText: loadingTextFor(videoNodeId),
              onGenerate: () =>
                handleGenerateVideo(
                  scene.scene_id,
                  sb.image_url,
                  scene.prompt || sb.prompt_used,
                  scene.negative_prompt
                ),
            },
          });
          newEdges.push({
            id: `e-${sceneId}-${videoNodeId}`,
            source: sceneId,
            target: videoNodeId,
          });

          // 配音节点（视频生成后出现）
          if (vd) {
            const vc = voiceMap.get(scene.scene_id);
            const voiceNodeId = `voice-${scene.scene_id}`;
            newNodes.push({
              id: voiceNodeId,
              type: "custom",
              position: { x: 1300, y: 320 },
              data: {
                label: `配音 ${scene.scene_id}`,
                type: "voice",
                detail: vc
                  ? `edge-tts · ${vc.total_lines} 条`
                  : "edge-tts 自动提取对白",
                audioUrl: vc?.audio_urls[0]?.audio_url,
                hasGenerated: !!vc,
                generateLabel: vc ? "重新生成配音" : "生成配音",
                loading: loadingFor(voiceNodeId),
                loadingText: loadingTextFor(voiceNodeId),
                onGenerate: () => handleGenerateVoice(scene),
              },
            });
            newEdges.push({
              id: `e-${videoNodeId}-${voiceNodeId}`,
              source: videoNodeId,
              target: voiceNodeId,
            });

            // 字幕节点（配音生成后出现）
            if (vc && vc.audio_urls.length > 0) {
              const st = subtitleMap.get(scene.scene_id);
              const subtitleNodeId = `subtitle-${scene.scene_id}`;
              const subtitlePreview = st
                ? st.segments
                    .slice(0, 3)
                    .map((seg) => seg.text)
                    .join(" / ")
                : "";
              newNodes.push({
                id: subtitleNodeId,
                type: "custom",
                position: { x: 1600, y: 320 },
                data: {
                  label: `字幕 ${scene.scene_id}`,
                  type: "subtitle",
                  detail: st
                    ? `faster-whisper (${st.language}) · ${st.segments.length} 段`
                    : "faster-whisper ASR",
                  subtitleText: subtitlePreview,
                  hasGenerated: !!st,
                  generateLabel: st ? "重新生成字幕" : "生成字幕",
                  loading: loadingFor(subtitleNodeId),
                  loadingText: loadingTextFor(subtitleNodeId),
                  onGenerate: () =>
                    handleGenerateSubtitle(
                      scene.scene_id,
                      vc.audio_urls[0].audio_url
                    ),
                },
              });
              newEdges.push({
                id: `e-${voiceNodeId}-${subtitleNodeId}`,
                source: voiceNodeId,
                target: subtitleNodeId,
              });
            }
          }
        }
      });

      // 成片节点（有完整素材时出现）
      // 流程控制：所有场景的视频+配音+字幕都完成才能合成成片
      const allScenesReady = scriptData.scenes.every((s) => {
        const v = videoMap.get(s.scene_id);
        const voice = voiceMap.get(s.scene_id);
        const sub = subtitleMap.get(s.scene_id);
        return (
          !!v && !!voice && voice.audio_urls.length > 0 && !!sub
        );
      });
      const hasReadyScenes = videos.some((v) => {
        const voice = voices.find((vo) => vo.scene_id === v.scene_id);
        const subtitle = subtitles.find((s) => s.scene_id === v.scene_id);
        return voice && voice.audio_urls.length > 0 && subtitle;
      });
      if (hasReadyScenes) {
        newNodes.push({
          id: "edit-final",
          type: "custom",
          position: { x: 1900, y: 320 },
          data: {
            label: editData ? `成片: ${editData.title}` : "合成成片",
            type: "edit",
            detail: editData
              ? `${editData.segments_count} 场景 | ${editData.duration_seconds.toFixed(1)}s`
              : "合成视频+配音+字幕",
            videoUrl: editData?.final_video_url,
            hasGenerated: !!editData,
            generateLabel: editData ? "重新合成成片" : "合成成片",
            canGenerate: allScenesReady,
            lockReason: allScenesReady
              ? undefined
              : "请先完成所有场景的视频、配音、字幕",
            loading: loadingFor("edit-final"),
            loadingText: loadingTextFor("edit-final"),
            onGenerate: handleComposeVideo,
          },
        });
        // 从字幕节点连向成片
        subtitles.forEach((sub) => {
          newEdges.push({
            id: `e-subtitle-${sub.scene_id}-edit`,
            source: `subtitle-${sub.scene_id}`,
            target: "edit-final",
          });
        });
      }

      // 质检节点（流程控制：成片合成后才能质检）
      const qualitySummary = qualityData
        ? `质量分 ${qualityData.score} | ${qualityData.issues.length} 问题`
        : "台词一致性 / 剧情逻辑 / 敏感词";
      const qualityIssuesPreview = qualityData
        ? qualityData.issues
            .slice(0, 3)
            .map(
              (i) => `[${i.severity}] ${i.message}`
            )
            .join("\n")
        : "";
      newNodes.push({
        id: "quality-final",
        type: "custom",
        position: { x: 400, y: 500 },
        data: {
          label: qualityData ? `质检: ${qualityData.title}` : "剧本质检",
          type: "quality",
          detail: qualityData
            ? `已检查 | 质量分 ${qualityData.score}`
            : "一键质检",
          qualitySummary,
          qualityIssues: qualityIssuesPreview,
          hasGenerated: !!qualityData,
          generateLabel: qualityData ? "重新质检" : "一键质检",
          canGenerate: !!editData,
          lockReason: editData ? undefined : "请先合成成片",
          loading: loadingFor("quality-final"),
          loadingText: loadingTextFor("quality-final"),
          onGenerate: handleCheckQuality,
        },
      });
      newEdges.push({
        id: "e-script-quality",
        source: "script",
        target: "quality-final",
      });

      // 视觉质检节点（有视频时出现）
      if (videos.length > 0) {
        const vqSummary = visualQualityData
          ? `质量分 ${visualQualityData.score} | 场景 ${visualQualityData.scene_id}`
          : "角色一致性 / 画面连贯性";
        const vqIssuesPreview = visualQualityData
          ? visualQualityData.issues
              .slice(0, 3)
              .map((i) => `[${i.severity}] ${i.message}`)
              .join("\n")
          : "";
        newNodes.push({
          id: "visual-quality-final",
          type: "custom",
          position: { x: 1600, y: 540 },
          data: {
            label: visualQualityData
              ? `视觉质检: 场景 ${visualQualityData.scene_id}`
              : "视觉质检",
            type: "visual_quality",
            detail: visualQualityData
              ? `已检查 | 质量分 ${visualQualityData.score}`
              : "视频画面质检",
            qualitySummary: vqSummary,
            qualityIssues: vqIssuesPreview,
            hasGenerated: !!visualQualityData,
            generateLabel: visualQualityData ? "重新视觉质检" : "视觉质检",
            loading: loadingFor("visual-quality-final"),
            loadingText: loadingTextFor("visual-quality-final"),
            onGenerate: handleCheckVisualQuality,
          },
        });
        // 从第一个视频节点连向视觉质检
        newEdges.push({
          id: "e-video-visual-quality",
          source: `video-${videos[0].scene_id}`,
          target: "visual-quality-final",
        });
      }
    }

    // 全局生成状态锁定：任一 Agent 生成中时，所有带 generateLabel 的节点禁止触发
    const lockedNodes = newNodes.map((node) => {
      if (!globalLoading || !node.data.generateLabel) return node;
      return {
        ...node,
        data: {
          ...node.data,
          canGenerate: false,
          lockReason: "有其他生成任务进行中，请等待完成",
        },
      };
    });

    const { nodes: layoutedNodes, edges: layoutedEdges } = getLayoutedElements(
      lockedNodes,
      newEdges
    );
    setNodes(layoutedNodes);
    setEdges(layoutedEdges);
  }, [
    scriptData,
    storyboards,
    videos,
    voices,
    subtitles,
    editData,
    qualityData,
    visualQualityData,
    loadingMap,
    characterCards,
    addCharacterCard,
    globalLoading,
    setScriptData,
    addStoryboard,
    addVideo,
    addVoice,
    addSubtitle,
    setEditData,
    setQualityData,
    setVisualQualityData,
    setStatusInfo,
    setLoading,
    clearLoading,
    startGlobalLoading,
    stopGlobalLoading,
  ]);

  return (
    <>
      {activePreviewCharacterId && (
        <CharacterPreviewPanel
          characterId={activePreviewCharacterId}
          onClose={() => setActivePreviewCharacterId(null)}
        />
      )}
      {activeDetailNode && (
        <NodeDetailPanel
          nodeId={activeDetailNode.id}
          type={activeDetailNode.type}
          onGenerate={activeDetailNode.onGenerate}
          onClose={() => setActiveDetailNode(null)}
        />
      )}
      <div className="sidebar">
        <div className="sidebar-title">节点面板</div>
        <div className="node-palette">
          <div className="node-palette-item">
            <span className="node-dot" style={{ background: "var(--node-script)" }}></span>
            剧本节点
          </div>
          <div className="node-palette-item">
            <span className="node-dot" style={{ background: "var(--node-character)" }}></span>
            角色节点
          </div>
          <div className="node-palette-item">
            <span className="node-dot" style={{ background: "var(--node-storyboard)" }}></span>
            分镜节点
          </div>
          <div className="node-palette-item">
            <span className="node-dot" style={{ background: "var(--node-video)" }}></span>
            视频节点
          </div>
          <div className="node-palette-item">
            <span className="node-dot" style={{ background: "var(--node-voice)" }}></span>
            配音节点
          </div>
          <div className="node-palette-item">
            <span className="node-dot" style={{ background: "var(--node-subtitle)" }}></span>
            字幕节点
          </div>
          <div className="node-palette-item">
            <span className="node-dot" style={{ background: "var(--node-edit)" }}></span>
            成片节点
          </div>
          <div className="node-palette-item">
            <span className="node-dot" style={{ background: "var(--node-quality)" }}></span>
            质检节点
          </div>
          <div className="node-palette-item">
            <span className="node-dot" style={{ background: "var(--node-visual-quality)" }}></span>
            视觉质检节点
          </div>
        </div>

        <div className="sidebar-title" style={{ marginTop: "16px" }}>
          批量生成
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
          <button
            style={{
              ...btnStyle,
              fontSize: "12px",
              ...(globalLoading ? { opacity: 0.4, cursor: "not-allowed", background: "#333" } : {}),
            }}
            disabled={globalLoading}
            onClick={handleGenerateAllStoryboards}
            className="nodrag"
          >
            生成全部分镜（多 GPU）
          </button>
          <button
            style={{
              ...btnStyle,
              fontSize: "12px",
              ...(globalLoading ? { opacity: 0.4, cursor: "not-allowed", background: "#333" } : {}),
            }}
            disabled={globalLoading}
            onClick={handleGenerateAllVideos}
            className="nodrag"
          >
            生成全部视频（多 GPU）
          </button>
        </div>
      </div>

      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={onConnect}
        onNodeClick={(_, node) => {
          if (node.data.type === "character" && node.id.startsWith("char-")) {
            setActivePreviewCharacterId(node.id.replace("char-", ""));
            return;
          }
          setActiveDetailNode({
            id: node.id,
            type: node.data.type,
            onGenerate: node.data.onGenerate,
          });
        }}
        nodeTypes={nodeTypes}
        fitView
        fitViewOptions={{ padding: 0.2 }}
      >
        <Background color="#2a2a2a" gap={20} />
        <Controls />
        <MiniMap
          nodeColor={(node) => {
            const type = (node.data as DramaNodeData)?.type;
            const map: Record<string, string> = {
              script: "#4a6fa5",
              character: "#a54a6f",
              storyboard: "#4aa57a",
              video: "#a57a4a",
              voice: "#6b4aa5",
              subtitle: "#4aa5a5",
              edit: "#a54a4a",
              quality: "#a5a54a",
              visual_quality: "#7a4aa5",
            };
            return map[type] || "#8a6b4a";
          }}
          maskColor="rgba(26,26,26,0.8)"
        />
      </ReactFlow>
    </>
  );
}
