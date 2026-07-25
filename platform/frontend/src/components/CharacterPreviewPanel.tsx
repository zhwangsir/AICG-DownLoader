import { useEffect, useRef, useState } from "react";
import { previewCharacter, generateCharacter, type CharacterData, type CharacterPreviewResult, type CharacterCardData } from "../api/client";
import { useDramaStore, type CharacterPreviewData, type CharacterPreviewStage } from "../store/useDramaStore";

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

const statusBadgeStyle = (stage: CharacterPreviewStage): React.CSSProperties => ({
  display: "inline-block",
  padding: "3px 8px",
  borderRadius: "12px",
  fontSize: "11px",
  fontWeight: 500,
  background:
    stage === "searching"
      ? "rgba(74, 165, 165, 0.15)"
      : stage === "generating"
      ? "rgba(165, 120, 74, 0.15)"
      : stage === "completed"
      ? "rgba(120, 165, 74, 0.15)"
      : "rgba(255,255,255,0.06)",
  color:
    stage === "searching"
      ? "var(--node-storyboard)"
      : stage === "generating"
      ? "var(--node-video)"
      : stage === "completed"
      ? "var(--node-edit)"
      : "var(--text-secondary)",
});

const STATUS_TEXT: Record<CharacterPreviewStage, string> = {
  idle: "等待开始",
  searching: "AI 联网调研中",
  editing: "可编辑确认",
  generating: "正在生成定妆照",
  completed: "生成完成",
};

