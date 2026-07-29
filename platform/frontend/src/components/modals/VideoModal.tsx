import { useEffect, useState } from "react";
import {
  generateVideoAsync,
  SceneData,
  StoryboardData,
  VideoData,
} from "../../api/client";
import { useProgress } from "../../hooks/useProgress";
import { ProgressBar } from "../ProgressBar";
import { modalScrollStyle, textareaStyle } from "./shared";

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
          <div style={{ color: "var(--error)", fontSize: "12px", marginTop: "8px" }}>{error}</div>
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
