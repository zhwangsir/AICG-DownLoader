import { useState, useMemo, useEffect, useRef } from "react";
import Canvas from "./components/Canvas";
import LeftRail from "./components/nav/LeftRail";
import AssetLibraryPanel from "./components/panels/AssetLibraryPanel";
import AgentBar from "./components/agent/AgentBar";
import {
  ScriptModal,
  CharacterModal,
  StoryboardModal,
  VideoModal,
  VoiceModal,
  SubtitleModal,
  EditModal,
  QualityModal,
  VisualQualityModal,
  PipelineModal,
  NsfwGateModal,
} from "./components/modals";
import { useDramaStore } from "./store/useDramaStore";
import { getNsfwStatus } from "./api/client";
import { ChevronDown, Clapperboard, Plus, Workflow, Zap, Users, Image as ImageIcon, Film, Mic, Captions, ShieldCheck, ScanEye, Scissors } from "lucide-react";

export default function App() {
  const store = useDramaStore();
  const {
    modals,
    setModal,
    scriptData,
    storyboards,
    videos,
    voices,
    subtitles,
    editData,
    qualityData,
    visualQualityData,
    characterCards,
    statusInfo,
    globalLoading,
    setScriptData,
    addStoryboard,
    addVideo,
    addVoice,
    addSubtitle,
    setEditData,
    setQualityData,
    setVisualQualityData,
    setStatusInfo,
  } = store;

  const [showAdvancedMenu, setShowAdvancedMenu] = useState(false);
  const advancedMenuRef = useRef<HTMLDivElement>(null);

  // M27：启动时同步 NSFW 状态（模型库/搜索联动）
  useEffect(() => {
    getNsfwStatus()
      .then((s) => store.setNsfwState(s.nsfw_enabled, s.has_pin))
      .catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 下拉 dismiss：点击组件外部 / Escape 关闭（LibTV 标准菜单行为）
  useEffect(() => {
    if (!showAdvancedMenu) return;
    const onPointerDown = (e: MouseEvent) => {
      if (!advancedMenuRef.current?.contains(e.target as globalThis.Node)) {
        setShowAdvancedMenu(false);
      }
    };
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") setShowAdvancedMenu(false);
    };
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [showAdvancedMenu]);

  const steps = useMemo(
    () => [
      { key: "script", label: "剧本", done: !!scriptData },
      { key: "character", label: "角色", done: characterCards.length > 0 },
      { key: "storyboard", label: "分镜", done: storyboards.length > 0 },
      { key: "video", label: "视频", done: videos.length > 0 },
      { key: "voice", label: "配音", done: voices.length > 0 },
      { key: "subtitle", label: "字幕", done: subtitles.length > 0 },
      { key: "edit", label: "合成", done: !!editData },
      { key: "quality", label: "质检", done: !!qualityData },
    ],
    [scriptData, characterCards, storyboards, videos, voices, subtitles, editData, qualityData]
  );

  const activeStepIndex = useMemo(() => {
    const idx = steps.findIndex((s) => !s.done);
    return idx === -1 ? steps.length - 1 : idx;
  }, [steps]);

  const advancedActions = [
    { key: "character", label: "生成角色", icon: Users, disabled: !scriptData || globalLoading },
    { key: "storyboard", label: "生成分镜", icon: ImageIcon, disabled: !scriptData || globalLoading },
    { key: "video", label: "生成视频", icon: Film, disabled: storyboards.length === 0 || globalLoading },
    { key: "voice", label: "生成配音", icon: Mic, disabled: !scriptData || globalLoading },
    { key: "subtitle", label: "生成字幕", icon: Captions, disabled: voices.length === 0 || globalLoading },
    { key: "quality", label: "剧本质检", icon: ShieldCheck, disabled: !scriptData || globalLoading },
    { key: "visualQuality", label: "视觉质检", icon: ScanEye, disabled: videos.length === 0 || globalLoading },
    { key: "edit", label: "合成成片", icon: Scissors, disabled: videos.length === 0 || voices.length === 0 || subtitles.length === 0 || globalLoading },
  ];

  const handleScriptGenerated = (data: NonNullable<typeof scriptData>) => {
    setScriptData(data);
    setStatusInfo(
      `剧本已生成: ${data.title} | ${data.characters.length} 角色 | ${data.scenes.length} 分镜`
    );
    setModal("script", false);
  };

  const handleCharacterGenerated = (name: string) => {
    setStatusInfo(`角色定妆照已生成: ${name}`);
    setModal("character", false);
  };

  const handleStoryboardGenerated = (data: Parameters<typeof addStoryboard>[0]) => {
    addStoryboard(data);
    setStatusInfo(`分镜关键帧已生成: 场景 ${data.scene_id}`);
  };

  const handleVideoGenerated = (data: Parameters<typeof addVideo>[0]) => {
    addVideo(data);
    setStatusInfo(`视频片段已生成: 场景 ${data.scene_id} (${data.duration_seconds}s)`);
  };

  const handleVoiceGenerated = (data: Parameters<typeof addVoice>[0]) => {
    addVoice(data);
    setStatusInfo(`配音已生成: 场景 ${data.scene_id} (${data.total_lines} 条语音)`);
  };

  const handleSubtitleGenerated = (data: Parameters<typeof addSubtitle>[0]) => {
    addSubtitle(data);
    setStatusInfo(`字幕已生成: 场景 ${data.scene_id} (${data.segments.length} 段)`);
  };

  const handleEditGenerated = (data: NonNullable<typeof editData>) => {
    setEditData(data);
    setStatusInfo(
      `成片已合成: ${data.title} | ${data.segments_count} 场景 | ${data.duration_seconds.toFixed(1)}s`
    );
    setModal("edit", false);
  };

  const handleQualityChecked = (data: NonNullable<typeof qualityData>) => {
    setQualityData(data);
    const critical = data.issues.filter((i) => i.severity === "critical").length;
    const warning = data.issues.filter((i) => i.severity === "warning").length;
    setStatusInfo(`质检完成: ${data.title} | 质量分 ${data.score} | critical ${critical} | warning ${warning}`);
    setModal("quality", false);
  };

  const handleVisualQualityChecked = (data: NonNullable<typeof visualQualityData>) => {
    setVisualQualityData(data);
    const critical = data.issues.filter((i) => i.severity === "critical").length;
    const warning = data.issues.filter((i) => i.severity === "warning").length;
    setStatusInfo(`视觉质检完成: 场景 ${data.scene_id} | 质量分 ${data.score} | critical ${critical} | warning ${warning}`);
    setModal("visualQuality", false);
  };

  return (
    <div className="app-container">
      <div className="topbar">
        <div className="topbar-brand">
          <div className="topbar-logo">
            <Clapperboard size={17} strokeWidth={2.2} />
          </div>
          <div className="topbar-title-group">
            <span className="topbar-kicker">Atelier</span>
            <span className="topbar-title">AIGCPannel</span>
          </div>
        </div>

        <div className="topbar-progress">
          {steps.map((s, i) => (
            <span key={s.key}>
              {i > 0 && <span className="topbar-step-sep" />}
              <div
                className={
                  "topbar-step" +
                  (s.done ? " done" : "") +
                  (!s.done && i === activeStepIndex ? " active" : "")
                }
              >
                <span className="topbar-step-dot" />
                <span className="topbar-step-label">{s.label}</span>
              </div>
            </span>
          ))}
        </div>

        <div className="topbar-actions">
          <button
            className="topbar-btn"
            onClick={() => setModal("pipeline", true)}
            disabled={globalLoading}
            title="一句话创意 → 全链路自动成片"
          >
            <Zap size={13} />
            一键成片
          </button>
          <div className="dropdown-wrapper" ref={advancedMenuRef}>
            <button
              className="topbar-btn"
              onClick={() => setShowAdvancedMenu(!showAdvancedMenu)}
              disabled={globalLoading}
            >
              <Workflow size={13} />
              操作流程
              <ChevronDown size={13} style={{ marginLeft: 2 }} />
            </button>
            {showAdvancedMenu && (
              <div className="dropdown-menu">
                {advancedActions.map((action) => {
                  const ActionIcon = action.icon;
                  return (
                    <button
                      key={action.key}
                      className="dropdown-item"
                      disabled={action.disabled}
                      onClick={() => {
                        setModal(action.key as any, true);
                        setShowAdvancedMenu(false);
                      }}
                    >
                      <ActionIcon size={13} className="dropdown-item-icon" />
                      {action.label}
                    </button>
                  );
                })}
              </div>
            )}
          </div>
          <button
            className="topbar-btn topbar-btn-primary"
            onClick={() => setModal("script", true)}
            disabled={globalLoading}
          >
            <Plus size={14} strokeWidth={2.5} />
            新建剧本
          </button>
        </div>
      </div>

      <div className="main-row">
        <LeftRail />
        <Canvas />
        <AssetLibraryPanel />
      </div>

      <AgentBar />

      <div className="status-bar">
        <div className="status-item">
          <span
            className={
              "status-dot" +
              (globalLoading ? " loading" : statusInfo.includes("失败") || statusInfo.includes("错误") ? " error" : "")
            }
          />
          <span>{statusInfo || "就绪"}</span>
        </div>
        <div className="status-item status-version">v0.13.0 · LibTV × MiniMax</div>
      </div>

      {modals.script && (
        <ScriptModal
          scriptData={scriptData}
          onClose={() => setModal("script", false)}
          onSuccess={handleScriptGenerated}
          onUpdate={() => {
            setStatusInfo("剧本修改已保存");
            setModal("script", false);
          }}
        />
      )}

      {modals.character && (
        <CharacterModal
          characters={scriptData?.characters || []}
          onClose={() => setModal("character", false)}
          onSuccess={handleCharacterGenerated}
        />
      )}

      {modals.storyboard && (
        <StoryboardModal
          scenes={scriptData?.scenes || []}
          characters={scriptData?.characters || []}
          onClose={() => setModal("storyboard", false)}
          onSuccess={handleStoryboardGenerated}
        />
      )}

      {modals.video && (
        <VideoModal
          storyboards={storyboards}
          scenes={scriptData?.scenes || []}
          onClose={() => setModal("video", false)}
          onSuccess={handleVideoGenerated}
        />
      )}

      {modals.voice && (
        <VoiceModal
          scenes={scriptData?.scenes || []}
          characters={scriptData?.characters || []}
          onClose={() => setModal("voice", false)}
          onSuccess={handleVoiceGenerated}
        />
      )}

      {modals.subtitle && (
        <SubtitleModal
          voices={voices}
          onClose={() => setModal("subtitle", false)}
          onSuccess={handleSubtitleGenerated}
        />
      )}

      {modals.edit && (
        <EditModal
          videos={videos}
          voices={voices}
          subtitles={subtitles}
          onClose={() => setModal("edit", false)}
          onSuccess={handleEditGenerated}
        />
      )}

      {modals.quality && (
        <QualityModal
          scriptData={scriptData}
          subtitles={subtitles}
          onClose={() => setModal("quality", false)}
          onSuccess={handleQualityChecked}
        />
      )}

      {modals.visualQuality && (
        <VisualQualityModal
          videos={videos}
          title={scriptData?.title || "未命名短剧"}
          onClose={() => setModal("visualQuality", false)}
          onSuccess={handleVisualQualityChecked}
        />
      )}

      {modals.pipeline && (
        <PipelineModal onClose={() => setModal("pipeline", false)} />
      )}

      {modals.nsfwGate && (
        <NsfwGateModal onClose={() => setModal("nsfwGate", false)} />
      )}
    </div>
  );
}
