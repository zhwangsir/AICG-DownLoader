import { useState } from "react";
import { SendHorizonal, Sparkles } from "lucide-react";
import { useDramaStore } from "../../store/useDramaStore";
import TaskCenter from "../task/TaskCenter";

/**
 * LibTV 式底部常驻 Agent 输入框（「说出你的创意」）。
 * 回车/发送 → 创意写入 store.draftPremise 并打开剧本模态（预填），
 * 不直接触发 LLM，保持「人确认后再生成」的协同节奏。
 * 右侧集成 DramaClaw 式任务中心触发器（全局长任务统一视图）。
 */
export default function AgentBar() {
  const [text, setText] = useState("");
  const setDraftPremise = useDramaStore((s) => s.setDraftPremise);
  const setModal = useDramaStore((s) => s.setModal);
  const globalLoading = useDramaStore((s) => s.globalLoading);

  const submit = () => {
    const v = text.trim();
    if (!v || globalLoading) return;
    setDraftPremise(v);
    setModal("script", true);
    setText("");
  };

  return (
    <div className="agent-bar">
      <div className="agent-bar-inner">
        <Sparkles size={15} className="agent-bar-icon" />
        <input
          className="agent-bar-input"
          placeholder="说出你的创意，回车开始创作…"
          value={text}
          disabled={globalLoading}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.nativeEvent.isComposing) submit();
          }}
        />
        <TaskCenter />
        <button
          className="agent-bar-send"
          title="以该创意新建剧本"
          disabled={!text.trim() || globalLoading}
          onClick={submit}
        >
          <SendHorizonal size={14} />
        </button>
      </div>
    </div>
  );
}
