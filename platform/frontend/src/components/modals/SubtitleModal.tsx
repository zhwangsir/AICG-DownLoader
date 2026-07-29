import { useState } from "react";
import {
  generateSubtitle,
  VoiceData,
  SubtitleData,
} from "../../api/client";
import { useDramaStore } from "../../store/useDramaStore";
import {
  modalScrollStyle,
  sectionTitleStyle,
  itemBoxStyle,
  textareaStyle,
} from "./shared";

export function SubtitleModal({
  voices,
  onClose,
  onSuccess,
}: {
  voices: VoiceData[];
  onClose: () => void;
  onSuccess: (data: SubtitleData) => void;
}) {
  const subtitles = useDramaStore((s) => s.subtitles);
  const updateSubtitleSegment = useDramaStore((s) => s.updateSubtitleSegment);
  const [selectedSceneId, setSelectedSceneId] = useState<number | null>(voices[0]?.scene_id || null);
  const [language, setLanguage] = useState("zh");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const selectedVoice = voices.find((v) => v.scene_id === selectedSceneId) || null;
  const firstAudioUrl = selectedVoice?.audio_urls[0]?.audio_url || null;
  const selectedSubtitle = subtitles.find((s) => s.scene_id === selectedSceneId) || null;

  const handleGenerate = async () => {
    if (!firstAudioUrl || !selectedSceneId) return;
    setLoading(true);
    setError(null);
    try {
      const resp = await generateSubtitle({
        scene_id: selectedSceneId,
        audio_url: firstAudioUrl,
        language,
      });
      if (resp.success && resp.data) {
        onSuccess(resp.data);
      } else {
        setError(resp.error || "生成失败");
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
        <div className="modal-title">生成字幕（faster-whisper ASR）</div>
        {voices.length === 0 ? (
          <div style={{ color: "var(--text-secondary)", fontSize: "13px" }}>请先生成配音。</div>
        ) : (
          <>
            <div className="modal-field">
              <label className="modal-label">选择场景（取首条配音音频）</label>
              <select
                className="modal-input"
                value={selectedSceneId ?? ""}
                onChange={(e) => setSelectedSceneId(Number(e.target.value))}
              >
                {voices.map((v) => (
                  <option key={v.scene_id} value={v.scene_id}>
                    场景 {v.scene_id}（{v.total_lines} 条语音）
                  </option>
                ))}
              </select>
            </div>
            <div className="modal-field">
              <label className="modal-label">语言</label>
              <select
                className="modal-input"
                value={language}
                onChange={(e) => setLanguage(e.target.value)}
              >
                <option value="zh">中文</option>
                <option value="en">英文</option>
                <option value="auto">自动检测</option>
              </select>
            </div>
            {selectedVoice && (
              <div style={{ fontSize: "12px", color: "var(--text-secondary)", marginTop: "8px" }}>
                <div>配音音频: {selectedVoice.audio_urls.length} 条</div>
                {firstAudioUrl && (
                  <audio controls src={firstAudioUrl} style={{ width: "100%", marginTop: "6px" }} />
                )}
              </div>
            )}

            {selectedSubtitle && (
              <div style={{ marginTop: "12px" }}>
                <div style={sectionTitleStyle}>
                  字幕编辑（{selectedSubtitle.segments.length} 段，文本可编辑）
                </div>
                <div style={{ maxHeight: "260px", overflowY: "auto" }}>
                  {selectedSubtitle.segments.map((seg, i) => (
                    <div key={i} style={itemBoxStyle}>
                      <div
                        style={{
                          fontSize: "11px",
                          color: "var(--text-secondary)",
                          marginBottom: "4px",
                        }}
                      >
                        {seg.start.toFixed(1)}s → {seg.end.toFixed(1)}s
                      </div>
                      <textarea
                        style={textareaStyle}
                        value={seg.text}
                        onChange={(e) =>
                          updateSubtitleSegment(selectedSubtitle.scene_id, i, e.target.value)
                        }
                      />
                    </div>
                  ))}
                </div>
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
            onClick={handleGenerate}
            disabled={loading || !firstAudioUrl}
          >
            {loading ? <span className="loading"></span> : "生成字幕"}
          </button>
        </div>
      </div>
    </div>
  );
}
