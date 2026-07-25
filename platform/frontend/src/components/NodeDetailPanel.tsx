import { useEffect, useState } from "react";
import { useDramaStore } from "../store/useDramaStore";
import type {
  CharacterData,
  SceneData,
  SubtitleData,
} from "../api/client";

const panelStyle: React.CSSProperties = {
  position: "fixed",
  top: "50px",
  right: 0,
  width: "420px",
  maxWidth: "100%",
  height: "calc(100vh - 50px - 28px)",
  background: "rgba(18, 18, 20, 0.98)",
  borderLeft: "1px solid var(--border)",
  boxShadow: "-4px 0 24px rgba(0,0,0,0.4)",
  zIndex: 100,
  display: "flex",
  flexDirection: "column",
  padding: "16px",
  overflow: "hidden",
  fontSize: "13px",
};

const sectionStyle: React.CSSProperties = {
  marginBottom: "14px",
  padding: "10px",
  background: "rgba(255,255,255,0.03)",
  border: "1px solid var(--border)",
  borderRadius: "6px",
};

const labelStyle: React.CSSProperties = {
  fontSize: "11px",
  color: "var(--text-secondary)",
  marginBottom: "4px",
  marginTop: "8px",
};

const inputStyle: React.CSSProperties = {
  width: "100%",
  padding: "6px 8px",
  background: "var(--bg-primary)",
  border: "1px solid var(--border)",
  borderRadius: "4px",
  color: "var(--text-primary)",
  fontSize: "12px",
  fontFamily: "inherit",
  marginTop: "4px",
};

const textareaStyle: React.CSSProperties = {
  ...inputStyle,
  minHeight: "48px",
  resize: "vertical",
};

const btnStyle: React.CSSProperties = {
  padding: "8px 14px",
  fontSize: "13px",
  background: "var(--accent-dim)",
  border: "1px solid var(--border)",
  color: "var(--text-primary)",
  borderRadius: "4px",
  cursor: "pointer",
};

const btnPrimaryStyle: React.CSSProperties = {
  ...btnStyle,
  background: "var(--accent)",
  borderColor: "var(--accent)",
  color: "#000",
};

interface NodeDetailPanelProps {
  nodeId: string;
  type: string;
  onClose: () => void;
  onGenerate?: () => void;
}

