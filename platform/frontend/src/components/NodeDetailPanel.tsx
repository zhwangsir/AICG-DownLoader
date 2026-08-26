import { useEffect, useState } from "react";
import { Bot, Loader2 } from "lucide-react";
import { useDramaStore } from "../store/useDramaStore";
import {
  agentAssist,
  type CharacterData,
  type SceneData,
  type SubtitleData,
} from "../api/client";
import type { DramaNodeData } from "./canvas/layout";

/** 剧本生成前端配置项（含后端已有字段 + 前端透传字段）
 * 数值字段允许空字符串，表示节点表单不设置默认值，由用户在点击生成前填写。 */
export interface ScriptGenerateOptions {
  premise: string;
  genre: string;
  episodes: number | "";
  scenes_per_episode: number | "";
  style: string;
  aspect_ratio: string;
}

interface NodeDetailPanelProps {
  nodeId: string;
  type: string;
  data?: DramaNodeData;
  onClose: () => void;
  onGenerate?: (options: ScriptGenerateOptions) => void;
}

export default function NodeDetailPanel({
  nodeId,
  type,
  data,
  onClose,
  onGenerate,
}: NodeDetailPanelProps) {
  const scriptData = useDramaStore((s) => s.scriptData);
  const subtitles = useDramaStore((s) => s.subtitles);
  const updateScriptField = useDramaStore((s) => s.updateScriptField);
  const updateCharacter = useDramaStore((s) => s.updateCharacter);
  const updateScene = useDramaStore((s) => s.updateScene);
  const updateSubtitleSegment = useDramaStore((s) => s.updateSubtitleSegment);
  const globalLoading = useDramaStore((s) => s.globalLoading);

  const charId = type === "character" ? nodeId.replace("char-", "") : null;
  const sceneId =
    type === "storyboard" || type === "video" || type === "voice" || type === "subtitle"
      ? Number(nodeId.split("-")[1])
      : null;

  const character = scriptData?.characters.find((c) => c.character_id === charId);
  const scene = scriptData?.scenes.find((s) => s.scene_id === sceneId);
  const subtitle = subtitles.find((s) => s.scene_id === sceneId);

  const projectStyle = useDramaStore((s) => s.projectStyle);

  const [scriptForm, setScriptForm] = useState({
    title: scriptData?.title || "",
    genre: scriptData?.genre || "",
  });
  const [ideaForm, setIdeaForm] = useState<ScriptGenerateOptions>({
    premise: "",
    genre: "",
    episodes: "",
    scenes_per_episode: "",
    style: "",
    aspect_ratio: "",
  });
  const [charForm, setCharForm] = useState<CharacterData | null>(character || null);
  const [sceneForm, setSceneForm] = useState<SceneData | null>(scene || null);
  const [subtitleForm, setSubtitleForm] = useState<SubtitleData | null>(subtitle || null);
  const [formError, setFormError] = useState<string | null>(null);

  useEffect(() => {
    setScriptForm({
      title: scriptData?.title || "",
      genre: scriptData?.genre || "",
    });
    // 剧本生成后，把实际总集数/分镜数回显到 ideaForm，便于用户再次编辑并重新生成
    if (scriptData) {
      setIdeaForm((prev) => ({
        ...prev,
        genre: scriptData.genre || prev.genre,
        episodes: scriptData.total_episodes || prev.episodes,
        scenes_per_episode:
          scriptData.scenes.length > 0
            ? Math.max(
                1,
                Math.round(scriptData.scenes.length / Math.max(1, scriptData.total_episodes))
              )
            : prev.scenes_per_episode,
      }));
    }
  }, [scriptData?.title, scriptData?.genre, scriptData?.total_episodes, scriptData?.scenes?.length]);

  useEffect(() => {
    setCharForm(character || null);
  }, [character]);

  useEffect(() => {
    setSceneForm(scene || null);
  }, [scene]);

  useEffect(() => {
    setSubtitleForm(subtitle || null);
  }, [subtitle]);

  const handleSave = () => {
    if (type === "script" && scriptData) {
      updateScriptField({ title: scriptForm.title, genre: scriptForm.genre });
    }
    if (type === "character" && charForm && charId) {
      updateCharacter(charId, charForm);
    }
    if (
      (type === "storyboard" || type === "video" || type === "voice") &&
      sceneForm &&
      sceneId
    ) {
      updateScene(sceneId, sceneForm);
    }
    if (type === "subtitle" && subtitleForm && sceneId) {
      subtitleForm.segments.forEach((seg, idx) => {
        updateSubtitleSegment(sceneId, idx, seg.text);
      });
    }
    onClose();
  };

  const validateScriptForm = (
    form: ScriptGenerateOptions,
    opts?: { skipPremise?: boolean }
  ): string | null => {
    // 已有剧本态无 premise 输入框（用现有剧本重新生成），跳过创意校验
    if (!opts?.skipPremise && !form.premise.trim()) return "请输入一句话创意";
    if (!form.genre.trim()) return "请输入题材";
    if (form.episodes === "") return "请设置集数";
    if (form.scenes_per_episode === "") return "请设置每集分镜数";
    if (!form.style) return "请选择视觉风格";
    if (!form.aspect_ratio) return "请选择画幅比例";
    return null;
  };

  const titleMap: Record<string, string> = {
    script: "剧本详情",
    character: "角色详情",
    storyboard: "分镜详情",
    video: "视频生成详情",
    voice: "配音详情",
    subtitle: "字幕详情",
    edit: "成片详情",
    quality: "质检详情",
    visual_quality: "视觉质检详情",
  };

  return (
    <div className="side-panel" onClick={(e) => e.stopPropagation()}>
      <div className="side-panel-header">
        <div className="side-panel-title">
          {titleMap[type] || "节点详情"}
        </div>
        <button className="panel-btn side-panel-close" onClick={onClose}>
          收起
        </button>
      </div>

      <div className="side-panel-content">
        {data && (
          <div className="panel-section" style={{ background: "var(--bg-secondary)", borderRadius: 12, padding: 14, marginBottom: 16 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
              <div
                style={{
                  width: 8,
                  height: 8,
                  borderRadius: "50%",
                  background: data.hasGenerated ? "var(--success)" : data.loading ? "var(--warning)" : "var(--text-disabled)",
                }}
              />
              <div style={{ fontSize: 12, fontWeight: 600, color: "var(--text-secondary)" }}>
                {data.statusText || (data.hasGenerated ? "已完成" : data.loading ? "生成中" : "待开始")}
              </div>
            </div>
            {data.tags && data.tags.length > 0 && (
              <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 10 }}>
                {data.tags.map((tag, idx) => (
                  <span
                    key={idx}
                    style={{
                      padding: "3px 8px",
                      borderRadius: 6,
                      fontSize: 10,
                      fontWeight: 600,
                      color: "var(--text-secondary)",
                      background: "var(--bg-elevated)",
                      border: "1px solid var(--border-light)",
                    }}
                  >
                    {tag}
                  </span>
                ))}
              </div>
            )}
            {data.meta && data.meta.length > 0 && (
              <div style={{ display: "grid", gridTemplateColumns: "repeat(2, 1fr)", gap: "6px 10px", marginBottom: data.preview ? 10 : 0 }}>
                {data.meta.map((m, idx) => (
                  <div key={idx}>
                    <div style={{ fontSize: 9, color: "var(--text-tertiary)", textTransform: "uppercase", letterSpacing: "0.04em" }}>
                      {m.label}
                    </div>
                    <div style={{ fontSize: 11, fontWeight: 600, color: "var(--text-primary)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                      {m.value}
                    </div>
                  </div>
                ))}
              </div>
            )}
            {data.preview && (
              <div style={{ fontSize: 12, color: "var(--text-secondary)", lineHeight: 1.6, display: "-webkit-box", WebkitLineClamp: 4, WebkitBoxOrient: "vertical", overflow: "hidden" }}>
                {data.preview}
              </div>
            )}
          </div>
        )}

        {type === "script" && scriptData && (
          <div className="panel-section">
            <label className="panel-label" style={{ marginTop: 0 }}>标题</label>
            <input
              className="panel-input"
              value={scriptForm.title}
              onChange={(e) =>
                setScriptForm((prev) => ({ ...prev, title: e.target.value }))
              }
            />
            <AgentAssistToolbar
              text={scriptForm.title}
              context="script"
              extraInstruction="标题更吸引人，简洁有力"
              onApply={(text) =>
                setScriptForm((prev) => ({ ...prev, title: text }))
              }
            />
            <label className="panel-label">题材</label>
            <input
              className="panel-input"
              value={scriptForm.genre}
              onChange={(e) =>
                setScriptForm((prev) => ({ ...prev, genre: e.target.value }))
              }
            />
            <AgentAssistToolbar
              text={scriptForm.genre}
              context="script"
              extraInstruction="题材描述更精准，便于后续生成"
              onApply={(text) =>
                setScriptForm((prev) => ({ ...prev, genre: text }))
              }
            />
            <ScriptGlobalControls
              form={ideaForm}
              onChange={setIdeaForm}
              hint="修改后点击「保存并重新生成」生效"
            />
          </div>
        )}

        {type === "script" && !scriptData && (
          <div className="panel-section">
            <label className="panel-label" style={{ marginTop: 0 }}>一句话创意</label>
            <textarea
              className="panel-textarea"
              rows={4}
              value={ideaForm.premise}
              onChange={(e) =>
                setIdeaForm((prev) => ({ ...prev, premise: e.target.value }))
              }
            />
            <AgentAssistToolbar
              text={ideaForm.premise}
              context="script"
              extraInstruction="围绕核心创意展开，增强画面感与冲突"
              onApply={(text) =>
                setIdeaForm((prev) => ({ ...prev, premise: text }))
              }
            />
            <label className="panel-label">题材</label>
            <input
              className="panel-input"
              value={ideaForm.genre}
              onChange={(e) =>
                setIdeaForm((prev) => ({ ...prev, genre: e.target.value }))
              }
            />
            <AgentAssistToolbar
              text={ideaForm.genre}
              context="script"
              extraInstruction="题材描述更精准，便于后续生成"
              onApply={(text) =>
                setIdeaForm((prev) => ({ ...prev, genre: text }))
              }
            />
            <ScriptGlobalControls form={ideaForm} onChange={setIdeaForm} />
          </div>
        )}

        {type === "character" && charForm && (
          <div className="panel-section">
            <label className="panel-label" style={{ marginTop: 0 }}>角色名</label>
            <input
              className="panel-input"
              value={charForm.name}
              onChange={(e) =>
                setCharForm((prev) => (prev ? { ...prev, name: e.target.value } : prev))
              }
            />
            <AgentAssistToolbar
              text={charForm.name}
              context="character"
              extraInstruction="角色名更贴合题材与性格"
              onApply={(text) =>
                setCharForm((prev) => (prev ? { ...prev, name: text } : prev))
              }
            />
            <label className="panel-label">身份</label>
            <input
              className="panel-input"
              value={charForm.role}
              onChange={(e) =>
                setCharForm((prev) => (prev ? { ...prev, role: e.target.value } : prev))
              }
            />
            <AgentAssistToolbar
              text={charForm.role}
              context="character"
              extraInstruction="身份描述更鲜明"
              onApply={(text) =>
                setCharForm((prev) => (prev ? { ...prev, role: text } : prev))
              }
            />
            <label className="panel-label">年龄</label>
            <input
              className="panel-input"
              type="number"
              value={charForm.age ?? ""}
              onChange={(e) =>
                // 表单项仅在 charForm 非空时渲染，prev 恒非 null
                setCharForm((prev) => ({
                  ...prev!,
                  age: e.target.value ? Number(e.target.value) : null,
                }))
              }
            />
            <label className="panel-label">外貌描述</label>
            <textarea
              className="panel-textarea"
              value={charForm.description}
              onChange={(e) =>
                setCharForm((prev) =>
                  prev ? { ...prev, description: e.target.value } : prev
                )
              }
            />
            <AgentAssistToolbar
              text={charForm.description}
              context="character"
              extraInstruction="增加外貌细节与视觉特征，便于生成一致形象"
              onApply={(text) =>
                setCharForm((prev) =>
                  prev ? { ...prev, description: text } : prev
                )
              }
            />
            <label className="panel-label">性格</label>
            <textarea
              className="panel-textarea"
              value={charForm.personality}
              onChange={(e) =>
                setCharForm((prev) =>
                  prev ? { ...prev, personality: e.target.value } : prev
                )
              }
            />
            <AgentAssistToolbar
              text={charForm.personality}
              context="character"
              extraInstruction="性格更立体，便于配音与表演"
              onApply={(text) =>
                setCharForm((prev) =>
                  prev ? { ...prev, personality: text } : prev
                )
              }
            />
          </div>
        )}

        {(type === "storyboard" || type === "video" || type === "voice") && sceneForm && (
          <div className="panel-section">
            <label className="panel-label" style={{ marginTop: 0 }}>场景编号</label>
            <div style={{ fontSize: 14, fontWeight: 500, color: "var(--text-primary)", marginBottom: 8 }}>
              场景 {sceneForm.scene_id}
            </div>
            {type === "storyboard" && (
              <>
                <label className="panel-label">镜头类型</label>
                <select
                  className="panel-select"
                  value={sceneForm.shot_type}
                  onChange={(e) =>
                    setSceneForm((prev) =>
                      prev ? { ...prev, shot_type: e.target.value } : prev
                    )
                  }
                >
                  {["特写", "近景", "中景", "远景"].map((t) => (
                    <option key={t} value={t}>
                      {t}
                    </option>
                  ))}
                </select>
                <label className="panel-label">画面描述</label>
                <textarea
                  className="panel-textarea"
                  value={sceneForm.description}
                  onChange={(e) =>
                    setSceneForm((prev) =>
                      prev ? { ...prev, description: e.target.value } : prev
                    )
                  }
                />
                <AgentAssistToolbar
                  text={sceneForm.description}
                  context="storyboard"
                  extraInstruction="增强画面感与镜头语言"
                  onApply={(text) =>
                    setSceneForm((prev) =>
                      prev ? { ...prev, description: text } : prev
                    )
                  }
                />
                <label className="panel-label">角色动作</label>
                <textarea
                  className="panel-textarea"
                  value={sceneForm.character_actions}
                  onChange={(e) =>
                    setSceneForm((prev) =>
                      prev ? { ...prev, character_actions: e.target.value } : prev
                    )
                  }
                />
                <AgentAssistToolbar
                  text={sceneForm.character_actions}
                  context="storyboard"
                  extraInstruction="动作描述更具体、有戏剧张力"
                  onApply={(text) =>
                    setSceneForm((prev) =>
                      prev ? { ...prev, character_actions: text } : prev
                    )
                  }
                />
                <label className="panel-label">情绪</label>
                <input
                  className="panel-input"
                  value={sceneForm.emotion}
                  onChange={(e) =>
                    setSceneForm((prev) =>
                      prev ? { ...prev, emotion: e.target.value } : prev
                    )
                  }
                />
                <AgentAssistToolbar
                  text={sceneForm.emotion}
                  context="storyboard"
                  extraInstruction="情绪表达更精准"
                  onApply={(text) =>
                    setSceneForm((prev) =>
                      prev ? { ...prev, emotion: text } : prev
                    )
                  }
                />
                <label className="panel-label">运镜</label>
                <input
                  className="panel-input"
                  value={sceneForm.camera_movement}
                  onChange={(e) =>
                    setSceneForm((prev) =>
                      prev ? { ...prev, camera_movement: e.target.value } : prev
                    )
                  }
                />
                <AgentAssistToolbar
                  text={sceneForm.camera_movement}
                  context="storyboard"
                  extraInstruction="运镜描述更专业、有电影感"
                  onApply={(text) =>
                    setSceneForm((prev) =>
                      prev ? { ...prev, camera_movement: text } : prev
                    )
                  }
                />
              </>
            )}

            <label className="panel-label">正面提示词</label>
            <textarea
              className="panel-textarea"
              value={sceneForm.prompt}
              onChange={(e) =>
                setSceneForm((prev) =>
                  prev ? { ...prev, prompt: e.target.value } : prev
                )
              }
            />
            <AgentAssistToolbar
              text={sceneForm.prompt}
              context="video"
              extraInstruction="优化为英文提示词，提升生成质量"
              onApply={(text) =>
                setSceneForm((prev) =>
                  prev ? { ...prev, prompt: text } : prev
                )
              }
            />
            <label className="panel-label">反向提示词</label>
            <textarea
              className="panel-textarea"
              value={sceneForm.negative_prompt}
              onChange={(e) =>
                setSceneForm((prev) =>
                  prev ? { ...prev, negative_prompt: e.target.value } : prev
                )
              }
            />
            <AgentAssistToolbar
              text={sceneForm.negative_prompt}
              context="video"
              extraInstruction="精简并保留最关键的排除项"
              onApply={(text) =>
                setSceneForm((prev) =>
                  prev ? { ...prev, negative_prompt: text } : prev
                )
              }
            />
            <label className="panel-label">时长（秒）</label>
            <input
              className="panel-input"
              type="number"
              value={sceneForm.duration_seconds}
              onChange={(e) =>
                // 表单项仅在 sceneForm 非空时渲染，prev 恒非 null
                setSceneForm((prev) => ({
                  ...prev!,
                  duration_seconds: Number(e.target.value) || 0,
                }))
              }
            />

            {type === "voice" && (
              <>
                <label className="panel-label">场景台词</label>
                <textarea
                  className="panel-textarea"
                  style={{ minHeight: 120 }}
                  value={sceneForm.dialogue}
                  onChange={(e) =>
                    setSceneForm((prev) =>
                      prev ? { ...prev, dialogue: e.target.value } : prev
                    )
                  }
                />
                <AgentAssistToolbar
                  text={sceneForm.dialogue}
                  context="voice"
                  extraInstruction="台词更口语化、符合角色性格"
                  onApply={(text) =>
                    setSceneForm((prev) =>
                      prev ? { ...prev, dialogue: text } : prev
                    )
                  }
                />
              </>
            )}
          </div>
        )}

        {type === "subtitle" && subtitleForm && (
          <div className="panel-section">
            <div className="panel-label" style={{ marginTop: 0 }}>
              字幕段数：{subtitleForm.segments.length}
            </div>
            {subtitleForm.segments.map((seg, idx) => (
              <div key={idx} style={{ marginTop: 10 }}>
                <div className="panel-label">
                  #{idx + 1} {formatTime(seg.start)} → {formatTime(seg.end)}
                </div>
                <textarea
                  className="panel-textarea"
                  value={seg.text}
                  onChange={(e) =>
                    setSubtitleForm((prev) => {
                      if (!prev) return prev;
                      const segments = [...prev.segments];
                      segments[idx] = { ...segments[idx], text: e.target.value };
                      return { ...prev, segments };
                    })
                  }
                />
                <AgentAssistToolbar
                  text={seg.text}
                  context="subtitle"
                  extraInstruction="字幕更简洁、与画面节奏匹配"
                  onApply={(text) =>
                    setSubtitleForm((prev) => {
                      if (!prev) return prev;
                      const segments = [...prev.segments];
                      segments[idx] = { ...segments[idx], text };
                      return { ...prev, segments };
                    })
                  }
                />
              </div>
            ))}
          </div>
        )}

        {type === "edit" && (
          <div className="panel-section">
            <div className="panel-meta-text">
              成片节点仅展示最终合成结果。如需修改，请调整上游场景后重新合成。
            </div>
          </div>
        )}
        {type === "quality" && (
          <div className="panel-section">
            <div className="panel-meta-text">
              质检节点展示剧本与字幕的自动检查结果，不支持直接编辑。
            </div>
          </div>
        )}
        {type === "visual_quality" && (
          <div className="panel-section">
            <div className="panel-meta-text">
              视觉质检节点展示视频画面的自动检查结果，不支持直接编辑。
            </div>
          </div>
        )}
      </div>

      <div className="side-panel-footer">
        {formError && (
          <div
            style={{
              width: "100%",
              marginBottom: 10,
              padding: "8px 10px",
              borderRadius: 8,
              fontSize: 11,
              color: "#a04848",
              background: "rgba(160,72,72,0.08)",
              border: "1px solid rgba(160,72,72,0.2)",
            }}
          >
            {formError}
          </div>
        )}
        {type === "script" && !scriptData ? (
          <button
            className="panel-btn panel-btn-primary"
            onClick={() => {
              const err = validateScriptForm(ideaForm);
              if (err) {
                setFormError(err);
                return;
              }
              setFormError(null);
              onGenerate?.(ideaForm);
              onClose();
            }}
            disabled={globalLoading}
          >
            {globalLoading ? "生成中..." : "生成剧本"}
          </button>
        ) : (
          <>
            <button className="panel-btn panel-btn-primary" onClick={handleSave}>
              保存修改
            </button>
            {onGenerate && (
              <button
                className="panel-btn"
                onClick={() => {
                  const err = validateScriptForm(ideaForm, { skipPremise: true });
                  if (err) {
                    setFormError(err);
                    return;
                  }
                  setFormError(null);
                  handleSave();
                  onGenerate(ideaForm);
                }}
                disabled={globalLoading}
              >
                {globalLoading ? "生成中..." : "保存并重新生成"}
              </button>
            )}
          </>
        )}
        <button className="panel-btn" onClick={onClose}>
          取消
        </button>
      </div>
    </div>
  );
}

/** 视觉风格预设（与 OpenShortVideo / SkyReels 等行业实践对齐） */
const STYLE_PRESETS = [
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
];

/** 画幅比例预设 */
const ASPECT_RATIO_PRESETS = ["9:16", "16:9", "1:1"];

interface ScriptGlobalControlsProps {
  form: ScriptGenerateOptions;
  onChange: (patch: ScriptGenerateOptions) => void;
  hint?: string;
}

function ScriptGlobalControls({ form, onChange, hint }: ScriptGlobalControlsProps) {
  const update = <K extends keyof ScriptGenerateOptions>(key: K, value: ScriptGenerateOptions[K]) => {
    onChange({ ...form, [key]: value });
  };

  return (
    <>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, marginTop: 12 }}>
        <div>
          <label className="panel-label">集数</label>
          <input
            className="panel-input"
            type="number"
            min={1}
            max={100}
            placeholder="请输入"
            value={form.episodes}
            onChange={(e) => {
              const raw = e.target.value;
              update("episodes", raw === "" ? "" : Math.max(1, Math.min(100, Number(raw) || 1)));
            }}
          />
        </div>
        <div>
          <label className="panel-label">每集分镜数</label>
          <input
            className="panel-input"
            type="number"
            min={1}
            max={30}
            placeholder="请输入"
            value={form.scenes_per_episode}
            onChange={(e) => {
              const raw = e.target.value;
              update(
                "scenes_per_episode",
                raw === "" ? "" : Math.max(1, Math.min(30, Number(raw) || 1))
              );
            }}
          />
        </div>
      </div>

      <label className="panel-label">视觉风格</label>
      <select
        className="panel-select"
        value={form.style}
        onChange={(e) => update("style", e.target.value)}
      >
        <option value="" disabled>
          请选择视觉风格
        </option>
        {STYLE_PRESETS.map((s) => (
          <option key={s} value={s}>
            {s}
          </option>
        ))}
      </select>

      <label className="panel-label">画幅比例</label>
      <select
        className="panel-select"
        value={form.aspect_ratio}
        onChange={(e) => update("aspect_ratio", e.target.value)}
      >
        <option value="" disabled>
          请选择画幅比例
        </option>
        {ASPECT_RATIO_PRESETS.map((r) => (
          <option key={r} value={r}>
            {r}
          </option>
        ))}
      </select>

      {hint && (
        <div style={{ fontSize: 11, color: "var(--text-tertiary)", marginTop: 8 }}>
          {hint}
        </div>
      )}
    </>
  );
}