export default function CharacterPreviewPanel({
  characterId,
  onClose,
}: {
  characterId: string;
  onClose: () => void;
}) {
  const scriptData = useDramaStore((s) => s.scriptData);
  const characterPreviews = useDramaStore((s) => s.characterPreviews);
  const globalLoading = useDramaStore((s) => s.globalLoading);
  const startGlobalLoading = useDramaStore((s) => s.startGlobalLoading);
  const stopGlobalLoading = useDramaStore((s) => s.stopGlobalLoading);
  const setCharacterPreview = useDramaStore((s) => s.setCharacterPreview);
  const updateCharacterPreview = useDramaStore((s) => s.updateCharacterPreview);
  const updateCharacterPreviewPrompt = useDramaStore((s) => s.updateCharacterPreviewPrompt);
  const updateCharacter = useDramaStore((s) => s.updateCharacter);
  const addCharacterCard = useDramaStore((s) => s.addCharacterCard);
  const setStatusInfo = useDramaStore((s) => s.setStatusInfo);

  const preview = characterPreviews[characterId];
  const character = scriptData?.characters.find((c) => c.character_id === characterId);

  // 本地编辑的角色字段
  const [localCharacter, setLocalCharacter] = useState<CharacterData | null>(character || null);

  useEffect(() => {
    if (character) {
      setLocalCharacter(character);
    }
  }, [character]);

  const abortControllerRef = useRef<AbortController | null>(null);
  // 防止 Strict Mode / store 更新导致搜索重复启动
  const hasStartedSearchRef = useRef(false);

  // 初始化：如果当前角色没有预览数据，或预览处于 idle / 上次失败状态，自动开始 AI 搜索
  useEffect(() => {
    if (!character) return;
    if (hasStartedSearchRef.current) return;
    hasStartedSearchRef.current = true;
    const current = useDramaStore.getState().characterPreviews[character.character_id];
    const shouldSearch = !current || current.stage === "idle" || (current.stage === "editing" && current.error);
    if (!shouldSearch) return;
    startSearch(character);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [character]);

  // 丢弃过期响应的辅助函数：当前预览已经不是 searching 时忽略结果
  const isSearchStale = (charId: string) => {
    const current = useDramaStore.getState().characterPreviews[charId];
    return !current || current.stage !== "searching";
  };

  const startSearch = async (char: CharacterData) => {
    // 防止 React Strict Mode 双挂载或面板重复打开时启动多次搜索，
    // 避免后启动的请求覆盖已完成的编辑状态导致界面卡在 searching。
    // 允许在上次失败（editing + error）时重新搜索。
    const existing = useDramaStore.getState().characterPreviews[char.character_id];
    const canRestart = !existing || existing.stage === "idle" || (existing.stage === "editing" && existing.error);
    if (!canRestart) {
      return;
    }

    setCharacterPreview(char.character_id, {
      character_id: char.character_id,
      character: char,
      style: "写实电影感",
      searchReference: "",
      generatedPrompts: {
        front_view_prompt: "",
        side_view_prompt: "",
        closeup_prompt: "",
        negative_prompt: "",
      },
      editedPrompts: {
        front_view_prompt: "",
        side_view_prompt: "",
        closeup_prompt: "",
        negative_prompt: "",
      },
      stage: "searching",
    });

    try {
      const resp = await previewCharacter({ character: char, style: "写实电影感" });
      if (isSearchStale(char.character_id)) return;
      if (resp.success && resp.data) {
        const data: CharacterPreviewResult = resp.data as CharacterPreviewResult;
        updateCharacterPreview(char.character_id, {
          searchReference: data.search_reference || "",
          generatedPrompts: data.prompts,
          editedPrompts: data.prompts,
          stage: "editing",
          error: resp.error || undefined,
        });
        setStatusInfo(
          resp.error
            ? `角色 "${char.name}" 预览已生成（${resp.error}）`
            : `角色 "${char.name}" AI 调研完成，请确认后生成`
        );
      } else {
        updateCharacterPreview(char.character_id, {
          stage: "editing",
          error: resp.error || "搜索失败",
        });
      }
    } catch (e) {
      if (isSearchStale(char.character_id)) return;
      updateCharacterPreview(char.character_id, {
        stage: "editing",
        error: e instanceof Error ? e.message : String(e),
      });
    }
  };

  const handleConfirmGenerate = async () => {
    if (!character || !preview || globalLoading) return;

    // 先把本地编辑的角色信息保存到 store
    updateCharacter(character.character_id, localCharacter || character);

    startGlobalLoading(`正在生成 ${character.name} 的定妆照...`);
    updateCharacterPreview(character.character_id, { stage: "generating" });
    setStatusInfo(`正在生成 ${character.name} 的定妆照...`);

    try {
      const resp = await generateCharacter({
        character: localCharacter || character,
        style: "写实电影感",
        consistency_level: "L3",
        preview_positive_prompt: preview.editedPrompts.front_view_prompt,
        preview_negative_prompt: preview.editedPrompts.negative_prompt,
      });

      if (resp.success && resp.data) {
        // 生成成功：保存角色卡到 store（持久化），Canvas 会自动读取
        updateCharacterPreview(character.character_id, { stage: "completed" });
        setStatusInfo(`${character.name} 定妆照生成完成`);
        addCharacterCard(resp.data as CharacterCardData);
      } else {
        updateCharacterPreview(character.character_id, {
          stage: "editing",
          error: resp.error || "生成失败",
        });
        setStatusInfo(resp.error || "生成失败");
      }
    } catch (e) {
      updateCharacterPreview(character.character_id, {
        stage: "editing",
        error: e instanceof Error ? e.message : String(e),
      });
      setStatusInfo(e instanceof Error ? e.message : String(e));
    } finally {
      stopGlobalLoading();
    }
  };

  if (!character || !localCharacter) {
    return (
      <div style={panelStyle}>
        <div style={{ color: "var(--text-secondary)" }}>未找到角色信息</div>
        <button style={{ ...btnStyle, marginTop: "12px" }} onClick={onClose}>
          关闭
        </button>
      </div>
    );
  }

  const currentPreview = preview || {
    stage: "idle" as CharacterPreviewStage,
    searchReference: "",
    generatedPrompts: {
      front_view_prompt: "",
      side_view_prompt: "",
      closeup_prompt: "",
      negative_prompt: "",
    },
    editedPrompts: {
      front_view_prompt: "",
      side_view_prompt: "",
      closeup_prompt: "",
      negative_prompt: "",
    },
  };

  const canGenerate =
    currentPreview.stage === "editing" ||
    currentPreview.stage === "completed" ||
    currentPreview.stage === "idle";

  return (
    <div style={panelStyle}>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginBottom: "12px",
        }}
      >
        <div style={{ fontSize: "15px", fontWeight: 600 }}>角色定妆照生成预览</div>
        <button
          style={{ ...btnStyle, padding: "4px 10px" }}
          onClick={onClose}
          disabled={currentPreview.stage === "generating"}
        >
          收起
        </button>
      </div>

      <div style={{ flex: 1, overflow: "auto", paddingRight: "6px" }}>
        {/* 状态指示 */}
        <div style={{ ...sectionStyle, display: "flex", alignItems: "center", gap: "10px" }}>
          <span style={statusBadgeStyle(currentPreview.stage)}>
            {STATUS_TEXT[currentPreview.stage]}
          </span>
          {currentPreview.error && (
            <span style={{ color: "var(--node-quality)", fontSize: "11px" }}>
              {currentPreview.error}
            </span>
          )}
        </div>

        {/* 剧本信息预览 */}
        <div style={sectionStyle}>
          <div style={labelStyle}>所属剧本</div>
          <div style={{ fontSize: "14px", fontWeight: 500 }}>
            {scriptData?.title || "未命名短剧"}
          </div>
          <div style={{ fontSize: "11px", color: "var(--text-secondary)", marginTop: "4px" }}>
            题材：{scriptData?.genre || "-"} | 角色数：{scriptData?.characters.length || 0}
          </div>
        </div>

        {/* 角色信息编辑 */}
        <div style={sectionStyle}>
          <div style={{ ...labelStyle, fontWeight: 600, color: "var(--text-primary)" }}>
            角色信息（可编辑）
          </div>
          <div style={labelStyle}>角色名</div>
          <input
            style={inputStyle}
            value={localCharacter.name}
            onChange={(e) => setLocalCharacter({ ...localCharacter, name: e.target.value })}
          />
          <div style={labelStyle}>身份</div>
          <input
            style={inputStyle}
            value={localCharacter.role}
            onChange={(e) => setLocalCharacter({ ...localCharacter, role: e.target.value })}
          />
          <div style={labelStyle}>年龄</div>
          <input
            style={inputStyle}
            type="number"
            value={localCharacter.age ?? ""}
            onChange={(e) =>
              setLocalCharacter({
                ...localCharacter,
                age: e.target.value ? Number(e.target.value) : null,
              })
            }
          />
          <div style={labelStyle}>外貌描述</div>
          <textarea
            style={textareaStyle}
            value={localCharacter.description}
            onChange={(e) =>
              setLocalCharacter({ ...localCharacter, description: e.target.value })
            }
          />
          <div style={labelStyle}>性格</div>
          <textarea
            style={textareaStyle}
            value={localCharacter.personality}
            onChange={(e) =>
              setLocalCharacter({ ...localCharacter, personality: e.target.value })
            }
          />
        </div>

        {/* AI 联网调研结果 */}
        <div style={sectionStyle}>
          <div style={{ ...labelStyle, fontWeight: 600, color: "var(--text-primary)" }}>
            AI 联网调研参考
          </div>
          {currentPreview.stage === "searching" ? (
            <div style={{ color: "var(--text-secondary)", fontSize: "12px" }}>
              <span className="loading" style={{ width: "12px", height: "12px", marginRight: "6px" }}></span>
              正在联网搜索相关参考资料...
            </div>
          ) : currentPreview.searchReference ? (
            <div
              style={{
                fontSize: "11px",
                lineHeight: "1.5",
                color: "var(--text-secondary)",
                maxHeight: "140px",
                overflow: "auto",
                whiteSpace: "pre-wrap",
              }}
            >
              {currentPreview.searchReference}
            </div>
          ) : (
            <div style={{ color: "var(--text-secondary)", fontSize: "12px" }}>
              暂无搜索资料（可能网络受限，可直接编辑提示词生成）
            </div>
          )}
        </div>

        {/* 提示词编辑 */}
        <div style={sectionStyle}>
          <div style={{ ...labelStyle, fontWeight: 600, color: "var(--text-primary)" }}>
            生成提示词（可编辑）
          </div>
          <div style={labelStyle}>正面提示词（三视图共用）</div>
          <textarea
            style={textareaStyle}
            value={currentPreview.editedPrompts.front_view_prompt}
            onChange={(e) =>
              updateCharacterPreviewPrompt(character.character_id, "front_view_prompt", e.target.value)
            }
            disabled={currentPreview.stage === "searching"}
          />
          <div style={labelStyle}>反向提示词</div>
          <textarea
            style={textareaStyle}
            value={currentPreview.editedPrompts.negative_prompt}
            onChange={(e) =>
              updateCharacterPreviewPrompt(character.character_id, "negative_prompt", e.target.value)
            }
            disabled={currentPreview.stage === "searching"}
          />
        </div>
      </div>

      {/* 底部操作按钮 */}
      <div style={{ marginTop: "12px", display: "flex", gap: "10px" }}>
        <button
          style={btnPrimaryStyle}
          disabled={!canGenerate || globalLoading}
          onClick={handleConfirmGenerate}
        >
          {currentPreview.stage === "completed" ? "重新生成" : "确认生成定妆照"}
        </button>
        <button style={btnStyle} onClick={onClose} disabled={currentPreview.stage === "generating"}>
          取消
        </button>
      </div>
    </div>
  );
}
