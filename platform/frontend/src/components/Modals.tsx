import { useEffect, useId, useState } from "react";
import type { CSSProperties } from "react";
import {
  generateScript,
  generateCharacter,
  generateStoryboard,
  generateVideoAsync,
  generateVoice,
  generateSubtitle,
  composeVideo,
  checkQuality,
  checkVisualQuality,
  applySubtitleFix,
  generateLipSync,
  generatePostprocess,
  ScriptData,
  CharacterData,
  SceneData,
  StoryboardData,
  VideoData,
  VoiceData,
  SubtitleData,
  EditData,
  QualityCheckData,
  QualityVisualData,
  SubtitleFixResult,
  LipSyncData,
  PostprocessData,
  PostprocessStep,
  CharacterCardData,
} from "../api/client";
import { useProgress } from "../hooks/useProgress";
import { ProgressBar } from "./ProgressBar";
import { useDramaStore } from "../store/useDramaStore";
import { Check, Wand2, Smile, Layers } from "./ui/Icon";

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
const STYLE_OPTIONS = ["写实电影感", "日系动漫", "国风水墨", "赛博朋克", "油画质感", "水彩插画", "黑白银盐", "复古胶片", "暗黑哥特", "极简主义"];
const SHOT_TYPE_OPTIONS = ["特写", "近景", "中景", "全景", "远景", "鸟瞰", "仰拍", "俯拍", "过肩镜头", "手持跟拍"];
const CAMERA_MOVEMENT_OPTIONS = ["固定", "推镜头", "拉镜头", "摇镜头", "移镜头", "跟拍", "升降", "手持晃动"];
const EMOTION_OPTIONS = ["平静", "紧张", "温馨", "悲伤", "愤怒", "恐惧", "惊喜", "暧昧", "绝望", "希望"];
const TRANSITION_OPTIONS = [
  { value: "none", label: "无" },
  { value: "fade", label: "淡入淡出" },
  { value: "slide", label: "滑动" },
  { value: "zoom", label: "缩放" },
  { value: "wipe", label: "擦除" },
];
const RESOLUTION_OPTIONS = ["480x832", "720x1280", "1080x1920", "1280x720", "1920x1080"];
const VOICE_OPTIONS = [
  { value: "zh-CN-XiaoxiaoNeural", label: "晓晓（女·温柔）" },
  { value: "zh-CN-YunxiNeural", label: "云希（男·沉稳）" },
  { value: "zh-CN-XiaoyiNeural", label: "晓伊（女·活泼）" },
  { value: "zh-CN-YunjianNeural", label: "云健（男·浑厚）" },
  { value: "zh-CN-XiaohanNeural", label: "晓涵（女·成熟）" },
  { value: "zh-CN-YunyangNeural", label: "云扬（男·标准）" },
];
const RATE_OPTIONS = ["+0%", "+10%", "-10%", "+20%", "-20%", "+30%", "-30%"];

const modalScrollStyle: CSSProperties = { maxHeight: "80vh", overflowY: "auto" };
const sectionStyle: CSSProperties = {
  border: "1px solid var(--border)",
  borderRadius: "4px",
  padding: "10px",
  marginBottom: "10px",
  background: "var(--bg-tertiary)",
};
const itemBoxStyle: CSSProperties = {
  border: "1px solid var(--border)",
  borderRadius: "4px",
  padding: "8px",
  marginBottom: "8px",
  background: "var(--bg-primary)",
};
const textareaStyle: CSSProperties = {
  width: "100%",
  minHeight: "56px",
  padding: "8px 10px",
  background: "var(--bg-primary)",
  border: "1px solid var(--border)",
  borderRadius: "4px",
  color: "var(--text-primary)",
  fontSize: "13px",
  fontFamily: "inherit",
  resize: "vertical",
};
const compactInputStyle: CSSProperties = {
  padding: "4px 8px",
  fontSize: "12px",
};
const smallBtnStyle: CSSProperties = {
  padding: "2px 8px",
  fontSize: "12px",
  background: "transparent",
  border: "1px solid var(--border)",
  color: "var(--text-primary)",
  borderRadius: "4px",
  cursor: "pointer",
};
const sectionTitleStyle: CSSProperties = {
  fontSize: "12px",
  color: "var(--accent)",
  marginBottom: "6px",
  fontWeight: 500,
};

/** 下拉预设 + 自定义输入组合控件 */
function ComboInput({
  value,
  onChange,
  options,
  placeholder,
}: {
  value: string;
  onChange: (v: string) => void;
  options: string[];
  placeholder?: string;
}) {
  const id = useId();
  return (
    <>
      <input
        className="modal-input"
        list={id}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
      />
      <datalist id={id}>
        {options.map((o) => (
          <option key={o} value={o} />
        ))}
      </datalist>
    </>
  );
}

