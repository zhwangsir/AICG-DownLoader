import { useEffect, useState } from "react";
import {
  generateStoryboard,
  CharacterData,
  SceneData,
  StoryboardData,
} from "../../api/client";
import { useDramaStore } from "../../store/useDramaStore";
import { Check, Image, Sparkles } from "../ui/Icon";
import { PromptToolkit } from "../common/PromptToolkit";
import {
  STYLE_OPTIONS,
  modalScrollStyle,
  sectionStyle,
  sectionTitleStyle,
  textareaStyle,
  ComboInput,
} from "./shared";

/** M25.9 C1 线稿预览（线稿先行两段式确认流：先看构图，确认后同 seed 精绘防漂移）。 */
interface SketchPreview {
  image_url: string;
  sketch_seed: number;
  prompt_used: string;
}

export function StoryboardModal({
  scenes,
  characters,
  onClose,
  onSuccess,
}: {
  scenes: SceneData[];
  characters: CharacterData[];
  onClose: () => void;
  onSuccess: (data: StoryboardData) => void;
}) {
  const updateScene = useDramaStore((s) => s.updateScene);
  const [selectedSceneId, setSelectedSceneId] = useState<number | null>(scenes[0]?.scene_id || null);
  const [editDescription, setEditDescription] = useState(scenes[0]?.description || "");
  const [editPrompt, setEditPrompt] = useState(scenes[0]?.prompt || "");
  const [style, setStyle] = useState("写实电影感");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  // M25.9 C1 线稿先行：手动分镜修正场景默认开启两段式确认流
  const [sketchFirst, setSketchFirst] = useState(true);
  const [sketchPreview, setSketchPreview] = useState<SketchPreview | null>(null);

  const selectedScene = scenes.find((s) => s.scene_id === selectedSceneId) || null;

  useEffect(() => {
    if (selectedScene) {
      setEditDescription(selectedScene.description);
      setEditPrompt(selectedScene.prompt);
      // 切换场景时丢弃上一个场景的线稿（seed 不跨场景复用）
      setSketchPreview(null);
      setError(null);
    }
  }, [selectedSceneId]);

  /** 统一调用分镜生成；sketch 参数化见 M25.9 C1。 */
  const runGenerate = async (opts: { sketchMode: boolean; refineSeed?: number }) => {
    if (!selectedScene) return;
    setLoading(true);
    setError(null);
    try {
      const resp = await generateStoryboard({
        scene: { ...selectedScene, description: editDescription, prompt: editPrompt },
        characters,
        style,
        sketch_mode: opts.sketchMode,
        refine_seed: opts.refineSeed ?? null,
      });
      if (resp.success && resp.data) {
        if (opts.sketchMode) {
          // 线稿阶段：仅展示预览，不写回 store，等待用户确认构图
          setSketchPreview({
            image_url: resp.data.image_url,
            sketch_seed: resp.data.sketch_seed ?? 0,
            prompt_used: resp.data.prompt_used,
          });
        } else {
          // 精绘/直出阶段：正式结果回写
          onSuccess(resp.data);
        }
      } else {
        setError(resp.error || "生成失败");
      }
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  };

  /** 一段式直出（线稿先行关闭）。 */
  const handleGenerate = () => runGenerate({ sketchMode: false });
  /** 两段式第一步：低步数线稿快速看构图。 */
  const handleSketch = () => runGenerate({ sketchMode: true });
  /** 两段式第二步：采用线稿构图，同 seed 精绘（防构图漂移）。 */
  const handleRefine = () => {
    if (!sketchPreview) return;
    runGenerate({ sketchMode: false, refineSeed: sketchPreview.sketch_seed });
    setSketchPreview(null);
  };

  const handleSave = () => {
    if (!selectedScene) return;
    updateScene(selectedScene.scene_id, {
      description: editDescription,
      prompt: editPrompt,
    });
    setSaved(true);
    setTimeout(() => setSaved(false), 1500);
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" style={modalScrollStyle} onClick={(e) => e.stopPropagation()}>
        <div className="modal-title">生成分镜关键帧</div>
        {scenes.length === 0 ? (
          <div style={{ color: "var(--text-secondary)", fontSize: "13px" }}>
            请先生成剧本，分镜将从剧本场景中提取。
          </div>
        ) : (
          <>
            <div className="modal-field">
              <label className="modal-label">选择场景</label>
              <select
                className="modal-input"
                value={selectedSceneId ?? ""}
                onChange={(e) => setSelectedSceneId(Number(e.target.value))}
              >
                {scenes.map((s) => (
                  <option key={s.scene_id} value={s.scene_id}>
                    场景 {s.scene_id} — {s.shot_type}（{s.duration_seconds}s）
                  </option>
                ))}
              </select>
            </div>
            <div className="modal-field">
              <label className="modal-label">画风</label>
              <ComboInput value={style} onChange={setStyle} options={STYLE_OPTIONS} />
            </div>
            <div className="modal-field">
              <label
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "6px",
                  fontSize: "12px",
                  color: "var(--text-secondary)",
                  cursor: "pointer",
                }}
              >
                <input
                  type="checkbox"
                  checked={sketchFirst}
                  onChange={(e) => {
                    setSketchFirst(e.target.checked);
                    setSketchPreview(null);
                  }}
                  disabled={loading}
                />
                线稿先行（低步数快速看构图，确认后同 seed 精绘防漂移）
              </label>
            </div>
            {selectedScene && (
              <div style={sectionStyle}>
                <div style={sectionTitleStyle}>场景信息（可编辑）</div>
                <div
                  style={{
                    fontSize: "11px",
                    color: "var(--text-secondary)",
                    marginBottom: "4px",
                  }}
                >
                  场景 {selectedScene.scene_id} · {selectedScene.shot_type} · {selectedScene.emotion}
                </div>
                <label style={{ fontSize: "11px", color: "var(--text-secondary)" }}>场景描述</label>
                <textarea
                  style={textareaStyle}
                  value={editDescription}
                  onChange={(e) => setEditDescription(e.target.value)}
                />
                <label
                  style={{
                    fontSize: "11px",
                    color: "var(--text-secondary)",
                    marginTop: "6px",
                    display: "block",
                  }}
                >
                  提示词
                </label>
                <textarea
                  style={textareaStyle}
                  value={editPrompt}
                  onChange={(e) => setEditPrompt(e.target.value)}
                />
                <PromptToolkit
                  text={editPrompt}
                  onChange={setEditPrompt}
                  context="短剧分镜画面提示词"
                  disabled={loading}
                />
                {selectedScene.dialogue && (
                  <div
                    style={{
                      marginTop: "6px",
                      fontSize: "12px",
                      color: "var(--text-secondary)",
                      fontStyle: "italic",
                    }}
                  >
                    「{selectedScene.dialogue}」
                  </div>
                )}
              </div>
            )}
            {sketchFirst && sketchPreview && (
              <div style={sectionStyle}>
                <div style={sectionTitleStyle}>
                  <span style={{ display: "inline-flex", alignItems: "center", gap: "4px" }}>
                    <Image size={13} /> 线稿预览 · seed {sketchPreview.sketch_seed}
                  </span>
                </div>
                <img
                  src={sketchPreview.image_url}
                  alt="分镜线稿预览"
                  style={{
                    width: "100%",
                    borderRadius: "6px",
                    border: "1px solid var(--border)",
                    display: "block",
                  }}
                />
                <div
                  style={{
                    fontSize: "11px",
                    color: "var(--text-secondary)",
                    marginTop: "4px",
                  }}
                >
                  构图满意则「采用构图并精绘」，不满意可改提示词后「重出线稿」。
                </div>
                <div style={{ display: "flex", gap: "8px", marginTop: "8px" }}>
                  <button
                    className="topbar-btn topbar-btn-primary"
                    onClick={handleRefine}
                    disabled={loading}
                    style={{ flex: 1 }}
                  >
                    {loading ? (
                      <span className="loading"></span>
                    ) : (
                      <span style={{ display: "inline-flex", alignItems: "center", gap: "4px" }}>
                        <Sparkles size={14} /> 采用构图并精绘
                      </span>
                    )}
                  </button>
                  <button
                    className="topbar-btn"
                    onClick={handleSketch}
                    disabled={loading}
                    style={{ flex: 1 }}
                  >
                    重出线稿
                  </button>
                  <button
                    className="topbar-btn"
                    onClick={() => setSketchPreview(null)}
                    disabled={loading}
                  >
                    弃用
                  </button>
                </div>
              </div>
            )}
          </>
        )}
        {error && (
          <div style={{ color: "var(--error)", fontSize: "12px", marginTop: "8px" }}>{error}</div>
        )}
        <div className="modal-actions">
          <button className="topbar-btn" onClick={onClose}>
            取消
          </button>
          {selectedScene && (
            <button className="topbar-btn" onClick={handleSave}>
              {saved ? (
                <span style={{ display: "inline-flex", alignItems: "center", gap: "4px" }}>
                  <Check size={14} strokeWidth={2.5} /> 已保存
                </span>
              ) : (
                "保存场景修改"
              )}
            </button>
          )}
          <button
            className="topbar-btn topbar-btn-primary"
            onClick={sketchFirst ? handleSketch : handleGenerate}
            disabled={loading || !selectedScene}
          >
            {loading ? <span className="loading"></span> : sketchFirst ? "生成线稿" : "生成分镜"}
          </button>
        </div>
      </div>
    </div>
  );
}
