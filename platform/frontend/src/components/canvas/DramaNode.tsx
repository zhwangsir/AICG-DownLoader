import { memo, useState, type MouseEventHandler } from "react";
import { Handle, Position } from "reactflow";
import {
  Check,
  Lock,
  FileText,
  Users,
  Film,
  Video,
  Mic,
  Subtitles,
  Clapperboard,
  CheckCircle2,
  Loader2,
  Wand2,
  Image as ImageIcon,
  Music,
  Type,
  Sparkles,
  AlertCircle,
} from "lucide-react";
import { useDramaStore } from "../../store/useDramaStore";
import type { DramaNodeData } from "./layout";
import type { ScriptGenerateOptions } from "../NodeDetailPanel";

const iconMap: Record<string, React.ElementType> = {
  script: FileText,
  character: Users,
  storyboard: Film,
  video: Video,
  voice: Mic,
  subtitle: Subtitles,
  edit: Clapperboard,
  quality: CheckCircle2,
  visual_quality: Sparkles,
  lip_sync: Mic,
  postprocess: Clapperboard,
};

const placeholderIconMap: Record<string, React.ElementType> = {
  script: FileText,
  character: Users,
  storyboard: ImageIcon,
  video: Video,
  voice: Music,
  subtitle: Type,
  edit: Clapperboard,
  quality: CheckCircle2,
  visual_quality: Sparkles,
};

const typeConfig: Record<
  string,
  { color: string; bg: string; glow: string; dark: string; label: string }
> = {
  script: { color: "#b8823f", bg: "#faf2e2", glow: "rgba(184,130,63,0.3)", dark: "#7e5624", label: "剧本" },
  character: { color: "#5c9488", bg: "#e8f0ee", glow: "rgba(92,148,136,0.3)", dark: "#3d6860", label: "角色" },
  storyboard: { color: "#5b7fb5", bg: "#e8eef6", glow: "rgba(91,127,181,0.3)", dark: "#3d5a8a", label: "分镜" },
  video: { color: "#b05a8a", bg: "#f5e9f0", glow: "rgba(176,90,138,0.3)", dark: "#7d3d62", label: "视频" },
  voice: { color: "#c48a3c", bg: "#f9f0e0", glow: "rgba(196,138,60,0.3)", dark: "#8a5f26", label: "配音" },
  subtitle: { color: "#55998f", bg: "#e6f1ef", glow: "rgba(85,153,143,0.3)", dark: "#3a6b64", label: "字幕" },
  edit: { color: "#a04848", bg: "#f4e6e6", glow: "rgba(160,72,72,0.3)", dark: "#703030", label: "成片" },
  quality: { color: "#4d8c54", bg: "#e5f0e7", glow: "rgba(77,140,84,0.3)", dark: "#35633b", label: "质检" },
  visual_quality: { color: "#7c5aa8", bg: "#f0eaf5", glow: "rgba(124,90,168,0.3)", dark: "#553d75", label: "视觉质检" },
  lip_sync: { color: "#c45a78", bg: "#f7e9ee", glow: "rgba(196,90,120,0.3)", dark: "#8a3d54", label: "唇形同步" },
  postprocess: { color: "#4f6eb0", bg: "#e8edf5", glow: "rgba(79,110,176,0.3)", dark: "#354d7d", label: "后处理" },
};

const GENRE_PRESETS = [
  "都市悬疑", "古风仙侠", "科幻未来", "校园青春", "职场商战",
  "武侠江湖", "末日废土", "温情治愈", "犯罪推理", "奇幻冒险",
  "家庭伦理", "历史穿越", "甜宠恋爱", "恐怖惊悚", "医疗救援",
  "体育竞技", "美食治愈", "商战复仇",
];

function truncate(str: string, max: number) {
  if (!str) return "";
  return str.length > max ? str.slice(0, max) + "…" : str;
}

function MediaPlaceholder({
  cfg,
  icon: Icon,
  label,
  reason,
  compact = false,
}: {
  cfg: ReturnType<typeof getCfg>;
  icon: React.ElementType;
  label: string;
  reason?: string;
  compact?: boolean;
}) {
  return (
    <div
      style={{
        width: "100%",
        height: compact ? 78 : 110,
        borderRadius: compact ? 10 : 12,
        background: `linear-gradient(135deg, ${cfg.bg}, rgba(255,255,255,0.6))`,
        border: `${compact ? 1 : 1.5}px dashed ${cfg.color}${compact ? "30" : "40"}`,
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        gap: compact ? 5 : 8,
        color: cfg.dark,
        position: "relative",
        overflow: "hidden",
      }}
    >
      <div
        style={{
          width: compact ? 30 : 40,
          height: compact ? 30 : 40,
          borderRadius: compact ? 9 : 12,
          background: `rgba(255,255,255,0.72)`,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          boxShadow: compact ? `0 1px 5px ${cfg.glow}` : `0 2px 8px ${cfg.glow}`,
        }}
      >
        <Icon size={compact ? 15 : 20} color={cfg.color} strokeWidth={1.8} />
      </div>
      <div style={{ fontSize: compact ? 10 : 11, fontWeight: 600, color: cfg.dark }}>{label}</div>
      {reason && (
        <div
          style={{
            position: "absolute",
            bottom: 0,
            left: 0,
            right: 0,
            padding: compact ? "4px 8px" : "6px 10px",
            fontSize: compact ? 9 : 10,
            color: "var(--text-tertiary)",
            background: "rgba(250,248,245,0.85)",
            backdropFilter: "blur(4px)",
            display: "flex",
            alignItems: "center",
            gap: 5,
            borderTop: `1px solid ${cfg.color}20`,
          }}
        >
          <Lock size={compact ? 9 : 10} />
          <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {reason}
          </span>
        </div>
      )}
    </div>
  );
}

