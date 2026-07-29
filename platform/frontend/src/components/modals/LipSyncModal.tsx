import { useState } from "react";
import {
  generateLipSync,
  VideoData,
  VoiceData,
  LipSyncData,
  CharacterCardData,
} from "../../api/client";
import { Smile } from "../ui/Icon";
import {
  modalScrollStyle,
  sectionStyle,
  sectionTitleStyle,
} from "./shared";

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
                <div style={{ fontSize: "12px", color: "var(--error)" }}>
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
          <div style={{ color: "var(--error)", fontSize: "12px", marginTop: "8px" }}>{error}</div>
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
