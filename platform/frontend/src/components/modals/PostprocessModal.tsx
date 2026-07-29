import { useState } from "react";
import {
  generatePostprocess,
  VideoData,
  VoiceData,
  PostprocessData,
  PostprocessStep,
} from "../../api/client";
import { Check, Layers } from "../ui/Icon";
import {
  POSTPROCESS_STEP_META,
  POSTPROCESS_RESOLUTIONS,
  modalScrollStyle,
  sectionStyle,
  sectionTitleStyle,
} from "./shared";

// ============================================================================
// P4.4 后处理编排（超分 → 插帧 → 修复 → 降噪 → H.265 编码）
// ============================================================================

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
                        <span style={{ fontSize: "11px", color: "var(--error)" }}>（该场景无配音）</span>
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
                        <span style={{ color: "var(--error)" }}>失败</span>
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
          <div style={{ color: "var(--error)", fontSize: "12px", marginTop: "8px" }}>{error}</div>
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