export default function NodeDetailPanel({
  nodeId,
  type,
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

  // 本地编辑状态
  const [scriptForm, setScriptForm] = useState({
    title: scriptData?.title || "",
    genre: scriptData?.genre || "",
  });
  const [charForm, setCharForm] = useState<CharacterData | null>(character || null);
  const [sceneForm, setSceneForm] = useState<SceneData | null>(scene || null);
  const [subtitleForm, setSubtitleForm] = useState<SubtitleData | null>(subtitle || null);

  useEffect(() => {
    setScriptForm({
      title: scriptData?.title || "",
      genre: scriptData?.genre || "",
    });
  }, [scriptData?.title, scriptData?.genre]);

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
    <div style={panelStyle} onClick={(e) => e.stopPropagation()}>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginBottom: "12px",
        }}
      >
        <div style={{ fontSize: "15px", fontWeight: 600 }}>
          {titleMap[type] || "节点详情"}
        </div>
        <button style={{ ...btnStyle, padding: "4px 10px" }} onClick={onClose}>
          收起
        </button>
      </div>

      <div style={{ flex: 1, overflow: "auto", paddingRight: "6px" }}>
        {/* 剧本节点 */}
        {type === "script" && (
          <div style={sectionStyle}>
            <div style={{ ...labelStyle, marginTop: 0 }}>标题</div>
            <input
              style={inputStyle}
              value={scriptForm.title}
              onChange={(e) =>
                setScriptForm((prev) => ({ ...prev, title: e.target.value }))
              }
            />
            <div style={labelStyle}>题材</div>
            <input
              style={inputStyle}
              value={scriptForm.genre}
              onChange={(e) =>
                setScriptForm((prev) => ({ ...prev, genre: e.target.value }))
              }
            />
          </div>
        )}

        {/* 角色节点（除定妆照外的信息编辑，定妆照仍走 CharacterPreviewPanel） */}
        {type === "character" && charForm && (
          <div style={sectionStyle}>
            <div style={{ ...labelStyle, marginTop: 0 }}>角色名</div>
            <input
              style={inputStyle}
              value={charForm.name}
              onChange={(e) =>
                setCharForm((prev) => (prev ? { ...prev, name: e.target.value } : prev))
              }
            />
            <div style={labelStyle}>身份</div>
            <input
              style={inputStyle}
              value={charForm.role}
              onChange={(e) =>
                setCharForm((prev) => (prev ? { ...prev, role: e.target.value } : prev))
              }
            />
            <div style={labelStyle}>年龄</div>
            <input
              style={inputStyle}
              type="number"
              value={charForm.age ?? ""}
              onChange={(e) =>
                setCharForm((prev) =>
                  prev
                    ? { ...prev, age: e.target.value ? Number(e.target.value) : null }
                    : prev
                )
              }
            />
            <div style={labelStyle}>外貌描述</div>
            <textarea
              style={textareaStyle}
              value={charForm.description}
              onChange={(e) =>
                setCharForm((prev) =>
                  prev ? { ...prev, description: e.target.value } : prev
                )
              }
            />
            <div style={labelStyle}>性格</div>
            <textarea
              style={textareaStyle}
              value={charForm.personality}
              onChange={(e) =>
                setCharForm((prev) =>
                  prev ? { ...prev, personality: e.target.value } : prev
                )
              }
            />
          </div>
        )}

        {/* 分镜 / 视频 / 配音 共用场景信息 */}
        {(type === "storyboard" || type === "video" || type === "voice") && sceneForm && (
          <div style={sectionStyle}>
            <div style={{ ...labelStyle, marginTop: 0 }}>场景编号</div>
            <div style={{ fontSize: "14px", fontWeight: 500 }}>
              场景 {sceneForm.scene_id}
            </div>
            {type === "storyboard" && (
              <>
                <div style={labelStyle}>镜头类型</div>
                <select
                  style={inputStyle}
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
                <div style={labelStyle}>画面描述</div>
                <textarea
                  style={textareaStyle}
                  value={sceneForm.description}
                  onChange={(e) =>
                    setSceneForm((prev) =>
                      prev ? { ...prev, description: e.target.value } : prev
                    )
                  }
                />
                <div style={labelStyle}>角色动作</div>
                <textarea
                  style={textareaStyle}
                  value={sceneForm.character_actions}
                  onChange={(e) =>
                    setSceneForm((prev) =>
                      prev ? { ...prev, character_actions: e.target.value } : prev
                    )
                  }
                />
                <div style={labelStyle}>情绪</div>
                <input
                  style={inputStyle}
                  value={sceneForm.emotion}
                  onChange={(e) =>
                    setSceneForm((prev) =>
                      prev ? { ...prev, emotion: e.target.value } : prev
                    )
                  }
                />
                <div style={labelStyle}>运镜</div>
                <input
                  style={inputStyle}
                  value={sceneForm.camera_movement}
                  onChange={(e) =>
                    setSceneForm((prev) =>
                      prev ? { ...prev, camera_movement: e.target.value } : prev
                    )
                  }
                />
              </>
            )}

            <div style={labelStyle}>正面提示词</div>
            <textarea
              style={textareaStyle}
              value={sceneForm.prompt}
              onChange={(e) =>
                setSceneForm((prev) =>
                  prev ? { ...prev, prompt: e.target.value } : prev
                )
              }
            />
            <div style={labelStyle}>反向提示词</div>
            <textarea
              style={textareaStyle}
              value={sceneForm.negative_prompt}
              onChange={(e) =>
                setSceneForm((prev) =>
                  prev ? { ...prev, negative_prompt: e.target.value } : prev
                )
              }
            />
            <div style={labelStyle}>时长（秒）</div>
            <input
              style={inputStyle}
              type="number"
              value={sceneForm.duration_seconds}
              onChange={(e) =>
                setSceneForm((prev) =>
                  prev
                    ? { ...prev, duration_seconds: Number(e.target.value) || 0 }
                    : prev
                )
              }
            />

            {type === "voice" && (
              <>
                <div style={labelStyle}>场景台词</div>
                <textarea
                  style={{ ...textareaStyle, minHeight: "120px" }}
                  value={sceneForm.dialogue}
                  onChange={(e) =>
                    setSceneForm((prev) =>
                      prev ? { ...prev, dialogue: e.target.value } : prev
                    )
                  }
                />
              </>
            )}
          </div>
        )}

        {/* 字幕节点 */}
        {type === "subtitle" && subtitleForm && (
          <div style={sectionStyle}>
            <div style={{ ...labelStyle, marginTop: 0 }}>
              字幕段数：{subtitleForm.segments.length}
            </div>
            {subtitleForm.segments.map((seg, idx) => (
              <div key={idx} style={{ marginTop: "10px" }}>
                <div style={labelStyle}>
                  #{idx + 1} {formatTime(seg.start)} → {formatTime(seg.end)}
                </div>
                <textarea
                  style={textareaStyle}
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
              </div>
            ))}
          </div>
        )}

        {/* 成片 / 质检 / 视觉质检 只读 */}
        {type === "edit" && (
          <div style={sectionStyle}>
            <div style={{ color: "var(--text-secondary)" }}>
              成片节点仅展示最终合成结果。如需修改，请调整上游场景后重新合成。
            </div>
          </div>
        )}
        {type === "quality" && (
          <div style={sectionStyle}>
            <div style={{ color: "var(--text-secondary)" }}>
              质检节点展示剧本与字幕的自动检查结果，不支持直接编辑。
            </div>
          </div>
        )}
        {type === "visual_quality" && (
          <div style={sectionStyle}>
            <div style={{ color: "var(--text-secondary)" }}>
              视觉质检节点展示视频画面的自动检查结果，不支持直接编辑。
            </div>
          </div>
        )}
      </div>

      <div style={{ marginTop: "12px", display: "flex", gap: "10px" }}>
        <button style={btnPrimaryStyle} onClick={handleSave}>
          保存修改
        </button>
        {onGenerate && (
          <button
            style={btnStyle}
            onClick={() => {
              handleSave();
              onGenerate();
            }}
            disabled={globalLoading}
          >
            {globalLoading ? "生成中..." : "保存并重新生成"}
          </button>
        )}
        <button style={btnStyle} onClick={onClose}>
          取消
        </button>
      </div>
    </div>
  );
}

function formatTime(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  const ms = Math.floor((seconds % 1) * 1000);
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}.${String(ms).padStart(3, "0")}`;
}
