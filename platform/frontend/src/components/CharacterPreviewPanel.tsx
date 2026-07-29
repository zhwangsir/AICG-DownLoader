import { useEffect, useRef, useState } from "react";
import { previewCharacter, generateCharacter, type CharacterData, type CharacterPreviewResult, type CharacterCardData } from "../api/client";
import { useDramaStore, type CharacterPreviewData, type CharacterPreviewStage } from "../store/useDramaStore";

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

  const [localCharacter, setLocalCharacter] = useState<CharacterData | null>(character || null);

  useEffect(() => {
    if (character) {
      setLocalCharacter(character);
    }
  }, [character]);

  const abortControllerRef = useRef<AbortController | null>(null);
  const hasStartedSearchRef = useRef(false);

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

  const isSearchStale = (charId: string) => {
    const current = useDramaStore.getState().characterPreviews[charId];
    return !current || current.stage !== "searching";
  };

  const startSearch = async (char: CharacterData) => {
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
      <div className="side-panel">
        <div className="panel-meta-text">未找到角色信息</div>
        <button className="panel-btn" style={{ marginTop: 12 }} onClick={onClose}>
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
    <div className="side-panel">
      <div className="side-panel-header">
        <div className="side-panel-title">角色定妆照生成预览</div>
        <button
          className="panel-btn side-panel-close"
          onClick={onClose}
          disabled={currentPreview.stage === "generating"}
        >
          收起
        </button>
      </div>

      <div className="side-panel-content">
        <div className="panel-section" style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <span className={`panel-status-badge ${currentPreview.stage}`}>
            {STATUS_TEXT[currentPreview.stage]}
          </span>
          {currentPreview.error && (
            <span className="panel-error">{currentPreview.error}</span>
          )}
        </div>

        <div className="panel-section">
          <div className="panel-label">所属剧本</div>
          <div style={{ fontSize: 14, fontWeight: 500, color: "var(--text-primary)" }}>
            {scriptData?.title || "未命名短剧"}
          </div>
          <div className="panel-meta-row">
            题材：{scriptData?.genre || "-"} | 角色数：{scriptData?.characters.length || 0}
          </div>
        </div>

        <div className="panel-section">
          <div className="panel-label-bold">角色信息（可编辑）</div>
          <label className="panel-label">角色名</label>
          <input
            className="panel-input"
            value={localCharacter.name}
            onChange={(e) => setLocalCharacter({ ...localCharacter, name: e.target.value })}
          />
          <label className="panel-label">身份</label>
          <input
            className="panel-input"
            value={localCharacter.role}
            onChange={(e) => setLocalCharacter({ ...localCharacter, role: e.target.value })}
          />
          <label className="panel-label">年龄</label>
          <input
            className="panel-input"
            type="number"
            value={localCharacter.age ?? ""}
            onChange={(e) =>
              setLocalCharacter({
                ...localCharacter,
                age: e.target.value ? Number(e.target.value) : null,
              })
            }
          />
          <label className="panel-label">外貌描述</label>
          <textarea
            className="panel-textarea"
            value={localCharacter.description}
            onChange={(e) =>
              setLocalCharacter({ ...localCharacter, description: e.target.value })
            }
          />
          <label className="panel-label">性格</label>
          <textarea
            className="panel-textarea"
            value={localCharacter.personality}
            onChange={(e) =>
              setLocalCharacter({ ...localCharacter, personality: e.target.value })
            }
          />
        </div>

        <div className="panel-section">
          <div className="panel-label-bold">AI 联网调研参考</div>
          {currentPreview.stage === "searching" ? (
            <div className="panel-meta-text">
              <span className="node-spinner" style={{ width: 12, height: 12, marginRight: 6, display: "inline-block", verticalAlign: "middle" }}></span>
              正在联网搜索相关参考资料...
            </div>
          ) : currentPreview.searchReference ? (
            <div
              className="panel-meta-text"
              style={{ maxHeight: 140, overflow: "auto", whiteSpace: "pre-wrap" }}
            >
              {currentPreview.searchReference}
            </div>
          ) : (
            <div className="panel-meta-text">
              暂无搜索资料（可能网络受限，可直接编辑提示词生成）
            </div>
          )}
        </div>

        <div className="panel-section">
          <div className="panel-label-bold">生成提示词（可编辑）</div>
          <label className="panel-label">正面提示词（三视图共用）</label>
          <textarea
            className="panel-textarea"
            value={currentPreview.editedPrompts.front_view_prompt}
            onChange={(e) =>
              updateCharacterPreviewPrompt(character.character_id, "front_view_prompt", e.target.value)
            }
            disabled={currentPreview.stage === "searching"}
          />
          <label className="panel-label">反向提示词</label>
          <textarea
            className="panel-textarea"
            value={currentPreview.editedPrompts.negative_prompt}
            onChange={(e) =>
              updateCharacterPreviewPrompt(character.character_id, "negative_prompt", e.target.value)
            }
            disabled={currentPreview.stage === "searching"}
          />
        </div>
      </div>

      <div className="side-panel-footer">
        <button
          className="panel-btn panel-btn-primary"
          disabled={!canGenerate || globalLoading}
          onClick={handleConfirmGenerate}
        >
          {currentPreview.stage === "completed" ? "重新生成" : "确认生成定妆照"}
        </button>
        <button className="panel-btn" onClick={onClose} disabled={currentPreview.stage === "generating"}>
          取消
        </button>
      </div>
    </div>
  );
}
