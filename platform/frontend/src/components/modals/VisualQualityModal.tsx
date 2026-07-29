import { useState } from "react";
import {
  checkVisualQuality,
  VideoData,
  QualityVisualData,
} from "../../api/client";
import {
  modalScrollStyle,
  compactInputStyle,
} from "./shared";

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
          <div style={{ color: "var(--error)", fontSize: "12px", marginTop: "8px" }}>{error}</div>
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
