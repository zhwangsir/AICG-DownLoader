import { useEffect, useState } from "react";
import {
  generateScript,
  getPipelineTemplates,
  PipelineTemplateItem,
  ScriptData,
  CharacterData,
  SceneData,
} from "../../api/client";
import { useDramaStore } from "../../store/useDramaStore";
import { Check, Loader2 } from "../ui/Icon";
import {
  GENRE_OPTIONS,
  SHOT_TYPE_OPTIONS,
  CAMERA_MOVEMENT_OPTIONS,
  EMOTION_OPTIONS,
  modalScrollStyle,
  itemBoxStyle,
  textareaStyle,
  compactInputStyle,
  sectionTitleStyle,
  ComboInput,
  SmartSelect,
} from "./shared";

export function ScriptModal({
  scriptData,
  onClose,
  onSuccess,
  onUpdate,
}: {
  scriptData: ScriptData | null;
  onClose: () => void;
  onSuccess: (data: ScriptData) => void;
  onUpdate: () => void;
}) {
  const updateScriptField = useDramaStore((s) => s.updateScriptField);
  // AgentBar 创意草稿预填（LibTV 底部输入框 → 剧本模态），读后即清
  const draftPremise = useDramaStore((s) => s.draftPremise);
  const setDraftPremise = useDramaStore((s) => s.setDraftPremise);
  const [mode, setMode] = useState<"new" | "edit">(scriptData ? "edit" : "new");

  // 新建模式
  const [premise, setPremise] = useState(
    scriptData
      ? `${scriptData.genre}，${scriptData.title}`
      : draftPremise || "都市悬疑，外卖员发现客户是凶手"
  );
  useEffect(() => {
    if (draftPremise) setDraftPremise("");
    // 仅在挂载时执行一次（读后即清）
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const [genre, setGenre] = useState(scriptData?.genre || "都市悬疑");
  const [episodes, setEpisodes] = useState(1);
  const [scenesPerEpisode, setScenesPerEpisode] = useState(5);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // M25.3 模板起手：模板库下拉选择，选中后将模板内容预填到创意输入框（用户可修改后再生成）
  const [templates, setTemplates] = useState<PipelineTemplateItem[]>([]);
  const [templateId, setTemplateId] = useState("");
  useEffect(() => {
    let cancelled = false;
    getPipelineTemplates()
      .then((resp) => {
        if (!cancelled) setTemplates(resp.templates);
      })
      .catch(() => {
        // 模板库加载失败不阻塞剧本生成主流程，静默降级为无模板可选
        if (!cancelled) setTemplates([]);
      });
    return () => {
      cancelled = true;
    };
  }, []);
  const handleTemplateSelect = (id: string) => {
    setTemplateId(id);
    const tpl = templates.find((t) => t.id === id);
    if (tpl) setPremise(`${tpl.title}：${tpl.content}`);
  };

  // 编辑模式
  const [editTitle, setEditTitle] = useState(scriptData?.title || "");
  const [editGenre, setEditGenre] = useState(scriptData?.genre || "");
  const [editCharacters, setEditCharacters] = useState<CharacterData[]>(
    scriptData ? scriptData.characters.map((c) => ({ ...c })) : []
  );
  const [editScenes, setEditScenes] = useState<SceneData[]>(
    scriptData ? scriptData.scenes.map((s) => ({ ...s })) : []
  );
  const [saved, setSaved] = useState(false);

  const handleGenerate = async () => {
    setLoading(true);
    setError(null);
    try {
      const resp = await generateScript({
        premise,
        genre,
        episodes,
        scenes_per_episode: scenesPerEpisode,
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

  const handleRegenerate = () => {
    setPremise(`${editGenre}，${editTitle}`);
    setGenre(editGenre || "都市悬疑");
    setMode("new");
  };

  const handleSave = () => {
    updateScriptField({
      title: editTitle,
      genre: editGenre,
      characters: editCharacters,
      scenes: editScenes,
    });
    setSaved(true);
    setTimeout(() => setSaved(false), 1500);
    onUpdate();
  };

  const updateChar = (idx: number, patch: Partial<CharacterData>) => {
    setEditCharacters((prev) => prev.map((c, i) => (i === idx ? { ...c, ...patch } : c)));
  };

  const updateSceneRow = (idx: number, patch: Partial<SceneData>) => {
    setEditScenes((prev) => prev.map((s, i) => (i === idx ? { ...s, ...patch } : s)));
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" style={modalScrollStyle} onClick={(e) => e.stopPropagation()}>
        <div className="modal-title">{mode === "edit" ? "编辑剧本" : "生成剧本"}</div>

        {mode === "edit" && scriptData ? (
          <>
            <div style={{ fontSize: "13px", color: "var(--text-secondary)", marginBottom: "14px", lineHeight: 1.6 }}>
              当前为编辑模式，修改后点击「保存修改」即生效（不重新生成）。也可点击「重新生成」用新创意重新生成。
            </div>

            <div className="modal-field">
              <label className="modal-label">标题</label>
              <input
                className="modal-input"
                value={editTitle}
                onChange={(e) => setEditTitle(e.target.value)}
              />
            </div>
            <div className="modal-field">
              <label className="modal-label">题材</label>
              <ComboInput value={editGenre} onChange={setEditGenre} options={GENRE_OPTIONS} />
            </div>

            <div style={sectionTitleStyle}>角色列表（{editCharacters.length}）</div>
            <div style={{ maxHeight: "220px", overflowY: "auto", marginBottom: "14px" }}>
              {editCharacters.map((c, idx) => (
                <div key={c.character_id} style={itemBoxStyle}>
                  <div style={{ display: "flex", gap: "10px", marginBottom: "8px" }}>
                    <input
                      className="modal-input"
                      style={compactInputStyle}
                      value={c.name}
                      onChange={(e) => updateChar(idx, { name: e.target.value })}
                      placeholder="姓名"
                    />
                    <input
                      className="modal-input"
                      style={compactInputStyle}
                      value={c.role}
                      onChange={(e) => updateChar(idx, { role: e.target.value })}
                      placeholder="定位"
                    />
                    <input
                      className="modal-input"
                      style={{ ...compactInputStyle, width: "70px" }}
                      type="number"
                      value={c.age ?? ""}
                      onChange={(e) =>
                        updateChar(idx, { age: e.target.value === "" ? null : Number(e.target.value) })
                      }
                      placeholder="年龄"
                    />
                  </div>
                  <textarea
                    style={textareaStyle}
                    value={c.description}
                    onChange={(e) => updateChar(idx, { description: e.target.value })}
                    placeholder="描述"
                  />
                  <textarea
                    style={{ ...textareaStyle, marginTop: "4px" }}
                    value={c.personality}
                    onChange={(e) => updateChar(idx, { personality: e.target.value })}
                    placeholder="性格"
                  />
                </div>
              ))}
            </div>

            <div style={sectionTitleStyle}>场景列表（{editScenes.length}）</div>
            <div style={{ maxHeight: "280px", overflowY: "auto", marginBottom: "10px" }}>
              {editScenes.map((s, idx) => (
                <div key={s.scene_id} style={itemBoxStyle}>
                  <div style={{ fontSize: "11px", color: "var(--text-secondary)", marginBottom: "4px" }}>
                    场景 {s.scene_id} · 第 {s.episode} 集
                  </div>
                  <textarea
                    style={textareaStyle}
                    value={s.description}
                    onChange={(e) => updateSceneRow(idx, { description: e.target.value })}
                    placeholder="场景描述"
                  />
                  <div style={{ display: "flex", gap: "8px", margin: "6px 0" }}>
                    <div style={{ flex: 1 }}>
                      <label style={{ fontSize: "11px", color: "var(--text-secondary)" }}>景别</label>
                      <SmartSelect
                        value={s.shot_type}
                        onChange={(v) => updateSceneRow(idx, { shot_type: v })}
                        options={SHOT_TYPE_OPTIONS}
                      />
                    </div>
                    <div style={{ flex: 1 }}>
                      <label style={{ fontSize: "11px", color: "var(--text-secondary)" }}>情绪</label>
                      <SmartSelect
                        value={s.emotion}
                        onChange={(v) => updateSceneRow(idx, { emotion: v })}
                        options={EMOTION_OPTIONS}
                      />
                    </div>
                    <div style={{ flex: 1 }}>
                      <label style={{ fontSize: "11px", color: "var(--text-secondary)" }}>运镜</label>
                      <SmartSelect
                        value={s.camera_movement}
                        onChange={(v) => updateSceneRow(idx, { camera_movement: v })}
                        options={CAMERA_MOVEMENT_OPTIONS}
                      />
                    </div>
                    <div style={{ width: "70px" }}>
                      <label style={{ fontSize: "11px", color: "var(--text-secondary)" }}>时长(s)</label>
                      <input
                        className="modal-input"
                        style={compactInputStyle}
                        type="number"
                        value={s.duration_seconds}
                        onChange={(e) =>
                          updateSceneRow(idx, { duration_seconds: Number(e.target.value) || 0 })
                        }
                      />
                    </div>
                  </div>
                  <textarea
                    style={textareaStyle}
                    value={s.prompt}
                    onChange={(e) => updateSceneRow(idx, { prompt: e.target.value })}
                    placeholder="正向提示词"
                  />
                  <textarea
                    style={{ ...textareaStyle, marginTop: "4px" }}
                    value={s.negative_prompt}
                    onChange={(e) => updateSceneRow(idx, { negative_prompt: e.target.value })}
                    placeholder="反向提示词"
                  />
                  <textarea
                    style={{ ...textareaStyle, marginTop: "4px" }}
                    value={s.dialogue}
                    onChange={(e) => updateSceneRow(idx, { dialogue: e.target.value })}
                    placeholder="对白"
                  />
                </div>
              ))}
            </div>

            {error && (
              <div style={{ color: "var(--error)", fontSize: "12px", marginTop: "8px" }}>{error}</div>
            )}
            <div className="modal-actions">
              <button className="topbar-btn" onClick={onClose}>
                关闭
              </button>
              <button className="topbar-btn" onClick={handleRegenerate}>
                重新生成
              </button>
              <button className="topbar-btn topbar-btn-primary" onClick={handleSave}>
                {saved ? (
                  <span style={{ display: "inline-flex", alignItems: "center", gap: "4px" }}>
                    <Check size={14} strokeWidth={2.5} /> 已保存
                  </span>
                ) : (
                  "保存修改"
                )}
              </button>
            </div>
          </>
        ) : (
          <>
            {templates.length > 0 && (
              <div className="modal-field">
                <label className="modal-label">模板起手（可选）</label>
                <select
                  className="modal-input"
                  value={templateId}
                  onChange={(e) => handleTemplateSelect(e.target.value)}
                >
                  <option value="">不使用模板，直接输入创意</option>
                  {templates.map((t) => (
                    <option key={t.id} value={t.id}>
                      {t.title}（{t.tags.join("/")}）
                    </option>
                  ))}
                </select>
              </div>
            )}
            <div className="modal-field">
              <label className="modal-label">一句话创意</label>
              <input
                className="modal-input"
                value={premise}
                onChange={(e) => setPremise(e.target.value)}
                placeholder="输入你的创意..."
              />
            </div>
            <div className="modal-field">
              <label className="modal-label">题材</label>
              <ComboInput value={genre} onChange={setGenre} options={GENRE_OPTIONS} />
            </div>
            <div style={{ display: "flex", gap: "12px" }}>
              <div className="modal-field" style={{ flex: 1 }}>
                <label className="modal-label">集数</label>
                <input
                  className="modal-input"
                  type="number"
                  min={1}
                  max={100}
                  value={episodes}
                  onChange={(e) => setEpisodes(Number(e.target.value))}
                />
              </div>
              <div className="modal-field" style={{ flex: 1 }}>
                <label className="modal-label">每集分镜数</label>
                <input
                  className="modal-input"
                  type="number"
                  min={1}
                  max={30}
                  value={scenesPerEpisode}
                  onChange={(e) => setScenesPerEpisode(Number(e.target.value))}
                />
              </div>
            </div>
            {scriptData && (
              <button
                className="topbar-btn"
                style={{ marginTop: "6px" }}
                onClick={() => setMode("edit")}
              >
                返回编辑模式
              </button>
            )}
            {error && (
              <div style={{ color: "var(--error)", fontSize: "13px", marginTop: "10px" }}>{error}</div>
            )}
            <div className="modal-actions">
              <button className="topbar-btn" onClick={onClose}>
                取消
              </button>
              <button
                className="topbar-btn topbar-btn-primary"
                onClick={handleGenerate}
                disabled={loading}
              >
                {loading ? (
                  <span style={{ display: "inline-flex", alignItems: "center", gap: "6px" }}>
                    <Loader2 size={15} style={{ animation: "node-spin 1s linear infinite" }} />
                    生成中…
                  </span>
                ) : (
                  "生成"
                )}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
