import { useEffect, useMemo, useState } from "react";
import {
  runPipeline,
  cancelPipeline,
  resolveTaskUrl,
  resolveStaticUrl,
  extractScriptFromReport,
  type PipelineReport,
} from "../../api/client";
import { useProgress } from "../../hooks/useProgress";
import { useDramaStore } from "../../store/useDramaStore";
import { ProgressBar } from "../ProgressBar";
import { Check, Loader2, Sparkles, Square, Workflow, Zap } from "../ui/Icon";
import {
  GENRE_OPTIONS,
  STYLE_OPTIONS,
  modalScrollStyle,
  sectionStyle,
  textareaStyle,
  compactInputStyle,
  sectionTitleStyle,
  chipStyle,
  chipActiveStyle,
  primaryBtnStyle,
  secondaryBtnStyle,
  dangerBtnStyle,
  SmartSelect,
} from "./shared";

const STEP_LABELS: Record<string, string> = {
  script: "剧本",
  character: "角色定妆照",
  storyboard: "分镜",
  video: "视频",
  voice: "配音",
  subtitle: "字幕",
  edit: "剪辑",
  quality: "质检",
  visual_quality: "视觉对照",
};

export function PipelineModal({ onClose }: { onClose: () => void }) {
  const setStatusInfo = useDramaStore((s) => s.setStatusInfo);
  const setScriptData = useDramaStore((s) => s.setScriptData);

  // 表单状态
  const [premise, setPremise] = useState("都市悬疑，外卖员发现客户是凶手");
  const [genre, setGenre] = useState("都市悬疑");
  const [style, setStyle] = useState("写实电影感");
  const [episodes, setEpisodes] = useState(1);
  const [scenesPerEpisode, setScenesPerEpisode] = useState(3);
  const [mode, setMode] = useState<"iaa" | "iap">("iaa");
  const [genCharRefs, setGenCharRefs] = useState(false);
  const [runQc, setRunQc] = useState(true);
  const [runVc, setRunVc] = useState(false);
  const [aiLabel, setAiLabel] = useState(true);
  const [licenseNumber, setLicenseNumber] = useState("");

  // 任务状态
  const [taskId, setTaskId] = useState<string | null>(null);
  const [streamUrl, setStreamUrl] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const progress = useProgress(streamUrl);
  const running =
    !!taskId && progress.status !== "completed" && progress.status !== "failed";

  const report = useMemo(() => {
    if (progress.status !== "completed" && progress.status !== "failed") {
      return null;
    }
    return (progress.result as PipelineReport | null) ?? null;
  }, [progress.status, progress.result]);

  const finalVideoUrl = useMemo(() => {
    const raw = report?.steps?.edit?.final_video_url;
    return typeof raw === "string" && raw ? resolveStaticUrl(raw) : null;
  }, [report]);

  // M9 从终态报告提取完整剧本，供「加载到画布」回填（仅任务成功时可用）
  const canvasScript = useMemo(() => {
    if (!report?.passed) return null;
    return extractScriptFromReport(report);
  }, [report]);

  // M25.1 锚点重拍：pipeline 完成（含失败）即保存 project_id，
  // 供视频模态定位 shot_params.json 快照做参数锁定重拍
  const setPipelineProjectId = useDramaStore((s) => s.setPipelineProjectId);
  useEffect(() => {
    const pid = report?.project_id;
    if (typeof pid === "string" && pid) setPipelineProjectId(pid);
  }, [report, setPipelineProjectId]);

  const handleStart = async () => {
    if (!premise.trim()) {
      setError("请输入一句话创意");
      return;
    }
    setStarting(true);
    setError(null);
    try {
      const resp = await runPipeline({
        premise: premise.trim(),
        genre,
        style,
        episodes,
        scenes_per_episode: scenesPerEpisode,
        monetization_mode: mode,
        generate_character_refs: genCharRefs,
        run_quality_check: runQc,
        run_visual_check: runVc,
        ai_label_enabled: aiLabel,
        license_number: licenseNumber.trim(),
      });
      setTaskId(resp.task_id);
      setStreamUrl(resolveTaskUrl(resp.stream_url));
      setStatusInfo(`全链路任务已启动: ${resp.task_id}`);
      // DramaClaw 任务中心：登记全局任务 + SSE 流（模态关闭后进度仍可见）
      const streamFull = resolveTaskUrl(resp.stream_url);
      useDramaStore.getState().upsertTask({
        id: resp.task_id,
        label: `一键成片：${premise.trim().slice(0, 18)}${premise.trim().length > 18 ? "…" : ""}`,
        kind: "pipeline",
        status: "running",
        percent: 0,
        message: "任务已启动",
        startedAt: Date.now(),
      });
      useDramaStore.getState().setPipelineStream(resp.task_id, streamFull);
    } catch (e) {
      setError(String(e));
    } finally {
      setStarting(false);
    }
  };

  const handleCancel = async () => {
    if (!taskId) return;
    setCancelling(true);
    try {
      await cancelPipeline(taskId);
      setStatusInfo(`已请求取消全链路任务: ${taskId}`);
    } catch (e) {
      setError(String(e));
    } finally {
      setCancelling(false);
    }
  };

  const handleReset = () => {
    progress.reset();
    setTaskId(null);
    setStreamUrl(null);
    setError(null);
  };

  // M9 将全链路生成的剧本回填画布，进入「一键生成 → 画布微调 → 局部重跑」闭环
  const handleLoadToCanvas = () => {
    if (!canvasScript) return;
    setScriptData(canvasScript);
    setStatusInfo(
      `已加载到画布: 《${canvasScript.title}》（${canvasScript.characters.length} 角色 / ${canvasScript.scenes.length} 场景）`
    );
    progress.reset();
    onClose();
  };

  const handleClose = () => {
    if (running) {
      // 关闭不取消后端任务，仅断开本地 SSE 订阅
      setStatusInfo(`全链路任务 ${taskId} 仍在后台运行`);
    }
    progress.reset();
    onClose();
  };

  return (
    <div className="modal-overlay" onClick={handleClose}>
      <div
        className="modal"
        style={modalScrollStyle}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-title">
          一键全链路成片
        </div>

        {!taskId ? (
          <>
            <div
              style={{
                fontSize: 13,
                color: "var(--text-secondary)",
                marginBottom: 14,
                lineHeight: 1.6,
              }}
            >
              从一句话创意自动执行 剧本 → 角色 → 分镜 → 视频 → 配音 → 字幕 → 剪辑 →
              质检 全流程。任务在后台执行，可随时关闭本窗口。
            </div>

            <div style={sectionStyle}>
              <div style={sectionTitleStyle}>
                <Sparkles size={13} />
                创意设定
              </div>
              <textarea
                style={textareaStyle}
                value={premise}
                onChange={(e) => setPremise(e.target.value)}
                placeholder="一句话创意，如：都市悬疑，外卖员发现客户是凶手"
              />
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "1fr 1fr",
                  gap: 10,
                  marginTop: 12,
                }}
              >
                <label style={{ fontSize: 12, color: "var(--text-secondary)" }}>
                  题材
                  <SmartSelect value={genre} onChange={setGenre} options={GENRE_OPTIONS} />
                </label>
                <label style={{ fontSize: 12, color: "var(--text-secondary)" }}>
                  画风
                  <SmartSelect value={style} onChange={setStyle} options={STYLE_OPTIONS} />
                </label>
                <label style={{ fontSize: 12, color: "var(--text-secondary)" }}>
                  集数（1-10）
                  <input
                    className="modal-input"
                    style={compactInputStyle}
                    type="number"
                    min={1}
                    max={10}
                    value={episodes}
                    onChange={(e) =>
                      setEpisodes(Math.max(1, Math.min(10, Number(e.target.value) || 1)))
                    }
                  />
                </label>
                <label style={{ fontSize: 12, color: "var(--text-secondary)" }}>
                  每集分镜数（1-10）
                  <input
                    className="modal-input"
                    style={compactInputStyle}
                    type="number"
                    min={1}
                    max={10}
                    value={scenesPerEpisode}
                    onChange={(e) =>
                      setScenesPerEpisode(
                        Math.max(1, Math.min(10, Number(e.target.value) || 1))
                      )
                    }
                  />
                </label>
              </div>

              <div style={{ marginTop: 12, fontSize: 12, color: "var(--text-secondary)" }}>
                变现模式
                <div style={{ display: "flex", gap: 8, marginTop: 6 }}>
                  <button
                    style={mode === "iaa" ? chipActiveStyle : chipStyle}
                    onClick={() => setMode("iaa")}
                  >
                    IAA 免费+广告
                  </button>
                  <button
                    style={mode === "iap" ? chipActiveStyle : chipStyle}
                    onClick={() => setMode("iap")}
                  >
                    IAP 付费解锁
                  </button>
                </div>
              </div>
            </div>

            <div style={sectionStyle}>
              <div style={sectionTitleStyle}>
                <Zap size={13} />
                流程开关
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                <label style={{ fontSize: 13, display: "flex", gap: 8, alignItems: "center" }}>
                  <input
                    type="checkbox"
                    checked={genCharRefs}
                    onChange={(e) => setGenCharRefs(e.target.checked)}
                  />
                  生成角色定妆照（耗时较长，跳过可显著提速）
                </label>
                <label style={{ fontSize: 13, display: "flex", gap: 8, alignItems: "center" }}>
                  <input
                    type="checkbox"
                    checked={runQc}
                    onChange={(e) => setRunQc(e.target.checked)}
                  />
                  成片后执行文本质检
                </label>
                <label style={{ fontSize: 13, display: "flex", gap: 8, alignItems: "center" }}>
                  <input
                    type="checkbox"
                    checked={runVc}
                    onChange={(e) => setRunVc(e.target.checked)}
                  />
                  成片后执行视觉漂移对照（需角色定妆照，检测跨镜角色漂移）
                </label>
                <label style={{ fontSize: 13, display: "flex", gap: 8, alignItems: "center" }}>
                  <input
                    type="checkbox"
                    checked={aiLabel}
                    onChange={(e) => setAiLabel(e.target.checked)}
                  />
                  烧录「AI生成」标识（合规默认开启）
                </label>
                {aiLabel && (
                  <label style={{ fontSize: 12, color: "var(--text-secondary)" }}>
                    备案号（非空时随标识烧录）
                    <input
                      className="modal-input"
                      style={compactInputStyle}
                      value={licenseNumber}
                      onChange={(e) => setLicenseNumber(e.target.value)}
                      placeholder="如：京网微剧备字（2026）第001号"
                    />
                  </label>
                )}
              </div>
            </div>

            {error && (
              <div style={{ color: "var(--danger, #c45c47)", fontSize: 13, marginBottom: 10 }}>
                {error}
              </div>
            )}

            <div style={{ display: "flex", justifyContent: "flex-end", gap: 10 }}>
              <button style={secondaryBtnStyle} onClick={handleClose}>
                取消
              </button>
              <button
                style={primaryBtnStyle}
                onClick={handleStart}
                disabled={starting}
              >
                {starting ? (
                  <>
                    <Loader2 size={14} className="animate-spin" /> 启动中…
                  </>
                ) : (
                  <>
                    <Zap size={14} /> 开始生成
                  </>
                )}
              </button>
            </div>
          </>
        ) : (
          <>
            <div
              style={{
                fontSize: 13,
                color: "var(--text-secondary)",
                marginBottom: 14,
                lineHeight: 1.6,
              }}
            >
              任务 <code>{taskId}</code> {running ? "执行中…" : "已结束"}
            </div>

            <ProgressBar
              connected={progress.connected}
              status={progress.status}
              percent={progress.percent}
              message={progress.message}
              result={progress.result}
              error={progress.error}
            />

            {report && (
              <div style={{ ...sectionStyle, marginTop: 14 }}>
                <div style={sectionTitleStyle}>
                  <Check size={13} />
                  执行报告
                </div>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 10 }}>
                  {Object.entries(STEP_LABELS).map(([key, label]) => {
                    const step = report.steps?.[key];
                    const skipped = step?.skipped === true;
                    const done = !!step && !skipped;
                    return (
                      <span
                        key={key}
                        style={{
                          ...chipStyle,
                          cursor: "default",
                          ...(done
                            ? { borderColor: "var(--accent)", color: "var(--accent)" }
                            : {}),
                          opacity: skipped ? 0.55 : 1,
                        }}
                      >
                        {label}
                        {skipped ? "（跳过）" : done ? " ✓" : ""}
                      </span>
                    );
                  })}
                </div>
                {typeof report.total_elapsed_seconds === "number" && (
                  <div style={{ fontSize: 12, color: "var(--text-secondary)", marginBottom: 8 }}>
                    总耗时 {report.total_elapsed_seconds.toFixed(1)}s
                  </div>
                )}
                {finalVideoUrl && (
                  <div style={{ marginTop: 6 }}>
                    <video
                      src={finalVideoUrl}
                      controls
                      style={{ width: "100%", maxHeight: 320, borderRadius: 12 }}
                    />
                    <div style={{ fontSize: 12, marginTop: 6 }}>
                      <a href={finalVideoUrl} target="_blank" rel="noreferrer">
                        打开成片文件
                      </a>
                    </div>
                  </div>
                )}
                {report.error && (
                  <div style={{ color: "var(--danger, #c45c47)", fontSize: 13, marginTop: 8 }}>
                    {report.error}
                  </div>
                )}
              </div>
            )}

            {error && (
              <div style={{ color: "var(--danger, #c45c47)", fontSize: 13, margin: "10px 0" }}>
                {error}
              </div>
            )}

            <div style={{ display: "flex", justifyContent: "flex-end", gap: 10, marginTop: 14 }}>
              {running ? (
                <>
                  <button style={secondaryBtnStyle} onClick={handleClose}>
                    后台运行
                  </button>
                  <button
                    style={dangerBtnStyle}
                    onClick={handleCancel}
                    disabled={cancelling}
                  >
                    {cancelling ? (
                      <>
                        <Loader2 size={13} className="animate-spin" /> 取消中…
                      </>
                    ) : (
                      <>
                        <Square size={13} /> 取消任务
                      </>
                    )}
                  </button>
                </>
              ) : (
                <>
                  <button style={secondaryBtnStyle} onClick={handleClose}>
                    关闭
                  </button>
                  {canvasScript && (
                    <button style={primaryBtnStyle} onClick={handleLoadToCanvas}>
                      <Workflow size={14} /> 加载到画布
                    </button>
                  )}
                  <button style={primaryBtnStyle} onClick={handleReset}>
                    <Zap size={14} /> 再来一条
                  </button>
                </>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