/** 智能下拉：若当前值不在预设列表中，则自动追加为选项 */
function SmartSelect({
  value,
  onChange,
  options,
}: {
  value: string;
  onChange: (v: string) => void;
  options: string[];
}) {
  const opts = value && !options.includes(value) ? [value, ...options] : options;
  return (
    <select className="modal-input" value={value} onChange={(e) => onChange(e.target.value)}>
      {opts.map((o) => (
        <option key={o} value={o}>
          {o}
        </option>
      ))}
    </select>
  );
}

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
  const [mode, setMode] = useState<"new" | "edit">(scriptData ? "edit" : "new");

  // 新建模式
  const [premise, setPremise] = useState(
    scriptData ? `${scriptData.genre}，${scriptData.title}` : "都市悬疑，外卖员发现客户是凶手"
  );
  const [genre, setGenre] = useState(scriptData?.genre || "都市悬疑");
  const [episodes, setEpisodes] = useState(1);
  const [scenesPerEpisode, setScenesPerEpisode] = useState(5);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

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
            <div style={{ fontSize: "12px", color: "var(--text-secondary)", marginBottom: "10px" }}>
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
            <div style={{ maxHeight: "220px", overflowY: "auto", marginBottom: "10px" }}>
              {editCharacters.map((c, idx) => (
                <div key={c.character_id} style={itemBoxStyle}>
                  <div style={{ display: "flex", gap: "8px", marginBottom: "6px" }}>
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
              <div style={{ color: "#a55", fontSize: "12px", marginTop: "8px" }}>{error}</div>
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
                style={{ marginTop: "4px" }}
                onClick={() => setMode("edit")}
              >
                返回编辑模式
              </button>
            )}
            {error && (
              <div style={{ color: "#a55", fontSize: "12px", marginTop: "8px" }}>{error}</div>
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
                {loading ? <span className="loading"></span> : "生成"}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

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
          <div style={{ color: "#a55", fontSize: "12px", marginTop: "8px" }}>{error}</div>
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
          <div style={{ color: "#a55", fontSize: "12px", marginTop: "8px" }}>{error}</div>
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

export function VideoModal({
  storyboards,
  scenes,
  onClose,
  onSuccess,
}: {
  storyboards: StoryboardData[];
  scenes: SceneData[];
  onClose: () => void;
  onSuccess: (data: VideoData) => void;
}) {
  const [selectedSceneId, setSelectedSceneId] = useState<number | null>(
    storyboards[0]?.scene_id || null
  );
  const [durationSeconds, setDurationSeconds] = useState(5);
  const [prompt, setPrompt] = useState(scenes[0]?.prompt || "");
  const [negativePrompt, setNegativePrompt] = useState(
    scenes[0]?.negative_prompt || "blurry, low quality, deformed, ugly, watermark, static"
  );
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [streamUrl, setStreamUrl] = useState<string | null>(null);
  const progress = useProgress(streamUrl);

  const selectedStoryboard = storyboards.find((s) => s.scene_id === selectedSceneId) || null;
  const selectedScene = scenes.find((s) => s.scene_id === selectedSceneId) || null;

  useEffect(() => {
    if (selectedScene) {
      setPrompt(
        selectedScene.prompt || selectedStoryboard?.prompt_used || ""
      );
      setNegativePrompt(
        selectedScene.negative_prompt ||
          "blurry, low quality, deformed, ugly, watermark, static"
      );
    }
  }, [selectedSceneId]);

  const handleGenerate = async () => {
    if (!selectedStoryboard) return;
    setLoading(true);
    setError(null);
    setStreamUrl(null);
    progress.reset();
    try {
      const task = await generateVideoAsync({
        scene_id: selectedStoryboard.scene_id,
        image_url: selectedStoryboard.image_url,
        prompt: prompt || selectedStoryboard.prompt_used,
        negative_prompt:
          negativePrompt || "blurry, low quality, deformed, ugly, watermark, static",
        duration_seconds: durationSeconds,
      });
      setStreamUrl(task.stream_url);
    } catch (e) {
      setError(String(e));
      setLoading(false);
    }
  };

  if (
    progress.status === "completed" &&
    progress.result &&
    typeof progress.result === "object" &&
    "video_url" in progress.result
  ) {
    const data = progress.result as VideoData;
    setTimeout(() => {
      onSuccess(data);
      setStreamUrl(null);
      progress.reset();
      setLoading(false);
    }, 300);
  }

  if (progress.status === "failed" && loading) {
    setTimeout(() => {
      setError(progress.error || "视频生成失败");
      setStreamUrl(null);
      progress.reset();
      setLoading(false);
    }, 300);
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" style={modalScrollStyle} onClick={(e) => e.stopPropagation()}>
        <div className="modal-title">生成视频片段（Wan 2.2 I2V）</div>
        {storyboards.length === 0 ? (
          <div style={{ color: "var(--text-secondary)", fontSize: "13px" }}>
            请先生成分镜关键帧。
          </div>
        ) : (
          <>
            <div className="modal-field">
              <label className="modal-label">选择分镜</label>
              <select
                className="modal-input"
                value={selectedSceneId ?? ""}
                onChange={(e) => setSelectedSceneId(Number(e.target.value))}
              >
                {storyboards.map((s) => (
                  <option key={s.scene_id} value={s.scene_id}>
                    场景 {s.scene_id}
                  </option>
                ))}
              </select>
            </div>
            <div className="modal-field">
              <label className="modal-label">视频时长（秒，3-15）</label>
              <input
                className="modal-input"
                type="number"
                min={3}
                max={15}
                value={durationSeconds}
                onChange={(e) => setDurationSeconds(Number(e.target.value))}
              />
            </div>
            <div className="modal-field">
              <label className="modal-label">提示词（可编辑）</label>
              <textarea
                style={textareaStyle}
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
              />
            </div>
            <div className="modal-field">
              <label className="modal-label">反向提示词（可编辑）</label>
              <textarea
                style={textareaStyle}
                value={negativePrompt}
                onChange={(e) => setNegativePrompt(e.target.value)}
              />
            </div>
            {selectedStoryboard && (
              <div style={{ marginTop: "8px" }}>
                <img
                  src={selectedStoryboard.image_url}
                  alt={`scene-${selectedStoryboard.scene_id}`}
                  style={{
                    width: "100%",
                    maxHeight: "200px",
                    objectFit: "contain",
                    borderRadius: "6px",
                    background: "var(--bg-elevated)",
                  }}
                />
              </div>
            )}
            <ProgressBar {...progress} />
          </>
        )}
        {error && (
          <div style={{ color: "#a55", fontSize: "12px", marginTop: "8px" }}>{error}</div>
        )}
        <div className="modal-actions">
          <button className="topbar-btn" onClick={onClose}>
            取消
          </button>
          <button
            className="topbar-btn topbar-btn-primary"
            onClick={handleGenerate}
            disabled={loading || !selectedStoryboard}
          >
            {loading ? <span className="loading"></span> : "生成视频"}
          </button>
        </div>
      </div>
    </div>
  );
}

interface DialogueLine {
  text: string;
  character_name: string;
  character_role: string;
  character_age: number | null;
  rate: string;
  voice: string;
}

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
          <div style={{ color: "#a55", fontSize: "12px", marginTop: "8px" }}>{error}</div>
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

export function SubtitleModal({
  voices,
  onClose,
  onSuccess,
}: {
  voices: VoiceData[];
  onClose: () => void;
  onSuccess: (data: SubtitleData) => void;
}) {
  const subtitles = useDramaStore((s) => s.subtitles);
  const updateSubtitleSegment = useDramaStore((s) => s.updateSubtitleSegment);
  const [selectedSceneId, setSelectedSceneId] = useState<number | null>(voices[0]?.scene_id || null);
  const [language, setLanguage] = useState("zh");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const selectedVoice = voices.find((v) => v.scene_id === selectedSceneId) || null;
  const firstAudioUrl = selectedVoice?.audio_urls[0]?.audio_url || null;
  const selectedSubtitle = subtitles.find((s) => s.scene_id === selectedSceneId) || null;

  const handleGenerate = async () => {
    if (!firstAudioUrl || !selectedSceneId) return;
    setLoading(true);
    setError(null);
    try {
      const resp = await generateSubtitle({
        scene_id: selectedSceneId,
        audio_url: firstAudioUrl,
        language,
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
        <div className="modal-title">生成字幕（faster-whisper ASR）</div>
        {voices.length === 0 ? (
          <div style={{ color: "var(--text-secondary)", fontSize: "13px" }}>请先生成配音。</div>
        ) : (
          <>
            <div className="modal-field">
              <label className="modal-label">选择场景（取首条配音音频）</label>
              <select
                className="modal-input"
                value={selectedSceneId ?? ""}
                onChange={(e) => setSelectedSceneId(Number(e.target.value))}
              >
                {voices.map((v) => (
                  <option key={v.scene_id} value={v.scene_id}>
                    场景 {v.scene_id}（{v.total_lines} 条语音）
                  </option>
                ))}
              </select>
            </div>
            <div className="modal-field">
              <label className="modal-label">语言</label>
              <select
                className="modal-input"
                value={language}
                onChange={(e) => setLanguage(e.target.value)}
              >
                <option value="zh">中文</option>
                <option value="en">英文</option>
                <option value="auto">自动检测</option>
              </select>
            </div>
            {selectedVoice && (
              <div style={{ fontSize: "12px", color: "var(--text-secondary)", marginTop: "8px" }}>
                <div>配音音频: {selectedVoice.audio_urls.length} 条</div>
                {firstAudioUrl && (
                  <audio controls src={firstAudioUrl} style={{ width: "100%", marginTop: "6px" }} />
                )}
              </div>
            )}

            {selectedSubtitle && (
              <div style={{ marginTop: "12px" }}>
                <div style={sectionTitleStyle}>
                  字幕编辑（{selectedSubtitle.segments.length} 段，文本可编辑）
                </div>
                <div style={{ maxHeight: "260px", overflowY: "auto" }}>
                  {selectedSubtitle.segments.map((seg, i) => (
                    <div key={i} style={itemBoxStyle}>
                      <div
                        style={{
                          fontSize: "11px",
                          color: "var(--text-secondary)",
                          marginBottom: "4px",
                        }}
                      >
                        {seg.start.toFixed(1)}s → {seg.end.toFixed(1)}s
                      </div>
                      <textarea
                        style={textareaStyle}
                        value={seg.text}
                        onChange={(e) =>
                          updateSubtitleSegment(selectedSubtitle.scene_id, i, e.target.value)
                        }
                      />
                    </div>
                  ))}
                </div>
              </div>
            )}
          </>
        )}
        {error && (
          <div style={{ color: "#a55", fontSize: "12px", marginTop: "8px" }}>{error}</div>
        )}
        <div className="modal-actions">
          <button className="topbar-btn" onClick={onClose}>
            取消
          </button>
          <button
            className="topbar-btn topbar-btn-primary"
            onClick={handleGenerate}
            disabled={loading || !firstAudioUrl}
          >
            {loading ? <span className="loading"></span> : "生成字幕"}
          </button>
        </div>
      </div>
    </div>
  );
}

export function EditModal({
  videos,
  voices,
  subtitles,
  onClose,
  onSuccess,
}: {
  videos: VideoData[];
  voices: VoiceData[];
  subtitles: SubtitleData[];
  onClose: () => void;
  onSuccess: (data: EditData) => void;
}) {
  const [title, setTitle] = useState("短剧成片");
  const [transition, setTransition] = useState("none");
  const [bgmUrl, setBgmUrl] = useState("");
  const [outputResolution, setOutputResolution] = useState("1080x1920");
  const [outputFps, setOutputFps] = useState(30);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const readyScenes = videos
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
    .filter(Boolean) as {
    scene_id: number;
    video_url: string;
    audio_url: string;
    subtitle_url: string;
  }[];

  const [selectedIds, setSelectedIds] = useState<number[]>(() =>
    readyScenes.map((s) => s.scene_id)
  );

  const toggle = (id: number) => {
    setSelectedIds((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]
    );
  };

  const handleGenerate = async () => {
    const selected = readyScenes.filter((s) => selectedIds.includes(s.scene_id));
    if (selected.length === 0) {
      setError("请至少勾选一个场景参与合成");
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const resp = await composeVideo({
        project_id: `project-${Date.now()}`,
        title,
        segments: selected.map((s) => ({
          ...s,
          duration_seconds: 5,
        })),
        transition,
        bgm_url: bgmUrl || null,
        output_resolution: outputResolution,
        output_fps: outputFps,
      });
      if (resp.success && resp.data) {
        onSuccess(resp.data);
      } else {
        setError(resp.error || "合成失败");
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
        <div className="modal-title">剪辑合成成片</div>
        {readyScenes.length === 0 ? (
          <div style={{ color: "var(--text-secondary)", fontSize: "13px" }}>
            请先生成至少一个场景的完整素材（视频、配音、字幕）。
          </div>
        ) : (
          <>
            <div className="modal-field">
              <label className="modal-label">成片标题</label>
              <input
                className="modal-input"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder="输入成片标题..."
              />
            </div>
            <div className="modal-field">
              <label className="modal-label">转场效果</label>
              <select
                className="modal-input"
                value={transition}
                onChange={(e) => setTransition(e.target.value)}
              >
                {TRANSITION_OPTIONS.map((t) => (
                  <option key={t.value} value={t.value}>
                    {t.label}
                  </option>
                ))}
              </select>
            </div>
            <div style={{ display: "flex", gap: "12px" }}>
              <div className="modal-field" style={{ flex: 1 }}>
                <label className="modal-label">输出分辨率</label>
                <select
                  className="modal-input"
                  value={outputResolution}
                  onChange={(e) => setOutputResolution(e.target.value)}
                >
                  {RESOLUTION_OPTIONS.map((r) => (
                    <option key={r} value={r}>
                      {r}
                    </option>
                  ))}
                </select>
              </div>
              <div className="modal-field" style={{ flex: 1 }}>
                <label className="modal-label">输出帧率</label>
                <select
                  className="modal-input"
                  value={String(outputFps)}
                  onChange={(e) => setOutputFps(Number(e.target.value))}
                >
                  <option value="24">24</option>
                  <option value="30">30</option>
                  <option value="60">60</option>
                </select>
              </div>
            </div>
            <div className="modal-field">
              <label className="modal-label">背景音乐 URL（可选）</label>
              <input
                className="modal-input"
                value={bgmUrl}
                onChange={(e) => setBgmUrl(e.target.value)}
                placeholder="http://..."
              />
            </div>
            <div style={sectionTitleStyle}>参与合成的场景（勾选）</div>
            <div style={{ maxHeight: "200px", overflowY: "auto", marginBottom: "8px" }}>
              {readyScenes.map((s) => (
                <label
                  key={s.scene_id}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: "8px",
                    padding: "6px 8px",
                    fontSize: "13px",
                    cursor: "pointer",
                  }}
                >
                  <input
                    type="checkbox"
                    checked={selectedIds.includes(s.scene_id)}
                    onChange={() => toggle(s.scene_id)}
                  />
                  场景 {s.scene_id}
                </label>
              ))}
            </div>
            <div style={{ fontSize: "12px", color: "var(--text-secondary)" }}>
              将合成 {readyScenes.filter((s) => selectedIds.includes(s.scene_id)).length} 个场景。
            </div>
          </>
        )}
        {error && (
          <div style={{ color: "#a55", fontSize: "12px", marginTop: "8px" }}>{error}</div>
        )}
        <div className="modal-actions">
          <button className="topbar-btn" onClick={onClose}>
            取消
          </button>
          <button
            className="topbar-btn topbar-btn-primary"
            onClick={handleGenerate}
            disabled={loading || readyScenes.length === 0}
          >
            {loading ? <span className="loading"></span> : "合成成片"}
          </button>
        </div>
      </div>
    </div>
  );
}

export function QualityModal({
  scriptData,
  subtitles,
  onClose,
  onSuccess,
}: {
  scriptData: ScriptData | null;
  subtitles: SubtitleData[];
  onClose: () => void;
  onSuccess: (data: QualityCheckData) => void;
}) {
  const qualityData = useDramaStore((s) => s.qualityData);
  const addSubtitle = useDramaStore((s) => s.addSubtitle);
  const setStatusInfo = useDramaStore((s) => s.setStatusInfo);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fixing, setFixing] = useState(false);
  const [fixResult, setFixResult] = useState<SubtitleFixResult | null>(null);
  const [fixError, setFixError] = useState<string | null>(null);

  const handleCheck = async () => {
    if (!scriptData) return;
    setLoading(true);
    setError(null);
    setFixResult(null);
    setFixError(null);
    try {
      const resp = await checkQuality({
        project_id: scriptData.project_id || `project-${Date.now()}`,
        title: scriptData.title,
        characters: scriptData.characters,
        scenes: scriptData.scenes,
        subtitles,
      });
      if (resp.success && resp.data) {
        onSuccess(resp.data);
      } else {
        setError(resp.error || "质检失败");
      }
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  };

  // 字幕闭环：基于质检 issues 自动修正 ASR 错别字并回写 SRT
  const handleApplySubtitleFix = async () => {
    if (!qualityData || subtitles.length === 0) return;
    setFixing(true);
    setFixError(null);
    try {
      const resp = await applySubtitleFix({
        subtitles,
        issues: qualityData.issues,
        persist: true,
      });
      if (resp.success && resp.data) {
        // 用修正后的字幕替换 store 中对应场景的字幕
        resp.data.fixed_subtitles.forEach((sub) => addSubtitle(sub));
        setFixResult(resp.data);
        setStatusInfo(
          `字幕修正完成: ${resp.data.fixed_count} 段已修正，${resp.data.corrections.length} 个错别字已回写 SRT`
        );
      } else {
        setFixError(resp.error || "字幕修正失败");
      }
    } catch (e) {
      setFixError(String(e));
    } finally {
      setFixing(false);
    }
  };

  // 仅当存在 subtitle 类 issues 且有字幕数据时显示修正按钮
  const hasSubtitleIssues =
    qualityData &&
    qualityData.issues.some((i) => i.category === "subtitle") &&
    subtitles.length > 0;

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" style={modalScrollStyle} onClick={(e) => e.stopPropagation()}>
        <div className="modal-title">剧本质检</div>
        {!scriptData ? (
          <div style={{ color: "var(--text-secondary)", fontSize: "13px" }}>请先生成剧本。</div>
        ) : (
          <>
            <div style={{ fontSize: "12px", color: "var(--text-secondary)", marginBottom: "12px" }}>
              将对《{scriptData.title}》进行台词一致性、剧情逻辑、敏感词检查。
            </div>
            <div style={{ fontSize: "12px", color: "var(--text-secondary)" }}>
              检查场景数: {scriptData.scenes.length} · 字幕数: {subtitles.length}
            </div>

            {qualityData && (
              <div style={{ ...sectionStyle, marginTop: "12px" }}>
                <div style={sectionTitleStyle}>已有质检结果</div>
                <div style={{ fontSize: "13px", marginBottom: "4px" }}>
                  质量分: <span style={{ color: "var(--accent)" }}>{qualityData.score}</span>
                </div>
                <div
                  style={{
                    fontSize: "12px",
                    color: "var(--text-secondary)",
                    marginBottom: "6px",
                  }}
                >
                  {qualityData.summary}
                </div>
                {qualityData.issues.length > 0 && (
                  <div style={{ maxHeight: "180px", overflowY: "auto" }}>
                    {qualityData.issues.map((iss, i) => (
                      <div
                        key={i}
                        style={{
                          fontSize: "12px",
                          marginBottom: "4px",
                          paddingLeft: "6px",
                          borderLeft: "2px solid var(--border)",
                        }}
                      >
                        <span
                          style={{
                            color:
                              iss.severity === "critical"
                                ? "#a55"
                                : iss.severity === "warning"
                                ? "#aa5"
                                : "var(--text-secondary)",
                          }}
                        >
                          [{iss.severity}]
                        </span>{" "}
                        {iss.message}
                        {iss.suggestion && (
                          <div style={{ color: "var(--text-secondary)" }}>
                            建议: {iss.suggestion}
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}

            {/* 字幕闭环：质检发现字幕错别字时提供一键修正 */}
            {hasSubtitleIssues && (
              <div style={{ ...sectionStyle, marginTop: "12px" }}>
                <div style={sectionTitleStyle}>字幕错别字自动修正（P1-2 闭环）</div>
                <div style={{ fontSize: "12px", color: "var(--text-secondary)", marginBottom: "8px" }}>
                  检测到字幕类问题，可一键提取修正对、回写 SRT 文件并更新字幕数据。
                </div>
                <button
                  className="topbar-btn"
                  style={{ background: "var(--accent-dim)", borderColor: "var(--accent)" }}
                  onClick={handleApplySubtitleFix}
                  disabled={fixing}
                >
                  {fixing ? (
                    <span className="loading"></span>
                  ) : (
                    <>
                      <Wand2 size={14} strokeWidth={2} />
                      <span style={{ marginLeft: "4px" }}>一键修正字幕错别字</span>
                    </>
                  )}
                </button>
                {fixResult && (
                  <div style={{ marginTop: "8px", fontSize: "12px" }}>
                    <div style={{ color: "var(--accent)" }}>
                      <Check size={12} strokeWidth={2.5} />
                      <span style={{ marginLeft: "2px" }}>
                        已修正 {fixResult.fixed_count} 段字幕，提取 {fixResult.corrections.length} 个错别字
                      </span>
                    </div>
                    {fixResult.corrections.length > 0 && (
                      <div style={{ marginTop: "6px", display: "flex", flexWrap: "wrap", gap: "4px" }}>
                        {fixResult.corrections.map((c, i) => (
                          <span
                            key={i}
                            style={{
                              padding: "1px 6px",
                              background: "var(--bg-primary)",
                              border: "1px solid var(--border)",
                              borderRadius: "3px",
                              fontSize: "11px",
                            }}
                          >
                            {c.wrong} → {c.right}
                          </span>
                        ))}
                      </div>
                    )}
                    {fixResult.persisted_files.length > 0 && (
                      <div style={{ marginTop: "6px", color: "var(--text-secondary)" }}>
                        已回写 {fixResult.persisted_files.length} 个 SRT 文件
                      </div>
                    )}
                  </div>
                )}
                {fixError && (
                  <div style={{ color: "#a55", fontSize: "12px", marginTop: "6px" }}>{fixError}</div>
                )}
              </div>
            )}
          </>
        )}
        {error && (
          <div style={{ color: "#a55", fontSize: "12px", marginTop: "8px" }}>{error}</div>
        )}
        <div className="modal-actions">
          <button className="topbar-btn" onClick={onClose}>
            取消
          </button>
          <button
            className="topbar-btn topbar-btn-primary"
            onClick={handleCheck}
            disabled={loading || !scriptData}
          >
            {loading ? <span className="loading"></span> : "开始质检"}
          </button>
        </div>
      </div>
    </div>
  );
}

export function VisualQualityModal({
  videos,
  title,
  onClose,
  onSuccess,
}: {
  videos: VideoData[];
  title: string;
  onClose: () => void;
  onSuccess: (data: QualityVisualData) => void;
}) {
  const [selectedSceneId, setSelectedSceneId] = useState<number | null>(
    videos[0]?.scene_id || null
  );
  const [maxFrames, setMaxFrames] = useState(6);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const selectedVideo = videos.find((v) => v.scene_id === selectedSceneId) || null;

  const handleCheck = async () => {
    if (!selectedVideo) return;
    setLoading(true);
    setError(null);
    try {
      const resp = await checkVisualQuality({
        project_id: `project-${Date.now()}`,
        title: title || "未命名视频",
        scene_id: selectedVideo.scene_id,
        video_url: selectedVideo.video_url,
        max_frames: maxFrames,
      });
      if (resp.success && resp.data) {
        onSuccess(resp.data);
      } else {
        setError(resp.error || "视觉质检失败");
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
        <div className="modal-title">视觉质检</div>
        {videos.length === 0 ? (
          <div style={{ color: "var(--text-secondary)", fontSize: "13px" }}>
            请先生成视频片段。
          </div>
        ) : (
          <>
            <div className="modal-field">
              <label className="modal-label">选择视频</label>
              <select
                className="modal-input"
                value={selectedSceneId ?? ""}
                onChange={(e) => setSelectedSceneId(Number(e.target.value))}
              >
                {videos.map((v) => (
                  <option key={v.scene_id} value={v.scene_id}>
                    场景 {v.scene_id}
                  </option>
                ))}
              </select>
            </div>
            <div className="modal-field">
              <label className="modal-label">抽帧数（1-12）</label>
              <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
                <input
                  type="range"
                  min={1}
                  max={12}
                  value={maxFrames}
                  onChange={(e) => setMaxFrames(Number(e.target.value))}
                  style={{ flex: 1 }}
                />
                <input
                  className="modal-input"
                  style={{ ...compactInputStyle, width: "60px" }}
                  type="number"
                  min={1}
                  max={12}
                  value={maxFrames}
                  onChange={(e) =>
                    setMaxFrames(Math.max(1, Math.min(12, Number(e.target.value) || 1)))
                  }
                />
              </div>
            </div>
            {selectedVideo && (
              <div
                style={{
                  fontSize: "12px",
                  color: "var(--text-secondary)",
                  marginBottom: "12px",
                }}
              >
                将对场景 {selectedVideo.scene_id} 的视频进行角色一致性、画面连贯性、异常画面检查。
              </div>
            )}
          </>
        )}
        {error && (
          <div style={{ color: "#a55", fontSize: "12px", marginTop: "8px" }}>{error}</div>
        )}
        <div className="modal-actions">
          <button className="topbar-btn" onClick={onClose}>
            取消
          </button>
          <button
            className="topbar-btn topbar-btn-primary"
            onClick={handleCheck}
            disabled={loading || !selectedVideo}
          >
            {loading ? <span className="loading"></span> : "开始质检"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ============================================================================
// P4.4 唇形同步（LatentSync 1.6）
// ============================================================================

export function LipSyncModal({
  videos,
  voices,
  characterCards,
  onClose,
  onSuccess,
}: {
  videos: VideoData[];
  voices: VoiceData[];
  characterCards: CharacterCardData[];
  onClose: () => void;
  onSuccess: (data: LipSyncData) => void;
}) {
  const [selectedSceneId, setSelectedSceneId] = useState<number | null>(
    videos[0]?.scene_id || null
  );
  const [selectedCharId, setSelectedCharId] = useState<string>("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<LipSyncData | null>(null);

  const selectedVideo = videos.find((v) => v.scene_id === selectedSceneId) || null;
  const sceneVoice = voices.find((v) => v.scene_id === selectedSceneId) || null;
  const audioUrl = sceneVoice?.audio_urls[0]?.audio_url || "";
  const selectedCard = characterCards.find((c) => c.character_id === selectedCharId) || null;
  const referenceImageUrl = selectedCard
    ? Object.values(selectedCard.reference_images)[0] || null
    : null;

  const handleGenerate = async () => {
    if (!selectedVideo || !audioUrl) return;
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const resp = await generateLipSync({
        scene_id: selectedVideo.scene_id,
        video_url: selectedVideo.video_url,
        audio_url: audioUrl,
        reference_image_url: referenceImageUrl,
      });
      if (resp.success && resp.data) {
        setResult(resp.data);
        onSuccess(resp.data);
      } else {
        setError(resp.error || "唇形同步失败");
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
        <div className="modal-title">
          <Smile size={16} style={{ verticalAlign: "-2px", marginRight: "6px" }} />
          唇形同步（LatentSync 1.6）
        </div>
        {videos.length === 0 ? (
          <div style={{ color: "var(--text-secondary)", fontSize: "13px" }}>
            请先生成视频片段。
          </div>
        ) : (
          <>
            <div className="modal-field">
              <label className="modal-label">选择视频场景</label>
              <select
                className="modal-input"
                value={selectedSceneId ?? ""}
                onChange={(e) => {
                  setSelectedSceneId(Number(e.target.value));
                  setResult(null);
                }}
              >
                {videos.map((v) => (
                  <option key={v.scene_id} value={v.scene_id}>
                    场景 {v.scene_id}
                  </option>
                ))}
              </select>
            </div>

            <div className="modal-field">
              <label className="modal-label">配音音频</label>
              {audioUrl ? (
                <div style={{ fontSize: "12px", color: "var(--text-secondary)" }}>
                  已自动匹配场景 {selectedSceneId} 的配音（{sceneVoice?.total_lines} 条语音）
                  <audio src={audioUrl} controls style={{ width: "100%", marginTop: "6px" }} />
                </div>
              ) : (
                <div style={{ fontSize: "12px", color: "#a55" }}>
                  该场景暂无配音，请先生成配音。
                </div>
              )}
            </div>

            <div className="modal-field">
              <label className="modal-label">角色参考图（可选，提升面部一致性）</label>
              <select
                className="modal-input"
                value={selectedCharId}
                onChange={(e) => setSelectedCharId(e.target.value)}
              >
                <option value="">不使用参考图</option>
                {characterCards.map((c) => (
                  <option key={c.character_id} value={c.character_id}>
                    {c.name}
                  </option>
                ))}
              </select>
            </div>

            {result && (
              <div style={sectionStyle}>
                <div style={sectionTitleStyle}>
                  {result.synced ? "同步完成" : "已降级返回原视频"} · 耗时{" "}
                  {result.elapsed_seconds.toFixed(1)}s
                </div>
                <video
                  src={result.video_url}
                  controls
                  style={{
                    width: "100%",
                    maxHeight: "240px",
                    borderRadius: "6px",
                    background: "var(--bg-elevated)",
                  }}
                />
                {result.synced && result.original_video_url && (
                  <details style={{ marginTop: "8px" }}>
                    <summary
                      style={{
                        fontSize: "12px",
                        color: "var(--text-secondary)",
                        cursor: "pointer",
                      }}
                    >
                      对比原始视频
                    </summary>
                    <video
                      src={result.original_video_url}
                      controls
                      style={{
                        width: "100%",
                        maxHeight: "200px",
                        marginTop: "6px",
                        borderRadius: "6px",
                        background: "var(--bg-elevated)",
                      }}
                    />
                  </details>
                )}
              </div>
            )}
          </>
        )}
        {error && (
          <div style={{ color: "#a55", fontSize: "12px", marginTop: "8px" }}>{error}</div>
        )}
        <div className="modal-actions">
          <button className="topbar-btn" onClick={onClose}>
            关闭
          </button>
          <button
            className="topbar-btn topbar-btn-primary"
            onClick={handleGenerate}
            disabled={loading || !selectedVideo || !audioUrl}
          >
            {loading ? <span className="loading"></span> : "开始同步"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ============================================================================
// P4.4 后处理编排（超分 → 插帧 → 修复 → 降噪 → H.265 编码）
// ============================================================================

const POSTPROCESS_STEP_META: { key: PostprocessStep; label: string; needsAudio?: boolean }[] = [
  { key: "super_resolution", label: "超分（RealBasicVSR x4）" },
  { key: "frame_interpolation", label: "插帧（RIFE）" },
  { key: "inpainting", label: "修复（ProPainter）" },
  { key: "audio_denoise", label: "降噪（DeepFilterNet3）", needsAudio: true },
  { key: "final_encode", label: "H.265 编码（VideoToolbox）" },
];

const POSTPROCESS_RESOLUTIONS = ["1920x1080", "1080x1920", "2560x1440", "3840x2160"];

export function PostprocessModal({
  videos,
  voices,
  onClose,
  onSuccess,
}: {
  videos: VideoData[];
  voices: VoiceData[];
  onClose: () => void;
  onSuccess: (data: PostprocessData) => void;
}) {
  const [selectedSceneId, setSelectedSceneId] = useState<number | null>(
    videos[0]?.scene_id || null
  );
  const [enabledSteps, setEnabledSteps] = useState<Record<PostprocessStep, boolean>>({
    super_resolution: true,
    frame_interpolation: true,
    inpainting: false,
    audio_denoise: true,
    final_encode: true,
  });
  const [outputResolution, setOutputResolution] = useState<string>("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<PostprocessData | null>(null);

  const selectedVideo = videos.find((v) => v.scene_id === selectedSceneId) || null;
  const sceneVoice = voices.find((v) => v.scene_id === selectedSceneId) || null;
  const audioUrl = sceneVoice?.audio_urls[0]?.audio_url || null;

  const toggleStep = (step: PostprocessStep) => {
    setEnabledSteps((prev) => ({ ...prev, [step]: !prev[step] }));
  };

  const handleGenerate = async () => {
    if (!selectedVideo) return;
    const steps = POSTPROCESS_STEP_META.filter((m) => enabledSteps[m.key]).map((m) => m.key);
    if (steps.length === 0) {
      setError("请至少启用一个后处理步骤");
      return;
    }
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const resp = await generatePostprocess({
        scene_id: selectedVideo.scene_id,
        video_url: selectedVideo.video_url,
        audio_url: audioUrl,
        steps,
        output_resolution: outputResolution || null,
      });
      if (resp.success && resp.data) {
        setResult(resp.data);
        onSuccess(resp.data);
      } else {
        setError(resp.error || "后处理失败");
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
        <div className="modal-title">
          <Layers size={16} style={{ verticalAlign: "-2px", marginRight: "6px" }} />
          后处理编排（P4.4）
        </div>
        {videos.length === 0 ? (
          <div style={{ color: "var(--text-secondary)", fontSize: "13px" }}>
            请先生成视频片段。
          </div>
        ) : (
          <>
            <div className="modal-field">
              <label className="modal-label">选择视频场景</label>
              <select
                className="modal-input"
                value={selectedSceneId ?? ""}
                onChange={(e) => {
                  setSelectedSceneId(Number(e.target.value));
                  setResult(null);
                }}
              >
                {videos.map((v) => (
                  <option key={v.scene_id} value={v.scene_id}>
                    场景 {v.scene_id}
                  </option>
                ))}
              </select>
            </div>

            <div className="modal-field">
              <label className="modal-label">处理步骤（按顺序执行，单步失败不阻断）</label>
              <div style={sectionStyle}>
                {POSTPROCESS_STEP_META.map((m) => {
                  const disabled = m.needsAudio && !audioUrl;
                  return (
                    <label
                      key={m.key}
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: "8px",
                        padding: "4px 0",
                        fontSize: "13px",
                        color: disabled ? "var(--text-tertiary)" : "var(--text-primary)",
                        cursor: disabled ? "not-allowed" : "pointer",
                      }}
                    >
                      <input
                        type="checkbox"
                        checked={enabledSteps[m.key] && !disabled}
                        disabled={disabled}
                        onChange={() => toggleStep(m.key)}
                      />
                      {m.label}
                      {disabled && (
                        <span style={{ fontSize: "11px", color: "#a55" }}>（该场景无配音）</span>
                      )}
                    </label>
                  );
                })}
              </div>
            </div>

            <div className="modal-field">
              <label className="modal-label">最终输出分辨率（可选，默认用后端配置）</label>
              <select
                className="modal-input"
                value={outputResolution}
                onChange={(e) => setOutputResolution(e.target.value)}
              >
                <option value="">默认（后端配置）</option>
                {POSTPROCESS_RESOLUTIONS.map((r) => (
                  <option key={r} value={r}>
                    {r}
                  </option>
                ))}
              </select>
            </div>

            {result && (
              <div style={sectionStyle}>
                <div style={sectionTitleStyle}>
                  {result.success ? "全部步骤成功" : "部分步骤失败"} · 总耗时{" "}
                  {result.elapsed_seconds.toFixed(1)}s
                </div>
                {result.steps.map((s) => {
                  const meta = POSTPROCESS_STEP_META.find((m) => m.key === s.step);
                  return (
                    <div
                      key={s.step}
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: "8px",
                        padding: "4px 0",
                        fontSize: "12px",
                      }}
                    >
                      {s.skipped ? (
                        <span style={{ color: "var(--text-tertiary)" }}>跳过</span>
                      ) : s.success ? (
                        <Check size={14} style={{ color: "var(--accent)" }} />
                      ) : (
                        <span style={{ color: "#a55" }}>失败</span>
                      )}
                      <span style={{ flex: 1 }}>{meta?.label || s.step}</span>
                      {!s.skipped && (
                        <span style={{ color: "var(--text-secondary)" }}>
                          {s.elapsed_seconds.toFixed(1)}s
                        </span>
                      )}
                    </div>
                  );
                })}
                <video
                  src={result.final_video_url}
                  controls
                  style={{
                    width: "100%",
                    maxHeight: "240px",
                    marginTop: "8px",
                    borderRadius: "6px",
                    background: "var(--bg-elevated)",
                  }}
                />
              </div>
            )}
          </>
        )}
        {error && (
          <div style={{ color: "#a55", fontSize: "12px", marginTop: "8px" }}>{error}</div>
        )}
        <div className="modal-actions">
          <button className="topbar-btn" onClick={onClose}>
            关闭
          </button>
          <button
            className="topbar-btn topbar-btn-primary"
            onClick={handleGenerate}
            disabled={loading || !selectedVideo}
          >
            {loading ? <span className="loading"></span> : "开始后处理"}
          </button>
        </div>
      </div>
    </div>
  );
}