function getCfg(type: string) {
  return typeConfig[type] ?? typeConfig.script;
}

function DramaNode({ data, selected }: { data: DramaNodeData; selected?: boolean }) {
  const stopNodeDrag: MouseEventHandler<HTMLElement> = (e) => {
    e.stopPropagation();
  };
  const globalLoading = useDramaStore((s) => s.globalLoading);
  const projectStyle = useDramaStore((s) => s.projectStyle);
  const Icon = iconMap[data.type] ?? FileText;
  const PlaceholderIcon = placeholderIconMap[data.type] ?? ImageIcon;
  const cfg = getCfg(data.type);

  const [scriptOptions, setScriptOptions] = useState<ScriptGenerateOptions>({
    premise: "",
    genre: "",
    episodes: "",
    scenes_per_episode: "",
    style: "",
    aspect_ratio: "",
  });
  const updateScriptOption = <K extends keyof ScriptGenerateOptions>(
    key: K,
    value: ScriptGenerateOptions[K]
  ) => setScriptOptions((prev) => ({ ...prev, [key]: value }));

  const [showEditPanel, setShowEditPanel] = useState(false);
  const [editPositive, setEditPositive] = useState("");
  const [editNegative, setEditNegative] = useState("");
  const [videoError, setVideoError] = useState(false);
  const [nodeError, setNodeError] = useState<string | null>(null);

  const showGenerateBtn = !!data.generateLabel && !data.loading;
  const isLocked = showGenerateBtn && (data.canGenerate === false || globalLoading);
  const isFutureNode = !data.hasGenerated && !data.loading && !data.isScriptInput && !data.isEditInput;

  const openEditPanel = () => {
    setEditPositive(data.editablePrompts?.positive || "");
    setEditNegative(data.editablePrompts?.negative || "");
    setShowEditPanel(true);
  };

  const applyEditAndRegenerate = () => {
    setShowEditPanel(false);
    data.onEditPrompts?.(editPositive, editNegative);
  };

  const hasMedia = !!(data.imageUrl || data.videoUrl || data.audioUrl || data.subtitleText);
  const isMediaType = ["character", "storyboard", "video"].includes(data.type);
  const isDimmed = !data.hasGenerated && !data.loading && !data.isScriptInput && !data.isEditInput;

  const statusColor = data.loading
    ? cfg.color
    : data.hasGenerated
    ? "#4d8c54"
    : isLocked && data.lockReason
    ? "#b87a3f"
    : "#9a9184";

  const statusLabel = data.statusText
    ? data.statusText
    : data.loading
    ? data.loadingText || "处理中…"
    : data.hasGenerated
    ? "已完成"
    : isLocked && data.lockReason
    ? "待解锁"
    : "等待开始";

  const cardBg = isDimmed ? "rgba(250,248,245,0.42)" : "var(--bg-elevated)";
  const cardBorder = selected ? cfg.color : isDimmed ? `${cfg.color}28` : "var(--border-light)";
  const nodeWidth = isFutureNode ? 240 : 280;
  const contentPadding = isFutureNode ? "10px 14px 12px" : "12px 16px 14px";

  return (
    <div
      style={{
        width: nodeWidth,
        borderRadius: isFutureNode ? 14 : 18,
        background: cardBg,
        border: `${isFutureNode ? 1 : 1.5}px solid ${cardBorder}`,
        borderStyle: isDimmed && !selected ? "dashed" : "solid",
        boxShadow: selected
          ? `0 0 0 4px ${cfg.glow}, var(--shadow-lg), 0 20px 40px -12px ${cfg.glow}`
          : isDimmed
          ? "none"
          : data.hasGenerated
          ? "var(--shadow-md)"
          : "var(--shadow-sm)",
        opacity: isDimmed ? 0.72 : 1,
        overflow: "hidden",
        transition: "all 0.28s var(--ease-out)",
        transform: selected ? "translateY(-2px)" : "translateY(0)",
        position: "relative",
        cursor: "grab",
      }}
      onMouseEnter={(e) => {
        if (!selected) {
          (e.currentTarget as HTMLDivElement).style.boxShadow =
            "var(--shadow-lg), 0 8px 24px -8px rgba(28,24,20,0.12)";
          (e.currentTarget as HTMLDivElement).style.transform = "translateY(-2px)";
          (e.currentTarget as HTMLDivElement).style.opacity = "1";
          (e.currentTarget as HTMLDivElement).style.background = "var(--bg-elevated)";
          (e.currentTarget as HTMLDivElement).style.borderColor = cfg.color;
        }
      }}
      onMouseLeave={(e) => {
        if (!selected) {
          (e.currentTarget as HTMLDivElement).style.boxShadow = isDimmed ? "none" : data.hasGenerated ? "var(--shadow-md)" : "var(--shadow-sm)";
          (e.currentTarget as HTMLDivElement).style.transform = "translateY(0)";
          (e.currentTarget as HTMLDivElement).style.opacity = isDimmed ? "0.72" : "1";
          (e.currentTarget as HTMLDivElement).style.background = cardBg;
          (e.currentTarget as HTMLDivElement).style.borderColor = cardBorder;
        }
      }}
    >
      {/* 顶部类型色条 */}
      <div
        style={{
          height: 5,
          background: `linear-gradient(90deg, ${cfg.color}, ${cfg.dark})`,
          position: "relative",
        }}
      >
        {data.loading && (
          <div
            style={{
              position: "absolute",
              inset: 0,
              background: `linear-gradient(90deg, transparent, rgba(255,255,255,0.65), transparent)`,
              animation: "node-shimmer 2s ease-in-out infinite",
            }}
          />
        )}
      </div>

      {/* 头部区域 */}
      <div
        style={{
          padding: isFutureNode ? "10px 14px 7px" : "12px 16px 9px",
          display: "flex",
          alignItems: "center",
          gap: isFutureNode ? 8 : 10,
          borderBottom: `1px solid ${isDimmed ? `${cfg.color}22` : "var(--border-light)"}`,
          background: isDimmed ? `${cfg.bg}60` : cfg.bg,
        }}
      >
        <div
          style={{
            width: isFutureNode ? 30 : 36,
            height: isFutureNode ? 30 : 36,
            borderRadius: isFutureNode ? 8 : 10,
            background: isDimmed
              ? `linear-gradient(135deg, ${cfg.color}80, ${cfg.dark}80)`
              : `linear-gradient(135deg, ${cfg.color}, ${cfg.dark})`,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            boxShadow: isDimmed ? "none" : `0 2px 8px ${cfg.glow}`,
            flexShrink: 0,
          }}
        >
          <Icon size={isFutureNode ? 15 : 18} color="#fff" strokeWidth={2} />
        </div>

        <div style={{ flex: 1, minWidth: 0 }}>
          <div
            style={{
              fontSize: isFutureNode ? 12 : 13,
              fontWeight: 650,
              color: isDimmed ? "var(--text-secondary)" : "var(--text-primary)",
              letterSpacing: "-0.01em",
              lineHeight: 1.25,
              whiteSpace: "nowrap",
              overflow: "hidden",
              textOverflow: "ellipsis",
            }}
          >
            {data.label}
          </div>
          <div
            style={{
              fontSize: 9.5,
              color: statusColor,
              marginTop: 2,
              display: "flex",
              alignItems: "center",
              gap: 4,
              fontWeight: 600,
            }}
          >
            {data.loading ? (
              <Loader2 size={10} style={{ animation: "node-spin 1.2s linear infinite" }} />
            ) : data.hasGenerated ? (
              <CheckCircle2 size={10} />
            ) : isLocked && data.lockReason ? (
              <Lock size={10} />
            ) : (
              <Wand2 size={10} />
            )}
            <span>{statusLabel}</span>
          </div>
        </div>
      </div>

      {/* 内容区域 */}
      <div style={{ padding: contentPadding }}>
        {/* 创意输入区域 */}
        {data.isScriptInput && !data.hasGenerated && !data.loading && (
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            <textarea
              value={scriptOptions.premise}
              onChange={(e) => updateScriptOption("premise", e.target.value)}
              onMouseDown={stopNodeDrag}
              onClick={stopNodeDrag}
              placeholder="输入一句话创意…"
              className="nodrag"
              style={{
                width: "100%",
                minHeight: 58,
                padding: "10px 12px",
                background: "var(--bg-secondary)",
                border: "1.5px solid var(--border)",
                borderRadius: 12,
                color: "var(--text-primary)",
                fontSize: 12,
                fontFamily: "inherit",
                lineHeight: 1.5,
                resize: "vertical",
                outline: "none",
                transition: "all 0.2s var(--ease-out)",
                boxSizing: "border-box",
              }}
              onFocus={(e) => {
                e.currentTarget.style.borderColor = cfg.color;
                e.currentTarget.style.boxShadow = `0 0 0 3px ${cfg.glow}`;
              }}
              onBlur={(e) => {
                e.currentTarget.style.borderColor = "var(--border)";
                e.currentTarget.style.boxShadow = "none";
              }}
            />
            <input
              list="genre-presets"
              value={scriptOptions.genre}
              onChange={(e) => updateScriptOption("genre", e.target.value)}
              onMouseDown={stopNodeDrag}
              onClick={stopNodeDrag}
              placeholder="输入题材或选择推荐项"
              className="nodrag"
              style={{
                width: "100%",
                padding: "8px 12px",
                background: "var(--bg-secondary)",
                border: "1.5px solid var(--border)",
                borderRadius: 12,
                color: "var(--text-primary)",
                fontSize: 12,
                fontFamily: "inherit",
                outline: "none",
                transition: "all 0.2s var(--ease-out)",
                boxSizing: "border-box",
              }}
              onFocus={(e) => {
                e.currentTarget.style.borderColor = cfg.color;
                e.currentTarget.style.boxShadow = `0 0 0 3px ${cfg.glow}`;
              }}
              onBlur={(e) => {
                e.currentTarget.style.borderColor = "var(--border)";
                e.currentTarget.style.boxShadow = "none";
              }}
            />
            <datalist id="genre-presets">
              {GENRE_PRESETS.map((g) => (
                <option key={g} value={g} />
              ))}
            </datalist>

            {/* 脚本全局控制项：集数 / 分镜数 / 风格 / 画幅 */}
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
              <div>
                <label
                  style={{
                    display: "block",
                    fontSize: 10,
                    fontWeight: 600,
                    color: "var(--text-tertiary)",
                    marginBottom: 3,
                  }}
                >
                  集数
                </label>
                <input
                  type="number"
                  min={1}
                  max={100}
                  placeholder="请输入"
                  value={scriptOptions.episodes}
                  onChange={(e) => {
                    const raw = e.target.value;
                    updateScriptOption(
                      "episodes",
                      raw === "" ? "" : Math.max(1, Math.min(100, Number(raw) || 1))
                    );
                  }}
                  onMouseDown={stopNodeDrag}
                  onClick={stopNodeDrag}
                  className="nodrag"
                  style={{
                    width: "100%",
                    padding: "6px 8px",
                    background: "var(--bg-secondary)",
                    border: "1.5px solid var(--border)",
                    borderRadius: 10,
                    color: "var(--text-primary)",
                    fontSize: 11,
                    fontFamily: "inherit",
                    outline: "none",
                    boxSizing: "border-box",
                  }}
                />
              </div>
              <div>
                <label
                  style={{
                    display: "block",
                    fontSize: 10,
                    fontWeight: 600,
                    color: "var(--text-tertiary)",
                    marginBottom: 3,
                  }}
                >
                  每集分镜
                </label>
                <input
                  type="number"
                  min={1}
                  max={30}
                  placeholder="请输入"
                  value={scriptOptions.scenes_per_episode}
                  onChange={(e) => {
                    const raw = e.target.value;
                    updateScriptOption(
                      "scenes_per_episode",
                      raw === "" ? "" : Math.max(1, Math.min(30, Number(raw) || 1))
                    );
                  }}
                  onMouseDown={stopNodeDrag}
                  onClick={stopNodeDrag}
                  className="nodrag"
                  style={{
                    width: "100%",
                    padding: "6px 8px",
                    background: "var(--bg-secondary)",
                    border: "1.5px solid var(--border)",
                    borderRadius: 10,
                    color: "var(--text-primary)",
                    fontSize: 11,
                    fontFamily: "inherit",
                    outline: "none",
                    boxSizing: "border-box",
                  }}
                />
              </div>
            </div>

            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
              <div>
                <label
                  style={{
                    display: "block",
                    fontSize: 10,
                    fontWeight: 600,
                    color: "var(--text-tertiary)",
                    marginBottom: 3,
                  }}
                >
                  视觉风格
                </label>
                <select
                  value={scriptOptions.style}
                  onChange={(e) => updateScriptOption("style", e.target.value)}
                  onMouseDown={stopNodeDrag}
                  onClick={stopNodeDrag}
                  className="nodrag"
                  style={{
                    width: "100%",
                    padding: "6px 8px",
                    background: "var(--bg-secondary)",
                    border: "1.5px solid var(--border)",
                    borderRadius: 10,
                    color: "var(--text-primary)",
                    fontSize: 11,
                    fontFamily: "inherit",
                    outline: "none",
                    boxSizing: "border-box",
                  }}
                >
                  <option value="" disabled>
                    请选择
                  </option>
                  {[
                    "写实电影感",
                    "都市情感",
                    "悬疑暗调",
                    "赛博朋克",
                    "古风仙侠",
                    "国漫",
                    "动漫",
                    "卡通 3D",
                    "东方水墨",
                    "童话绘本",
                  ].map((s) => (
                    <option key={s} value={s}>
                      {s}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label
                  style={{
                    display: "block",
                    fontSize: 10,
                    fontWeight: 600,
                    color: "var(--text-tertiary)",
                    marginBottom: 3,
                  }}
                >
                  画幅
                </label>
                <select
                  value={scriptOptions.aspect_ratio}
                  onChange={(e) => updateScriptOption("aspect_ratio", e.target.value)}
                  onMouseDown={stopNodeDrag}
                  onClick={stopNodeDrag}
                  className="nodrag"
                  style={{
                    width: "100%",
                    padding: "6px 8px",
                    background: "var(--bg-secondary)",
                    border: "1.5px solid var(--border)",
                    borderRadius: 10,
                    color: "var(--text-primary)",
                    fontSize: 11,
                    fontFamily: "inherit",
                    outline: "none",
                    boxSizing: "border-box",
                  }}
                >
                  <option value="" disabled>
                    请选择
                  </option>
                  {["9:16", "16:9", "1:1"].map((r) => (
                    <option key={r} value={r}>
                      {r}
                    </option>
                  ))}
                </select>
              </div>
            </div>
          </div>
        )}

        {/* 图片预览 / 占位 */}
        {isMediaType && (
          <div style={{ marginBottom: isFutureNode && data.type === "character" ? 6 : 10 }}>
            {data.imageUrl ? (
              <div style={{ position: "relative" }}>
                <img
                  src={data.imageUrl}
                  alt={data.label}
                  loading="lazy"
                  style={{
                    width: "100%",
                    height: isFutureNode ? 96 : 132,
                    objectFit: "cover",
                    borderRadius: 12,
                    display: "block",
                    boxShadow: "var(--shadow-sm)",
                    transition: "transform 0.3s var(--ease-out)",
                  }}
                  onMouseEnter={(e) => {
                    (e.currentTarget as HTMLImageElement).style.transform = "scale(1.02)";
                  }}
                  onMouseLeave={(e) => {
                    (e.currentTarget as HTMLImageElement).style.transform = "scale(1)";
                  }}
                />
                {data.hasGenerated && (
                  <div
                    style={{
                      position: "absolute",
                      top: 8,
                      left: 8,
                      width: 22,
                      height: 22,
                      borderRadius: "50%",
                      background: "rgba(77,140,84,0.95)",
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      boxShadow: "0 2px 6px rgba(77,140,84,0.4)",
                    }}
                  >
                    <Check size={12} color="#fff" strokeWidth={3} />
                  </div>
                )}
              </div>
            ) : data.type === "character" && isDimmed ? (
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                  padding: "6px 8px",
                  borderRadius: 8,
                  background: `${cfg.bg}60`,
                  border: `1px dashed ${cfg.color}30`,
                  color: cfg.dark,
                  fontSize: 10,
                  fontWeight: 600,
                }}
              >
                <div
                  style={{
                    width: 22,
                    height: 22,
                    borderRadius: 7,
                    background: "rgba(255,255,255,0.7)",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                  }}
                >
                  <PlaceholderIcon size={12} color={cfg.color} strokeWidth={1.8} />
                </div>
                <span>定妆照待生成</span>
              </div>
            ) : (
              <MediaPlaceholder
                cfg={cfg}
                icon={PlaceholderIcon}
                label={data.hasGenerated ? cfg.label : `待生成${cfg.label}`}
                reason={isLocked && data.lockReason ? data.lockReason : undefined}
                compact={isFutureNode}
              />
            )}
          </div>
        )}

        {/* Tags */}
        {data.tags && data.tags.length > 0 && (
          <div style={{ display: "flex", flexWrap: "wrap", gap: isFutureNode ? 4 : 5, marginBottom: isFutureNode ? 8 : 10 }}>
            {data.tags.slice(0, isFutureNode ? 4 : undefined).map((tag, idx) => (
              <span
                key={idx}
                style={{
                  padding: isFutureNode ? "1.5px 6px" : "2px 7px",
                  borderRadius: 5,
                  fontSize: isFutureNode ? 9 : 9.5,
                  fontWeight: 600,
                  color: isDimmed ? cfg.dark : cfg.dark,
                  background: isDimmed ? `${cfg.bg}80` : cfg.bg,
                  border: `1px solid ${cfg.color}${isDimmed ? "20" : "28"}`,
                  letterSpacing: "0.01em",
                  opacity: isDimmed ? 0.85 : 1,
                }}
              >
                {tag}
              </span>
            ))}
          </div>
        )}

        {/* Meta 键值对：2 列网格（未来节点只展示关键 2 项） */}
        {data.meta && data.meta.length > 0 && (
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(2, 1fr)",
              gap: isFutureNode ? "4px 8px" : "6px 10px",
              marginBottom: isFutureNode ? 8 : 10,
              padding: isFutureNode ? "6px 8px" : "8px 10px",
              background: isDimmed ? "rgba(250,248,245,0.5)" : "var(--bg-secondary)",
              borderRadius: isFutureNode ? 8 : 10,
              border: `1px solid ${isDimmed ? `${cfg.color}18` : "var(--border-light)"}`,
            }}
          >
            {(isFutureNode ? data.meta.slice(0, 2) : data.meta).map((m, idx) => (
              <div key={idx} style={{ minWidth: 0, overflow: "hidden" }}>
                <div style={{ fontSize: 9, color: "var(--text-tertiary)", fontWeight: 600, marginBottom: 1 }}>
                  {m.label}
                </div>
                <div
                  style={{
                    fontSize: isFutureNode ? 10 : 11,
                    fontWeight: 600,
                    color: isDimmed ? "var(--text-secondary)" : "var(--text-primary)",
                    whiteSpace: "nowrap",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                  }}
                >
                  {m.value}
                </div>
              </div>
            ))}
          </div>
        )}

        {/* Preview 长文本 */}
        {data.preview && !data.loading && (
          <div
            style={{
              fontSize: isFutureNode ? 10.5 : 11.5,
              color: isDimmed ? "var(--text-tertiary)" : "var(--text-secondary)",
              lineHeight: 1.55,
              marginBottom: isFutureNode ? 8 : 10,
              display: "-webkit-box",
              WebkitLineClamp: isFutureNode ? 2 : 3,
              WebkitBoxOrient: "vertical",
              overflow: "hidden",
            }}
          >
            {data.preview}
          </div>
        )}

        {/* 视频预览 */}
        {data.videoUrl && (
          <>
            {!videoError ? (
              <video
                src={data.videoUrl}
                controls
                loop
                muted
                poster={data.imageUrl}
                preload="metadata"
                onError={() => setVideoError(true)}
                style={{
                  width: "100%",
                  height: 108,
                  objectFit: "cover",
                  borderRadius: 12,
                  marginBottom: 10,
                  display: "block",
                  border: "1px solid var(--border-light)",
                  boxShadow: "var(--shadow-xs)",
                  background: "var(--bg-secondary)",
                }}
              />
            ) : (
              <div
                style={{
                  width: "100%",
                  height: 108,
                  borderRadius: 12,
                  marginBottom: 10,
                  border: `1.5px dashed ${cfg.color}40`,
                  background: `linear-gradient(135deg, ${cfg.bg}, rgba(255,255,255,0.6))`,
                  display: "flex",
                  flexDirection: "column",
                  alignItems: "center",
                  justifyContent: "center",
                  gap: 6,
                  color: cfg.dark,
                  boxShadow: "var(--shadow-xs)",
                }}
              >
                <div
                  style={{
                    width: 36,
                    height: 36,
                    borderRadius: 10,
                    background: "rgba(255,255,255,0.72)",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    boxShadow: `0 2px 8px ${cfg.glow}`,
                  }}
                >
                  <Video size={18} color={cfg.color} strokeWidth={1.8} />
                </div>
                <div style={{ fontSize: 10, fontWeight: 600, letterSpacing: "0.01em" }}>
                  视频预览暂不可用
                </div>
              </div>
            )}
          </>
        )}

        {/* 音频 */}
        {data.audioUrl && (
          <audio
            controls
            src={data.audioUrl}
            style={{
              width: "100%",
              marginBottom: 10,
              height: 38,
              borderRadius: 8,
            }}
          />
        )}

        {/* 字幕预览 */}
        {data.subtitleText && (
          <div
            style={{
              marginBottom: 10,
              padding: "8px 10px",
              background: cfg.bg,
              borderRadius: 10,
              fontSize: 11,
              lineHeight: 1.55,
              maxHeight: 76,
              overflow: "auto",
              color: "var(--text-secondary)",
              border: `1px solid ${cfg.color}25`,
              fontFamily: '"SF Mono", "JetBrains Mono", Menlo, Consolas, monospace',
            }}
          >
            {data.subtitleText}
          </div>
        )}

        {/* 质检摘要 */}
        {data.qualitySummary && (
          <div
            style={{
              marginBottom: 8,
              padding: "10px 12px",
              background: `linear-gradient(135deg, ${cfg.bg}, var(--bg-secondary))`,
              borderRadius: 10,
              fontSize: 11,
              lineHeight: 1.5,
              border: `1px solid var(--border-light)`,
              color: "var(--text-secondary)",
            }}
          >
            {data.qualitySummary}
          </div>
        )}
        {data.qualityIssues && (
          <div
            style={{
              marginBottom: 8,
              fontSize: 10,
              color: "var(--text-tertiary)",
              maxHeight: 54,
              overflow: "auto",
              lineHeight: 1.5,
            }}
          >
            {data.qualityIssues}
          </div>
        )}

        {/* Loading 状态 */}
        {data.loading && (
          <div
            style={{
              padding: "18px 0",
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              gap: 10,
            }}
          >
            <div
              style={{
                width: 30,
                height: 30,
                borderRadius: "50%",
                border: `2.5px solid ${cfg.color}30`,
                borderTopColor: cfg.color,
                animation: "node-spin 0.85s linear infinite",
              }}
            />
            <span
              style={{
                fontSize: 11,
                color: cfg.color,
                fontWeight: 500,
              }}
            >
              {data.loadingText || "正在生成…"}
            </span>
          </div>
        )}

        {/* 非媒体类型锁定提示 */}
        {!isMediaType && isLocked && data.lockReason && !hasMedia && !data.loading && (
          <div
            style={{
              marginTop: 8,
              padding: "8px 10px",
              fontSize: 10,
              color: "var(--text-tertiary)",
              display: "flex",
              alignItems: "center",
              gap: 6,
              background: "rgba(197,189,176,0.12)",
              borderRadius: 8,
            }}
          >
            <Lock size={11} strokeWidth={2} />
            <span>{data.lockReason}</span>
          </div>
        )}

        {/* 编辑提示词按钮 */}
        {data.hasGenerated && !data.loading && data.editablePrompts && data.onEditPrompts && !showEditPanel && (
          <button
            onClick={(e) => {
              e.stopPropagation();
              openEditPanel();
            }}
            onMouseDown={stopNodeDrag}
            style={{
              width: "100%",
              marginTop: 10,
              padding: "7px 12px",
              background: "var(--bg-secondary)",
              border: "1.5px solid var(--border)",
              color: "var(--text-secondary)",
              borderRadius: 8,
              fontSize: 11,
              fontWeight: 500,
              cursor: "pointer",
              transition: "all 0.15s var(--ease-out)",
              fontFamily: "inherit",
            }}
            onMouseEnter={(e) => {
              (e.currentTarget as HTMLButtonElement).style.borderColor = cfg.color;
              (e.currentTarget as HTMLButtonElement).style.color = cfg.color;
              (e.currentTarget as HTMLButtonElement).style.background = cfg.bg;
            }}
            onMouseLeave={(e) => {
              (e.currentTarget as HTMLButtonElement).style.borderColor = "var(--border)";
              (e.currentTarget as HTMLButtonElement).style.color = "var(--text-secondary)";
              (e.currentTarget as HTMLButtonElement).style.background = "var(--bg-secondary)";
            }}
          >
            编辑提示词
          </button>
        )}

        {/* 编辑面板 */}
        {showEditPanel && (
          <div
            onClick={stopNodeDrag}
            style={{
              marginTop: 10,
              padding: 12,
              background: "var(--bg-secondary)",
              borderRadius: 10,
              border: "1px solid var(--border)",
              display: "flex",
              flexDirection: "column",
              gap: 8,
            }}
          >
            <div
              style={{
                fontSize: 10,
                color: "var(--text-tertiary)",
                fontWeight: 600,
                textTransform: "uppercase",
                letterSpacing: "0.05em",
              }}
            >
              正面提示词
            </div>
            <textarea
              value={editPositive}
              onChange={(e) => setEditPositive(e.target.value)}
              onMouseDown={stopNodeDrag}
              className="nodrag"
              style={{
                width: "100%",
                minHeight: 40,
                padding: "8px 10px",
                background: "var(--bg-primary)",
                border: "1px solid var(--border)",
                borderRadius: 8,
                fontSize: 10,
                fontFamily: "inherit",
                lineHeight: 1.5,
                resize: "vertical",
                outline: "none",
                boxSizing: "border-box",
              }}
            />
            <div
              style={{
                fontSize: 10,
                color: "var(--text-tertiary)",
                fontWeight: 600,
                textTransform: "uppercase",
                letterSpacing: "0.05em",
              }}
            >
              负面提示词
            </div>
            <textarea
              value={editNegative}
              onChange={(e) => setEditNegative(e.target.value)}
              onMouseDown={stopNodeDrag}
              className="nodrag"
              style={{
                width: "100%",
                minHeight: 30,
                padding: "8px 10px",
                background: "var(--bg-primary)",
                border: "1px solid var(--border)",
                borderRadius: 8,
                fontSize: 10,
                fontFamily: "inherit",
                lineHeight: 1.5,
                resize: "vertical",
                outline: "none",
                boxSizing: "border-box",
              }}
            />
            <div style={{ display: "flex", gap: 6, marginTop: 2 }}>
              <button
                disabled={globalLoading}
                onClick={(e) => {
                  e.stopPropagation();
                  applyEditAndRegenerate();
                }}
                onMouseDown={stopNodeDrag}
                style={{
                  flex: 1,
                  padding: "7px 10px",
                  background: `linear-gradient(135deg, ${cfg.color}, ${cfg.dark})`,
                  border: "none",
                  color: "#fff",
                  borderRadius: 8,
                  fontSize: 11,
                  fontWeight: 600,
                  cursor: globalLoading ? "not-allowed" : "pointer",
                  opacity: globalLoading ? 0.6 : 1,
                  transition: "all 0.15s var(--ease-out)",
                  fontFamily: "inherit",
                  boxShadow: `0 2px 8px ${cfg.glow}`,
                }}
              >
                {globalLoading ? "生成中..." : "应用并重新生成"}
              </button>
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  setShowEditPanel(false);
                }}
                onMouseDown={stopNodeDrag}
                style={{
                  padding: "7px 12px",
                  background: "var(--bg-primary)",
                  border: "1px solid var(--border)",
                  color: "var(--text-secondary)",
                  borderRadius: 8,
                  fontSize: 11,
                  fontWeight: 500,
                  cursor: "pointer",
                  transition: "all 0.15s var(--ease-out)",
                  fontFamily: "inherit",
                }}
              >
                取消
              </button>
            </div>
          </div>
        )}

        {/* 生成按钮：未来节点使用更轻量的样式 */}
        {nodeError && data.isScriptInput && (
          <div
            style={{
              marginTop: 8,
              padding: "7px 10px",
              borderRadius: 8,
              fontSize: 10,
              color: "#a04848",
              background: "rgba(160,72,72,0.08)",
              border: "1px solid rgba(160,72,72,0.2)",
            }}
          >
            {nodeError}
          </div>
        )}
        {showGenerateBtn && (
          <button
            disabled={isLocked}
            onClick={(e) => {
              e.stopPropagation();
              if (isLocked) return;
              if (data.isScriptInput) {
                if (!scriptOptions.premise.trim()) {
                  setNodeError("请输入一句话创意");
                  return;
                }
                if (!scriptOptions.genre.trim()) {
                  setNodeError("请输入题材");
                  return;
                }
                if (scriptOptions.episodes === "") {
                  setNodeError("请设置集数");
                  return;
                }
                if (scriptOptions.scenes_per_episode === "") {
                  setNodeError("请设置每集分镜数");
                  return;
                }
                if (!scriptOptions.style) {
                  setNodeError("请选择视觉风格");
                  return;
                }
                if (!scriptOptions.aspect_ratio) {
                  setNodeError("请选择画幅比例");
                  return;
                }
                setNodeError(null);
              }
              data.onGenerate?.(scriptOptions);
            }}
            onMouseDown={stopNodeDrag}
            style={{
              width: "100%",
              marginTop: isFutureNode ? 6 : 10,
              padding: isFutureNode ? "6px 10px" : "9px 14px",
              background: isLocked
                ? isFutureNode ? "transparent" : "var(--bg-tertiary)"
                : `linear-gradient(135deg, ${cfg.color}, ${cfg.dark})`,
              border: isFutureNode && isLocked ? `1px dashed ${cfg.color}40` : "none",
              color: isLocked ? (isFutureNode ? cfg.dark : "var(--text-tertiary)") : "#fff",
              borderRadius: isFutureNode ? 8 : 10,
              fontSize: isFutureNode ? 10.5 : 11.5,
              fontWeight: 600,
              cursor: isLocked ? "not-allowed" : "pointer",
              opacity: isLocked ? (isFutureNode ? 0.55 : 0.6) : 1,
              transition: "all 0.2s var(--ease-out)",
              fontFamily: "inherit",
              boxShadow: isLocked || isFutureNode ? "none" : `0 4px 12px ${cfg.glow}`,
              letterSpacing: "-0.005em",
            }}
            onMouseEnter={(e) => {
              if (!isLocked) {
                (e.currentTarget as HTMLButtonElement).style.transform = "translateY(-1px)";
                (e.currentTarget as HTMLButtonElement).style.boxShadow = isFutureNode ? `0 3px 10px ${cfg.glow}` : `0 6px 16px ${cfg.glow}`;
              }
            }}
            onMouseLeave={(e) => {
              (e.currentTarget as HTMLButtonElement).style.transform = "translateY(0)";
              if (!isLocked) {
                (e.currentTarget as HTMLButtonElement).style.boxShadow = isFutureNode ? "none" : `0 4px 12px ${cfg.glow}`;
              }
            }}
          >
            {data.generateLabel}
          </button>
        )}
      </div>

      {/* 连接手柄 - 输入 */}
      <Handle
        id="target-left"
        type="target"
        position={Position.Left}
        style={{
          width: 10,
          height: 10,
          background: "#fff",
          border: `2.5px solid ${cfg.color}`,
          borderRadius: "50%",
          left: -5,
          boxShadow: `0 0 0 4px ${cfg.glow}`,
          transition: "all 0.2s var(--ease-out)",
        }}
      />
      {/* 连接手柄 - 输出 */}
      <Handle
        id="source-right"
        type="source"
        position={Position.Right}
        style={{
          width: 10,
          height: 10,
          background: "#fff",
          border: `2.5px solid ${cfg.color}`,
          borderRadius: "50%",
          right: -5,
          boxShadow: `0 0 0 4px ${cfg.glow}`,
          transition: "all 0.2s var(--ease-out)",
        }}
      />

      {/* 纵向连接手柄：剧本 → 角色（向上） / 剧本 → 质检（向下） */}
      {data.type === "script" && (
        <>
          <Handle
            id="source-top"
            type="source"
            position={Position.Top}
            style={{
              width: 10,
              height: 10,
              background: "#fff",
              border: `2.5px solid ${cfg.color}`,
              borderRadius: "50%",
              top: -5,
              boxShadow: `0 0 0 4px ${cfg.glow}`,
              transition: "all 0.2s var(--ease-out)",
            }}
          />
          <Handle
            id="source-bottom"
            type="source"
            position={Position.Bottom}
            style={{
              width: 10,
              height: 10,
              background: "#fff",
              border: `2.5px solid ${cfg.color}`,
              borderRadius: "50%",
              bottom: -5,
              boxShadow: `0 0 0 4px ${cfg.glow}`,
              transition: "all 0.2s var(--ease-out)",
            }}
          />
        </>
      )}

      {/* 角色节点：从剧本顶部接收 */}
      {data.type === "character" && (
        <Handle
          id="target-bottom"
          type="target"
          position={Position.Bottom}
          style={{
            width: 10,
            height: 10,
            background: "#fff",
            border: `2.5px solid ${cfg.color}`,
            borderRadius: "50%",
            bottom: -5,
            boxShadow: `0 0 0 4px ${cfg.glow}`,
            transition: "all 0.2s var(--ease-out)",
          }}
        />
      )}

      {/* 视频节点：向下输出到视觉质检 */}
      {data.type === "video" && (
        <Handle
          id="source-bottom"
          type="source"
          position={Position.Bottom}
          style={{
            width: 10,
            height: 10,
            background: "#fff",
            border: `2.5px solid ${cfg.color}`,
            borderRadius: "50%",
            bottom: -5,
            boxShadow: `0 0 0 4px ${cfg.glow}`,
            transition: "all 0.2s var(--ease-out)",
          }}
        />
      )}

      {/* 质检节点：从剧本/视频底部接收 */}
      {(data.type === "quality" || data.type === "visual_quality") && (
        <Handle
          id="target-top"
          type="target"
          position={Position.Top}
          style={{
            width: 10,
            height: 10,
            background: "#fff",
            border: `2.5px solid ${cfg.color}`,
            borderRadius: "50%",
            top: -5,
            boxShadow: `0 0 0 4px ${cfg.glow}`,
            transition: "all 0.2s var(--ease-out)",
          }}
        />
      )}

      <style>{`
        @keyframes node-spin {
          from { transform: rotate(0deg); }
          to { transform: rotate(360deg); }
        }
        @keyframes node-shimmer {
          0% { transform: translateX(-100%); }
          100% { transform: translateX(100%); }
        }
      `}</style>
    </div>
  );
}

export default memo(DramaNode, (prev, next) => prev.data === next.data);
