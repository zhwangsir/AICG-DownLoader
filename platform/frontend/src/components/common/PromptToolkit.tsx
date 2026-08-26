import { useMemo, useState } from "react";
import { Sparkles, Wand2 } from "lucide-react";
import { agentAssist } from "../../api/client";
import { STYLE_OPTIONS } from "../modals/shared";

/**
 * M25.4 提示词助手（LibTV Prompts 优化的前端化）。
 * 三控件：
 * ① 风格预设 chips —— 点击把风格词附加到提示词（已含则跳过）
 * ② AI 补全 —— 复用 agentAssist(expand) 补全质量/细节词
 * ③ 权重语法预览 —— 实时解析 `(word:1.2)` 显示权重 chips（LibTV 权重高亮的轻量实现）
 */
export function PromptToolkit({
  text,
  onChange,
  context = "短剧分镜提示词",
  disabled,
}: {
  text: string;
  onChange: (v: string) => void;
  context?: string;
  disabled?: boolean;
}) {
  const [assisting, setAssisting] = useState(false);
  const [assistError, setAssistError] = useState("");

  /** 权重 token 实时解析：`(word:1.2)` / `(word)` 语法 */
  const weightTokens = useMemo(() => {
    const tokens: { word: string; weight: number }[] = [];
    const re = /\(([^():]+?)(?::([\d.]+))?\)/g;
    let m: RegExpExecArray | null;
    while ((m = re.exec(text)) !== null) {
      const word = m[1].trim();
      if (!word) continue;
      tokens.push({ word, weight: m[2] ? parseFloat(m[2]) : 1.1 });
    }
    return tokens;
  }, [text]);

  const appendStyle = (style: string) => {
    if (disabled || assisting) return;
    if (text.includes(style)) return; // 幂等：已含同风格词不重复注入
    onChange(text.trim() ? `${text.trim()}, ${style}` : style);
  };

  const handleAssist = async () => {
    if (!text.trim() || assisting || disabled) return;
    setAssisting(true);
    setAssistError("");
    try {
      const resp = await agentAssist({ text, context, action: "expand" });
      if (resp.success && resp.data) {
        onChange(resp.data.text);
      } else {
        setAssistError(resp.error || "补全失败");
      }
    } catch (e) {
      setAssistError(String(e));
    } finally {
      setAssisting(false);
    }
  };

  return (
    <div className="prompt-toolkit" data-testid="prompt-toolkit">
      <div className="prompt-toolkit-row">
        <span className="prompt-toolkit-label">
          <Sparkles size={11} /> 风格预设
        </span>
        <div className="prompt-toolkit-chips">
          {STYLE_OPTIONS.slice(0, 6).map((s) => {
            const active = text.includes(s);
            return (
              <button
                key={s}
                type="button"
                className={"prompt-toolkit-chip" + (active ? " active" : "")}
                disabled={disabled || assisting}
                title={active ? "已包含该风格词" : `附加「${s}」到提示词`}
                onClick={() => appendStyle(s)}
              >
                {s}
              </button>
            );
          })}
        </div>
        <button
          type="button"
          className="prompt-toolkit-assist"
          disabled={!text.trim() || disabled || assisting}
          title="AI 补全质量词与细节描写"
          onClick={handleAssist}
        >
          <Wand2 size={11} />
          {assisting ? "补全中…" : "AI 补全"}
        </button>
      </div>
      {assistError && <div className="prompt-toolkit-error">{assistError}</div>}
      {weightTokens.length > 0 && (
        <div className="prompt-toolkit-weights" data-testid="weight-tokens">
          {weightTokens.map((t, i) => (
            <span
              key={`${t.word}-${i}`}
              className="prompt-toolkit-weight-chip"
              title={`权重语法：(${t.word}:${t.weight})`}
            >
              {t.word} <em>×{t.weight}</em>
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
