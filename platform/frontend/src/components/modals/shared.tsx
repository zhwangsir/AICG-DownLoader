import { useId } from "react";
import type { CSSProperties } from "react";
import type { PostprocessStep } from "../../api/client";

export const GENRE_OPTIONS = [
  "都市悬疑",
  "古风仙侠",
  "科幻未来",
  "校园青春",
  "职场商战",
  "武侠江湖",
  "末日废土",
  "温情治愈",
  "犯罪推理",
  "奇幻冒险",
  "家庭伦理",
  "历史穿越",
  "甜宠恋爱",
  "恐怖惊悚",
  "医疗救援",
  "体育竞技",
  "美食治愈",
  "商战复仇",
];
export const STYLE_OPTIONS = ["写实电影感", "日系动漫", "国风水墨", "赛博朋克", "油画质感", "水彩插画", "黑白银盐", "复古胶片", "暗黑哥特", "极简主义"];
export const SHOT_TYPE_OPTIONS = ["特写", "近景", "中景", "全景", "远景", "鸟瞰", "仰拍", "俯拍", "过肩镜头", "手持跟拍"];
export const CAMERA_MOVEMENT_OPTIONS = ["固定", "推镜头", "拉镜头", "摇镜头", "移镜头", "跟拍", "升降", "手持晃动"];
export const EMOTION_OPTIONS = ["平静", "紧张", "温馨", "悲伤", "愤怒", "恐惧", "惊喜", "暧昧", "绝望", "希望"];
export const TRANSITION_OPTIONS = [
  { value: "none", label: "无" },
  { value: "fade", label: "淡入淡出" },
  { value: "slide", label: "滑动" },
  { value: "zoom", label: "缩放" },
  { value: "wipe", label: "擦除" },
];
export const RESOLUTION_OPTIONS = ["480x832", "720x1280", "1080x1920", "1280x720", "1920x1080"];
export const VOICE_OPTIONS = [
  { value: "zh-CN-XiaoxiaoNeural", label: "晓晓（女·温柔）" },
  { value: "zh-CN-YunxiNeural", label: "云希（男·沉稳）" },
  { value: "zh-CN-XiaoyiNeural", label: "晓伊（女·活泼）" },
  { value: "zh-CN-YunjianNeural", label: "云健（男·浑厚）" },
  { value: "zh-CN-XiaohanNeural", label: "晓涵（女·成熟）" },
  { value: "zh-CN-YunyangNeural", label: "云扬（男·标准）" },
];
export const RATE_OPTIONS = ["+0%", "+10%", "-10%", "+20%", "-20%", "+30%", "-30%"];

export const POSTPROCESS_STEP_META: { key: PostprocessStep; label: string; needsAudio?: boolean }[] = [
  { key: "super_resolution", label: "超分（RealBasicVSR x4）" },
  { key: "frame_interpolation", label: "插帧（RIFE）" },
  { key: "inpainting", label: "修复（ProPainter）" },
  { key: "audio_denoise", label: "降噪（DeepFilterNet3）", needsAudio: true },
  { key: "final_encode", label: "H.265 编码（VideoToolbox）" },
];

export const POSTPROCESS_RESOLUTIONS = ["1920x1080", "1080x1920", "2560x1440", "3840x2160"];

export const modalScrollStyle: CSSProperties = {
  maxHeight: "82vh",
  overflowY: "auto",
};

export const sectionStyle: CSSProperties = {
  border: "1px solid var(--border-light)",
  borderRadius: 16,
  padding: "18px",
  marginBottom: 16,
  background: "var(--bg-primary)",
  boxShadow: "var(--shadow-xs)",
  transition: "all 0.2s var(--ease-out)",
};

export const itemBoxStyle: CSSProperties = {
  border: "1px solid var(--border-light)",
  borderRadius: 14,
  padding: "16px",
  marginBottom: 14,
  background: "var(--bg-elevated)",
  boxShadow: "var(--shadow-xs)",
  transition: "all 0.2s var(--ease-out)",
};

export const textareaStyle: CSSProperties = {
  width: "100%",
  minHeight: 88,
  padding: "14px 16px",
  background: "var(--bg-primary)",
  border: "1px solid var(--border-light)",
  borderRadius: 12,
  color: "var(--text-primary)",
  fontSize: 14,
  fontFamily: "inherit",
  lineHeight: 1.65,
  resize: "vertical",
  outline: "none",
  transition: "all 0.2s var(--ease-out)",
  boxShadow: "var(--shadow-inner)",
};

