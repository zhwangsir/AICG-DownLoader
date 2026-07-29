import { useEffect, useState } from "react";
import { generateCharacter, CharacterData } from "../../api/client";
import { useDramaStore } from "../../store/useDramaStore";
import { Check } from "../ui/Icon";
import {
  STYLE_OPTIONS,
  modalScrollStyle,
  sectionStyle,
  sectionTitleStyle,
  compactInputStyle,
  textareaStyle,
  ComboInput,
} from "./shared";

export function CharacterModal({
  characters,
  onClose,
  onSuccess,
}: {
  characters: CharacterData[];
  onClose: () => void;
  onSuccess: (name: string) => void;
}) {
  const updateCharacter = useDramaStore((s) => s.updateCharacter);
  const [selectedCharId, setSelectedCharId] = useState<string>(characters[0]?.character_id || "");
  const [editChar, setEditChar] = useState<CharacterData | null>(
    characters[0] ? { ...characters[0] } : null
  );
  const [style, setStyle] = useState("写实电影感");
  const [consistencyLevel, setConsistencyLevel] = useState("L3");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    const c = characters.find((ch) => ch.character_id === selectedCharId);
    if (c) setEditChar({ ...c });
  }, [selectedCharId, characters]);

  const patch = (p: Partial<CharacterData>) =>
    setEditChar((prev) => (prev ? { ...prev, ...p } : prev));

  const handleGenerate = async () => {
    if (!editChar) return;
    setLoading(true);
    setError(null);
    try {
      const resp = await generateCharacter({
        character: editChar,
        style,
        consistency_level: consistencyLevel,
      });
      if (resp.success) {
        onSuccess(editChar.name);
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
    if (!editChar) return;
    updateCharacter(editChar.character_id, {
      name: editChar.name,
      role: editChar.role,
      age: editChar.age,
      description: editChar.description,
      personality: editChar.personality,
    });
    setSaved(true);
    setTimeout(() => setSaved(false), 1500);
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" style={modalScrollStyle} onClick={(e) => e.stopPropagation()}>
        <div className="modal-title">生成角色定妆照</div>
        {characters.length === 0 ? (
          <div style={{ color: "var(--text-secondary)", fontSize: "13px" }}>
            请先生成剧本，角色将从剧本中提取。
          </div>
        ) : (
          <>
            <div className="modal-field">
              <label className="modal-label">选择角色</label>
              <select
                className="modal-input"
                value={selectedCharId}
                onChange={(e) => setSelectedCharId(e.target.value)}
              >
                {characters.map((c) => (
                  <option key={c.character_id} value={c.character_id}>
                    {c.name} ({c.role})
                  </option>
                ))}
              </select>
            </div>
            <div className="modal-field">
              <label className="modal-label">画风</label>
              <ComboInput value={style} onChange={setStyle} options={STYLE_OPTIONS} />
            </div>
            <div className="modal-field">
              <label className="modal-label">一致性层级</label>
              <select
                className="modal-input"
                value={consistencyLevel}
                onChange={(e) => setConsistencyLevel(e.target.value)}
              >
                <option value="L1">L1 — 基础（Seed+提示词，50%）</option>
                <option value="L2">L2 — 标准（IPAdapter，82%）</option>
                <option value="L3">L3 — 进阶（LoRA微调，95%）</option>
                <option value="L4">L4 — 工业级（PuLID向量注入，&gt;95%）</option>
              </select>
            </div>

            {editChar && (
              <div style={sectionStyle}>
                <div style={sectionTitleStyle}>角色信息（可编辑）</div>
                <div style={{ display: "flex", gap: "8px", marginBottom: "6px" }}>
                  <input
                    className="modal-input"
                    style={compactInputStyle}
                    value={editChar.name}
                    onChange={(e) => patch({ name: e.target.value })}
                    placeholder="姓名"
                  />
                  <input
                    className="modal-input"
                    style={compactInputStyle}
                    value={editChar.role}
                    onChange={(e) => patch({ role: e.target.value })}
                    placeholder="定位"
                  />
                  <input
                    className="modal-input"
                    style={{ ...compactInputStyle, width: "70px" }}
                    type="number"
                    value={editChar.age ?? ""}
                    onChange={(e) =>
                      patch({ age: e.target.value === "" ? null : Number(e.target.value) })
                    }
                    placeholder="年龄"
                  />
                </div>
                <textarea
                  style={textareaStyle}
                  value={editChar.description}
                  onChange={(e) => patch({ description: e.target.value })}
                  placeholder="描述"
                />
                <textarea
                  style={{ ...textareaStyle, marginTop: "4px" }}
                  value={editChar.personality}
                  onChange={(e) => patch({ personality: e.target.value })}
                  placeholder="性格"
                />
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
          {editChar && (
            <button className="topbar-btn" onClick={handleSave}>
              {saved ? (
                <span style={{ display: "inline-flex", alignItems: "center", gap: "4px" }}>
                  <Check size={14} strokeWidth={2.5} /> 已保存
                </span>
              ) : (
                "保存角色修改"
              )}
            </button>
          )}
          <button
            className="topbar-btn topbar-btn-primary"
            onClick={handleGenerate}
            disabled={loading || !editChar}
          >
            {loading ? <span className="loading"></span> : "生成定妆照"}
          </button>
        </div>
      </div>
    </div>
  );
}
