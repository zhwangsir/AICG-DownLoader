import { useState } from "react";
import {
  composeVideo,
  VideoData,
  VoiceData,
  SubtitleData,
  EditData,
} from "../../api/client";
import {
  TRANSITION_OPTIONS,
  RESOLUTION_OPTIONS,
  modalScrollStyle,
  sectionTitleStyle,
} from "./shared";

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
          <div style={{ color: "var(--error)", fontSize: "12px", marginTop: "8px" }}>{error}</div>
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