export const compactInputStyle: CSSProperties = {
  padding: "10px 13px",
  fontSize: 13,
};

export const smallBtnStyle: CSSProperties = {
  padding: "7px 14px",
  fontSize: 12,
  fontWeight: 550,
  background: "var(--bg-primary)",
  border: "1px solid var(--border-light)",
  color: "var(--text-secondary)",
  borderRadius: 8,
  cursor: "pointer",
  transition: "all 0.18s var(--ease-out)",
};

export const sectionTitleStyle: CSSProperties = {
  fontSize: 13,
  color: "var(--accent)",
  marginBottom: 12,
  marginTop: 4,
  fontWeight: 600,
  letterSpacing: "-0.01em",
  display: "flex",
  alignItems: "center",
  gap: 6,
};

export const chipStyle: CSSProperties = {
  padding: "7px 14px",
  fontSize: 12,
  fontWeight: 500,
  background: "var(--bg-primary)",
  border: "1px solid var(--border-light)",
  color: "var(--text-secondary)",
  borderRadius: 20,
  cursor: "pointer",
  transition: "all 0.18s var(--ease-out)",
};

export const chipActiveStyle: CSSProperties = {
  ...chipStyle,
  background: "var(--accent-soft)",
  borderColor: "var(--accent)",
  color: "var(--accent)",
  fontWeight: 600,
  boxShadow: "0 2px 8px var(--accent-glow)",
};

export const primaryBtnStyle: CSSProperties = {
  padding: "11px 28px",
  fontSize: 14,
  fontWeight: 600,
  background: "linear-gradient(180deg, var(--accent) 0%, var(--accent-hover) 100%)",
  border: "1px solid var(--accent-hover)",
  color: "#fff",
  borderRadius: 12,
  cursor: "pointer",
  boxShadow: "0 2px 6px var(--accent-glow), 0 6px 16px -4px var(--accent-glow), inset 0 1px 0 rgba(255,255,255,0.2)",
  transition: "all 0.2s var(--ease-out)",
};

export const secondaryBtnStyle: CSSProperties = {
  padding: "11px 24px",
  fontSize: 14,
  fontWeight: 550,
  background: "transparent",
  border: "1px solid transparent",
  color: "var(--text-secondary)",
  borderRadius: 12,
  cursor: "pointer",
  transition: "all 0.2s var(--ease-out)",
};

export const dangerBtnStyle: CSSProperties = {
  padding: "10px 24px",
  fontSize: 13,
  fontWeight: 600,
  background: "linear-gradient(180deg, #c45c47, #a34837)",
  border: "1px solid #a34837",
  color: "#fff",
  borderRadius: 10,
  cursor: "pointer",
  boxShadow: "0 2px 6px rgba(196,92,71,0.25), 0 6px 16px -4px rgba(196,92,71,0.25), inset 0 1px 0 rgba(255,255,255,0.15)",
  transition: "all 0.2s var(--ease-out)",
};

export interface DialogueLine {
  text: string;
  character_name: string;
  character_role: string;
  character_age: number | null;
  rate: string;
  voice: string;
}

/** 下拉预设 + 自定义输入组合控件 */
export function ComboInput({
  value,
  onChange,
  options,
  placeholder,
}: {
  value: string;
  onChange: (v: string) => void;
  options: string[];
  placeholder?: string;
}) {
  const id = useId();
  return (
    <>
      <input
        className="modal-input"
        list={id}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
      />
      <datalist id={id}>
        {options.map((o) => (
          <option key={o} value={o} />
        ))}
      </datalist>
    </>
  );
}

/** 智能下拉：若当前值不在预设列表中，则自动追加为选项 */
export function SmartSelect({
  value,
  onChange,
  options,
}: {
  value: string;
  onChange: (v: string) => void;
  options: string[];
}) {
  const opts = value && !options.includes(value) ? [value, ...options] : options;
  return (
    <select className="modal-input" value={value} onChange={(e) => onChange(e.target.value)}>
      {opts.map((o) => (
        <option key={o} value={o}>
          {o}
        </option>
      ))}
    </select>
  );
}
