/**
 * 统一图标入口 — 全项目通过此文件引入 Lucide React 图标。
 *
 * 约束（AGENTS.md §九 / 用户偏好）：
 * - 全项目统一使用 lucide-react，禁止 emoji、其他图标库、自定义 SVG 图标
 * - 按需 re-export，保证 tree-shaking 友好
 * - 组件内严禁直接 `from "lucide-react"`，统一从此文件引入
 *
 * 新增图标时只需在下方 export 列表追加，无需改动调用方。
 */
export {
  // 通用状态
  Check,        // ✓ 完成
  Lock,         // 🔒 锁定
  Save,         // 保存
  Loader2,      // 加载中（可配合 animate-spin）
  AlertTriangle,// 警告/错误
  Info,         // 提示
  X,            // 关闭
  // 业务语义
  Film,         // 剧本/视频
  MessageSquare,// 台词/字幕
  Image,        // 分镜/角色定妆照
  Mic,          // 配音
  Video,        // 视频生成
  Scissors,     // 剪辑
  ShieldCheck,  // 质检
  Eye,          // 视觉质检
  Sparkles,     // AI 生成
  Wand2,        // AI 修正/魔法修复
  Settings,     // 设置
  Activity,     // 状态/连接
  Palette,      // 主题切换（Film Atelier 多配色）
  Smile,        // 唇形同步（P4.4）
  Layers,       // 后处理编排管线（P4.4）
} from "lucide-react";

import type {
  Check as CheckIcon,
  Lock as LockIcon,
  Save as SaveIcon,
  Loader2 as Loader2Icon,
  AlertTriangle as AlertTriangleIcon,
  Info as InfoIcon,
  X as XIcon,
  Film as FilmIcon,
  MessageSquare as MessageSquareIcon,
  Image as ImageIcon,
  Mic as MicIcon,
  Video as VideoIcon,
  Scissors as ScissorsIcon,
  ShieldCheck as ShieldCheckIcon,
  Eye as EyeIcon,
  Sparkles as SparklesIcon,
  Wand2 as Wand2Icon,
  Settings as SettingsIcon,
  Activity as ActivityIcon,
  Palette as PaletteIcon,
  Smile as SmileIcon,
  Layers as LayersIcon,
} from "lucide-react";

/** 项目中所有可用的图标组件类型联合，供泛型推断使用。 */
export type IconComponent =
  | typeof CheckIcon
  | typeof LockIcon
  | typeof SaveIcon
  | typeof Loader2Icon
  | typeof AlertTriangleIcon
  | typeof InfoIcon
  | typeof XIcon
  | typeof FilmIcon
  | typeof MessageSquareIcon
  | typeof ImageIcon
  | typeof MicIcon
  | typeof VideoIcon
  | typeof ScissorsIcon
  | typeof ShieldCheckIcon
  | typeof EyeIcon
  | typeof SparklesIcon
  | typeof Wand2Icon
  | typeof SettingsIcon
  | typeof ActivityIcon
  | typeof PaletteIcon
  | typeof SmileIcon
  | typeof LayersIcon;
