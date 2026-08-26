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
import type { DramaNodeData } from "./layout";

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
  // MiniMax 深色主题：color=类型主色（提亮），bg=类型色混入 #1c1c1c 深底，
  // dark=深色下的浅文字色（提亮版类型色），glow=主色 30% 光晕
  script: { color: "#c9b896", bg: "#2a2721", glow: "rgba(201,184,150,0.3)", dark: "#e0d4b8", label: "剧本" },
  character: { color: "#e08ab8", bg: "#2b2126", glow: "rgba(224,138,184,0.3)", dark: "#f0a8d0", label: "角色" },
  storyboard: { color: "#09caf5", bg: "#1d2a2e", glow: "rgba(9,202,245,0.3)", dark: "#5eddff", label: "分镜" },
  video: { color: "#5eb8d4", bg: "#232a2d", glow: "rgba(94,184,212,0.3)", dark: "#8fd4e8", label: "视频" },
  voice: { color: "#7ec98f", bg: "#222924", glow: "rgba(126,201,143,0.3)", dark: "#a5dfb4", label: "配音" },
  subtitle: { color: "#b8a88a", bg: "#282621", glow: "rgba(184,168,138,0.3)", dark: "#d4c8ac", label: "字幕" },
  edit: { color: "#f2664d", bg: "#2c2320", glow: "rgba(242,102,77,0.3)", dark: "#ff8a70", label: "成片" },
  quality: { color: "#f2a93a", bg: "#2c2820", glow: "rgba(242,169,58,0.3)", dark: "#ffc765", label: "质检" },
  visual_quality: { color: "#a8c46a", bg: "#27291f", glow: "rgba(168,196,106,0.3)", dark: "#c4de92", label: "视觉质检" },
};

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
        background: `linear-gradient(135deg, ${cfg.bg}, rgba(255,255,255,0.05))`,
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
            background: "rgba(255,255,255,0.06)",
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
  const Icon = iconMap[data.type] ?? FileText;
  const PlaceholderIcon = placeholderIconMap[data.type] ?? ImageIcon;
  const cfg = getCfg(data.type);

  const [videoError, setVideoError] = useState(false);

  const isFutureNode = !data.hasGenerated && !data.loading && !data.isScriptInput && !data.isEditInput;

  const hasMedia = !!(data.imageUrl || data.videoUrl || data.audioUrl || data.subtitleText);
  const isMediaType = ["character", "storyboard", "video"].includes(data.type);
  const isDimmed = !data.hasGenerated && !data.loading && !data.isScriptInput && !data.isEditInput;

  const statusColor = data.loading
    ? cfg.color
    : data.hasGenerated
    ? "#3dd68c"
    : "#919191";

  const statusLabel = data.statusText
    ? data.statusText
    : data.loading
    ? data.loadingText || "处理中…"
    : data.hasGenerated
    ? "已完成"
    : "等待开始";

  const cardBg = isDimmed ? "rgba(255,255,255,0.03)" : "var(--bg-elevated)";
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
              background: `linear-gradient(90deg, transparent, rgba(255,255,255,0.25), transparent)`,
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
          <Icon size={isFutureNode ? 15 : 18} color="rgba(6,19,26,0.85)" strokeWidth={2} />
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
            ) : (
              <Wand2 size={10} />
            )}
            <span>{statusLabel}</span>
          </div>
        </div>

      </div>

      {/* 内容区域 */}
      <div style={{ padding: contentPadding }}>
        {/* 创意输入区域：节点上仅展示，编辑请前往右侧详情面板 */}
        {data.isScriptInput && !data.hasGenerated && !data.loading && (
          <div
            style={{
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              gap: 10,
              padding: "12px 10px",
              borderRadius: 12,
              background: `linear-gradient(135deg, ${cfg.bg}, rgba(255,255,255,0.05))`,
              border: `1.5px dashed ${cfg.color}40`,
              textAlign: "center",
            }}
          >
            <div
              style={{
                width: 40,
                height: 40,
                borderRadius: 10,
                background: "rgba(255,255,255,0.08)",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                boxShadow: `0 2px 8px ${cfg.glow}`,
              }}
            >
              <Icon size={20} color={cfg.color} strokeWidth={1.8} />
            </div>
            <div style={{ fontSize: 11, color: "var(--text-secondary)", lineHeight: 1.5 }}>
              点击节点在右侧「剧本详情」中输入创意并生成
            </div>
            {data.onOpenDetail && (
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  data.onOpenDetail?.();
                }}
                onMouseDown={stopNodeDrag}
                className="nodrag"
                style={{
                  padding: "7px 16px",
                  background: `linear-gradient(135deg, ${cfg.color}, ${cfg.dark})`,
                  border: "none",
                  color: "rgba(6,19,26,0.88)",
                  borderRadius: 8,
                  fontSize: 11,
                  fontWeight: 600,
                  cursor: "pointer",
                  fontFamily: "inherit",
                  boxShadow: `0 2px 8px ${cfg.glow}`,
                }}
              >
                去详情页编辑
              </button>
            )}
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
                      background: "rgba(61,214,140,0.95)",
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      boxShadow: "0 2px 6px rgba(61,214,140,0.4)",
                    }}
                  >
                    <Check size={12} color="rgba(6,19,26,0.88)" strokeWidth={3} />
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
                    background: "rgba(255,255,255,0.08)",
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
                reason={data.lockReason || undefined}
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
              background: isDimmed ? "rgba(255,255,255,0.04)" : "var(--bg-secondary)",
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
                  background: `linear-gradient(135deg, ${cfg.bg}, rgba(255,255,255,0.05))`,
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
                    background: "rgba(255,255,255,0.08)",
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

        {/* 非媒体类型提示：仅展示流程锁定原因，不提供编辑入口 */}
        {!isMediaType && data.lockReason && !hasMedia && !data.loading && (
          <div
            style={{
              marginTop: 8,
              padding: "8px 10px",
              fontSize: 10,
              color: "var(--text-tertiary)",
              display: "flex",
              alignItems: "center",
              gap: 6,
              background: "rgba(255,255,255,0.06)",
              borderRadius: 8,
            }}
          >
            <Lock size={11} strokeWidth={2} />
            <span>{data.lockReason}</span>
          </div>
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
          background: "var(--bg-elevated)",
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
          background: "var(--bg-elevated)",
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
              background: "var(--bg-elevated)",
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
              background: "var(--bg-elevated)",
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
            background: "var(--bg-elevated)",
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
            background: "var(--bg-elevated)",
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
            background: "var(--bg-elevated)",
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
