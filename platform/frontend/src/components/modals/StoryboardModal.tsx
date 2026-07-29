import { useEffect, useState } from "react";
import {
  generateStoryboard,
  CharacterData,
  SceneData,
  StoryboardData,
} from "../../api/client";
import { useDramaStore } from "../../store/useDramaStore";
import { Check } from "../ui/Icon";
import {
  STYLE_OPTIONS,
  modalScrollStyle,
  sectionStyle,
  sectionTitleStyle,
  textareaStyle,
  ComboInput,
} from "./shared";

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

  const selectedScene = scenes.find((s) => s.scene_id === selectedSceneId) || null;

  useEffect(() => {
    if (selectedScene) {
      setEditDescription(selectedScene.description);
      setEditPrompt(selectedScene.prompt);
    }
  }, [selectedSceneId]);

  const handleGenerate = async () => {
    if (!selectedScene) return;
    setLoading(true);
    setError(null);
    try {
      const resp = await generateStoryboard({
        scene: { ...selectedScene, description: editDescription, prompt: editPrompt },
        characters,
        style,
      });
      if (resp.success && resp.data) {
        onSuccess(resp.data);
      } else {
        setError(resp.error || "生成失败");
      }
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
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
            onClick={handleGenerate}
            disabled={loading || !selectedScene}
          >
            {loading ? <span className="loading"></span> : "生成分镜"}
          </button>
        </div>
      </div>
    </div>
  );
}