function formatTime(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  const ms = Math.floor((seconds % 1) * 1000);
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}.${String(ms).padStart(3, "0")}`;
}

interface AgentAssistToolbarProps {
  text: string;
  onApply: (text: string) => void;
  context: string;
  extraInstruction?: string;
  disabled?: boolean;
}

function AgentAssistToolbar({
  text,
  onApply,
  context,
  extraInstruction,
  disabled,
}: AgentAssistToolbarProps) {
  const [loadingAction, setLoadingAction] = useState<string | null>(null);

  const handleAction = async (
    action: "polish" | "expand" | "shorten" | "rewrite"
  ) => {
    if (!text.trim() || loadingAction || disabled) return;
    setLoadingAction(action);
    try {
      const resp = await agentAssist({
        text,
        context,
        action,
        extra_instruction: extraInstruction,
      });
      if (resp.success && resp.data) {
        onApply(resp.data.text);
      }
    } finally {
      setLoadingAction(null);
    }
  };

  const actions: { key: "polish" | "expand" | "shorten" | "rewrite"; label: string }[] = [
    { key: "polish", label: "润色" },
    { key: "expand", label: "扩写" },
    { key: "shorten", label: "精简" },
    { key: "rewrite", label: "改写" },
  ];

  return (
    <div style={{ display: "flex", gap: 6, marginTop: 6, flexWrap: "wrap" }}>
      {actions.map(({ key, label }) => {
        const loading = loadingAction === key;
        return (
          <button
            key={key}
            type="button"
            onClick={() => handleAction(key)}
            disabled={!text.trim() || loadingAction !== null || disabled}
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 4,
              padding: "4px 10px",
              borderRadius: 6,
              border: "1px solid var(--border-light)",
              background: "var(--bg-elevated)",
              color: "var(--text-secondary)",
              fontSize: 11,
              fontWeight: 600,
              cursor: !text.trim() || loadingAction !== null || disabled ? "not-allowed" : "pointer",
              opacity: !text.trim() || loadingAction !== null || disabled ? 0.55 : 1,
              transition: "all 0.15s var(--ease-out)",
              fontFamily: "inherit",
            }}
            onMouseEnter={(e) => {
              if (text.trim() && !loadingAction && !disabled) {
                (e.currentTarget as HTMLButtonElement).style.borderColor = "var(--primary)";
                (e.currentTarget as HTMLButtonElement).style.color = "var(--primary)";
                (e.currentTarget as HTMLButtonElement).style.background = "rgba(9,202,245,0.08)";
              }
            }}
            onMouseLeave={(e) => {
              (e.currentTarget as HTMLButtonElement).style.borderColor = "var(--border-light)";
              (e.currentTarget as HTMLButtonElement).style.color = "var(--text-secondary)";
              (e.currentTarget as HTMLButtonElement).style.background = "var(--bg-elevated)";
            }}
          >
            {loading ? (
              <Loader2 size={11} style={{ animation: "spin 1s linear infinite" }} />
            ) : (
              <Bot size={11} strokeWidth={2} />
            )}
            <span>{label}</span>
          </button>
        );
      })}
    </div>
  );
}
