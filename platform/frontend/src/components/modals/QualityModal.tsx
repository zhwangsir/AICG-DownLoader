import { useState } from "react";
import {
  checkQuality,
  applySubtitleFix,
  ScriptData,
  SubtitleData,
  QualityCheckData,
  SubtitleFixResult,
} from "../../api/client";
import { useDramaStore } from "../../store/useDramaStore";
import { Check, Wand2 } from "../ui/Icon";
import {
  modalScrollStyle,
  sectionStyle,
  sectionTitleStyle,
} from "./shared";

export function QualityModal({
  scriptData,
  subtitles,
  onClose,
  onSuccess,
}: {
  scriptData: ScriptData | null;
  subtitles: SubtitleData[];
  onClose: () => void;
  onSuccess: (data: QualityCheckData) => void;
}) {
  const qualityData = useDramaStore((s) => s.qualityData);
  const addSubtitle = useDramaStore((s) => s.addSubtitle);
  const setStatusInfo = useDramaStore((s) => s.setStatusInfo);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fixing, setFixing] = useState(false);
  const [fixResult, setFixResult] = useState<SubtitleFixResult | null>(null);
  const [fixError, setFixError] = useState<string | null>(null);

  const handleCheck = async () => {
    if (!scriptData) return;
    setLoading(true);
    setError(null);
    setFixResult(null);
    setFixError(null);
    try {
      const resp = await checkQuality({
        project_id: scriptData.project_id || `project-${Date.now()}`,
        title: scriptData.title,
        characters: scriptData.characters,
        scenes: scriptData.scenes,
        subtitles,
      });
      if (resp.success && resp.data) {
        onSuccess(resp.data);
      } else {
        setError(resp.error || "质检失败");
      }
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  };

  // 字幕闭环：基于质检 issues 自动修正 ASR 错别字并回写 SRT
  const handleApplySubtitleFix = async () => {
    if (!qualityData || subtitles.length === 0) return;
    setFixing(true);
    setFixError(null);
    try {
      const resp = await applySubtitleFix({
        subtitles,
        issues: qualityData.issues,
        persist: true,
      });
      if (resp.success && resp.data) {
        // 用修正后的字幕替换 store 中对应场景的字幕
        resp.data.fixed_subtitles.forEach((sub) => addSubtitle(sub));
        setFixResult(resp.data);
        setStatusInfo(
          `字幕修正完成: ${resp.data.fixed_count} 段已修正，${resp.data.corrections.length} 个错别字已回写 SRT`
        );
      } else {
        setFixError(resp.error || "字幕修正失败");
      }
    } catch (e) {
      setFixError(String(e));
    } finally {
      setFixing(false);
    }
  };

  // 仅当存在 subtitle 类 issues 且有字幕数据时显示修正按钮
  const hasSubtitleIssues =
    qualityData &&
    qualityData.issues.some((i) => i.category === "subtitle") &&
    subtitles.length > 0;

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" style={modalScrollStyle} onClick={(e) => e.stopPropagation()}>
        <div className="modal-title">剧本质检</div>
        {!scriptData ? (
          <div style={{ color: "var(--text-secondary)", fontSize: "13px" }}>请先生成剧本。</div>
        ) : (
          <>
            <div style={{ fontSize: "12px", color: "var(--text-secondary)", marginBottom: "12px" }}>
              将对《{scriptData.title}》进行台词一致性、剧情逻辑、敏感词检查。
            </div>
            <div style={{ fontSize: "12px", color: "var(--text-secondary)" }}>
              检查场景数: {scriptData.scenes.length} · 字幕数: {subtitles.length}
            </div>

            {qualityData && (
              <div style={{ ...sectionStyle, marginTop: "12px" }}>
                <div style={sectionTitleStyle}>已有质检结果</div>
                <div style={{ fontSize: "13px", marginBottom: "4px" }}>
                  质量分: <span style={{ color: "var(--accent)" }}>{qualityData.score}</span>
                </div>
                <div
                  style={{
                    fontSize: "12px",
                    color: "var(--text-secondary)",
                    marginBottom: "6px",
                  }}
                >
                  {qualityData.summary}
                </div>
                {qualityData.issues.length > 0 && (
                  <div style={{ maxHeight: "180px", overflowY: "auto" }}>
                    {qualityData.issues.map((iss, i) => (
                      <div
                        key={i}
                        style={{
                          fontSize: "12px",
                          marginBottom: "4px",
                          paddingLeft: "6px",
                          borderLeft: "2px solid var(--border)",
                        }}
                      >
                        <span
                          style={{
                            color:
                              iss.severity === "critical"
                                ? "#a55"
                                : iss.severity === "warning"
                                ? "#aa5"
                                : "var(--text-secondary)",
                          }}
                        >
                          [{iss.severity}]
                        </span>{" "}
                        {iss.message}
                        {iss.suggestion && (
                          <div style={{ color: "var(--text-secondary)" }}>
                            建议: {iss.suggestion}
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}

            {/* 字幕闭环：质检发现字幕错别字时提供一键修正 */}
            {hasSubtitleIssues && (
              <div style={{ ...sectionStyle, marginTop: "12px" }}>
                <div style={sectionTitleStyle}>字幕错别字自动修正（P1-2 闭环）</div>
                <div style={{ fontSize: "12px", color: "var(--text-secondary)", marginBottom: "8px" }}>
                  检测到字幕类问题，可一键提取修正对、回写 SRT 文件并更新字幕数据。
                </div>
                <button
                  className="topbar-btn"
                  style={{ background: "var(--accent-dim)", borderColor: "var(--accent)" }}
                  onClick={handleApplySubtitleFix}
                  disabled={fixing}
                >
                  {fixing ? (
                    <span className="loading"></span>
                  ) : (
                    <>
                      <Wand2 size={14} strokeWidth={2} />
                      <span style={{ marginLeft: "4px" }}>一键修正字幕错别字</span>
                    </>
                  )}
                </button>
                {fixResult && (
                  <div style={{ marginTop: "8px", fontSize: "12px" }}>
                    <div style={{ color: "var(--accent)" }}>
                      <Check size={12} strokeWidth={2.5} />
                      <span style={{ marginLeft: "2px" }}>
                        已修正 {fixResult.fixed_count} 段字幕，提取 {fixResult.corrections.length} 个错别字
                      </span>
                    </div>
                    {fixResult.corrections.length > 0 && (
                      <div style={{ marginTop: "6px", display: "flex", flexWrap: "wrap", gap: "4px" }}>
                        {fixResult.corrections.map((c, i) => (
                          <span
                            key={i}
                            style={{
                              padding: "1px 6px",
                              background: "var(--bg-primary)",
                              border: "1px solid var(--border)",
                              borderRadius: "3px",
                              fontSize: "11px",
                            }}
                          >
                            {c.wrong} → {c.right}
                          </span>
                        ))}
                      </div>
                    )}
                    {fixResult.persisted_files.length > 0 && (
                      <div style={{ marginTop: "6px", color: "var(--text-secondary)" }}>
                        已回写 {fixResult.persisted_files.length} 个 SRT 文件
                      </div>
                    )}
                  </div>
                )}
                {fixError && (
                  <div style={{ color: "var(--error)", fontSize: "12px", marginTop: "6px" }}>{fixError}</div>
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
            取消
          </button>
          <button
            className="topbar-btn topbar-btn-primary"
            onClick={handleCheck}
            disabled={loading || !scriptData}
          >
            {loading ? <span className="loading"></span> : "开始质检"}
          </button>
        </div>
      </div>
    </div>
  );
}
