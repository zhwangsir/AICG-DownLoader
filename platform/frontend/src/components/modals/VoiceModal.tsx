import { useEffect, useState } from "react";
import {
  generateVoice,
  CharacterData,
  SceneData,
  VoiceData,
} from "../../api/client";
import {
  VOICE_OPTIONS,
  RATE_OPTIONS,
  modalScrollStyle,
  itemBoxStyle,
  textareaStyle,
  compactInputStyle,
  smallBtnStyle,
  sectionTitleStyle,
} from "./shared";
import type { DialogueLine } from "./shared";

export function VoiceModal({
  scenes,
  characters,
  onClose,
  onSuccess,
}: {
  scenes: SceneData[];
  characters: CharacterData[];
  onClose: () => void;
  onSuccess: (data: VoiceData) => void;
}) {
  const [selectedSceneId, setSelectedSceneId] = useState<number | null>(scenes[0]?.scene_id || null);
  const [dialogues, setDialogues] = useState<DialogueLine[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const selectedScene = scenes.find((s) => s.scene_id === selectedSceneId) || null;

  const buildDialogues = (scene: SceneData | null): DialogueLine[] => {
    if (!scene || !scene.dialogue) return [];
    const lines = scene.dialogue.split(/[，。！？\n]/).filter((t) => t.trim().length > 1);
    if (lines.length === 0) return [];
    return lines.map((text, i) => {
      const speaker = characters[i % Math.max(characters.length, 1)];
      return {
        text: text.trim(),
        character_name: speaker?.name || `角色${i + 1}`,
        character_role: speaker?.role || "",
        character_age: speaker?.age ?? null,
        rate: "+0%",
        voice: VOICE_OPTIONS[i % VOICE_OPTIONS.length].value,
      };
    });
  };

  useEffect(() => {
    setDialogues(buildDialogues(selectedScene));
  }, [selectedSceneId]);

  const updateLine = (idx: number, patch: Partial<DialogueLine>) => {
    setDialogues((prev) => prev.map((d, i) => (i === idx ? { ...d, ...patch } : d)));
  };

  const onCharacterChange = (idx: number, name: string) => {
    const c = characters.find((ch) => ch.name === name);
    if (c) {
      updateLine(idx, { character_name: name, character_role: c.role, character_age: c.age });
    } else {
      updateLine(idx, { character_name: name });
    }
  };

  const addLine = () => {
    setDialogues((prev) => [
      ...prev,
      {
        text: "",
        character_name: characters[0]?.name || "角色",
        character_role: characters[0]?.role || "",
        character_age: characters[0]?.age ?? null,
        rate: "+0%",
        voice: VOICE_OPTIONS[0].value,
      },
    ]);
  };

  const removeLine = (idx: number) => {
    setDialogues((prev) => prev.filter((_, i) => i !== idx));
  };

  const moveLine = (idx: number, dir: -1 | 1) => {
    setDialogues((prev) => {
      const target = idx + dir;
      if (target < 0 || target >= prev.length) return prev;
      const next = [...prev];
      const tmp = next[idx];
      next[idx] = next[target];
      next[target] = tmp;
      return next;
    });
  };

  const handleGenerate = async () => {
    if (!selectedScene) return;
    const valid = dialogues.filter((d) => d.text.trim().length > 0);
    if (valid.length === 0) {
      setError("该场景没有台词，无法生成配音");
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const resp = await generateVoice({
        scene_id: selectedScene.scene_id,
        dialogues: valid,
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

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" style={modalScrollStyle} onClick={(e) => e.stopPropagation()}>
        <div className="modal-title">生成配音（edge-tts）</div>
        {scenes.length === 0 ? (
          <div style={{ color: "var(--text-secondary)", fontSize: "13px" }}>请先生成剧本。</div>
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
                    场景 {s.scene_id} — {s.shot_type}
                  </option>
                ))}
              </select>
            </div>
            {selectedScene && (
              <div style={{ fontSize: "12px", color: "var(--text-secondary)", marginBottom: "8px" }}>
                {selectedScene.description}
                {selectedScene.dialogue && (
                  <div style={{ marginTop: "4px", fontStyle: "italic" }}>
                    「{selectedScene.dialogue}」
                  </div>
                )}
              </div>
            )}

            <div style={sectionTitleStyle}>
              对白列表（{dialogues.length} 条，可编辑/增删/排序）
            </div>
            <div style={{ maxHeight: "320px", overflowY: "auto", marginBottom: "8px" }}>
              {dialogues.map((d, idx) => (
                <div key={idx} style={itemBoxStyle}>
                  <div
                    style={{
                      display: "flex",
                      gap: "6px",
                      marginBottom: "6px",
                      flexWrap: "wrap",
                      alignItems: "center",
                    }}
                  >
                    <select
                      className="modal-input"
                      style={{ ...compactInputStyle, flex: "1 1 100px" }}
                      value={d.character_name}
                      onChange={(e) => onCharacterChange(idx, e.target.value)}
                    >
                      {characters.map((c) => (
                        <option key={c.character_id} value={c.name}>
                          {c.name}
                        </option>
                      ))}
                      {!characters.some((c) => c.name === d.character_name) && (
                        <option value={d.character_name}>{d.character_name}</option>
                      )}
                    </select>
                    <input
                      className="modal-input"
                      style={{ ...compactInputStyle, flex: "1 1 80px" }}
                      value={d.character_role}
                      onChange={(e) => updateLine(idx, { character_role: e.target.value })}
                      placeholder="定位"
                    />
                    <select
                      className="modal-input"
                      style={{ ...compactInputStyle, width: "80px" }}
                      value={d.rate}
                      onChange={(e) => updateLine(idx, { rate: e.target.value })}
                    >
                      {RATE_OPTIONS.map((r) => (
                        <option key={r} value={r}>
                          {r}
                        </option>
                      ))}
                    </select>
                    <select
                      className="modal-input"
                      style={{ ...compactInputStyle, flex: "1 1 120px" }}
                      value={d.voice}
                      onChange={(e) => updateLine(idx, { voice: e.target.value })}
                    >
                      {VOICE_OPTIONS.map((v) => (
                        <option key={v.value} value={v.value}>
                          {v.label}
                        </option>
                      ))}
                    </select>
                    <button
                      style={smallBtnStyle}
                      onClick={() => removeLine(idx)}
                      title="删除该条"
                    >
                      ×
                    </button>
                  </div>
                  <textarea
                    style={textareaStyle}
                    value={d.text}
                    onChange={(e) => updateLine(idx, { text: e.target.value })}
                    placeholder="对白文本"
                  />
                  <div style={{ display: "flex", gap: "6px", marginTop: "4px" }}>
                    <button
                      style={smallBtnStyle}
                      onClick={() => moveLine(idx, -1)}
                      disabled={idx === 0}
                      title="上移"
                    >
                      ↑
                    </button>
                    <button
                      style={smallBtnStyle}
                      onClick={() => moveLine(idx, 1)}
                      disabled={idx === dialogues.length - 1}
                      title="下移"
                    >
                      ↓
                    </button>
                  </div>
                </div>
              ))}
            </div>
            <button
              className="topbar-btn"
              onClick={addLine}
              style={{ marginBottom: "8px" }}
            >
              + 添加对白
            </button>
          </>
        )}
        {error && (
          <div style={{ color: "var(--error)", fontSize: "12px", marginTop: "8px" }}>{error}</div>
        )}
        <div className="modal-actions">
          <button className="topbar-btn" onClick={onClose}>
            取消
          </button>
          <button
            className="topbar-btn topbar-btn-primary"
            onClick={handleGenerate}
            disabled={loading || !selectedScene}
          >
            {loading ? <span className="loading"></span> : "生成配音"}
          </button>
        </div>
      </div>
    </div>
  );
}
