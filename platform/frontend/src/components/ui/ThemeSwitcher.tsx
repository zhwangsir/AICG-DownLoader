import { useEffect, useState } from "react";
import { Palette, Check } from "./Icon";

/**
 * Film Atelier 多配色主题切换器
 * ----------------------------------------------------------------
 * 三套主题：
 *   - darkroom-amber  暗房琥珀（默认，安全灯暖色调）
 *   - silver-halide    银盐冷调（黑白银盐摄影）
 *   - cyanotype        蓝晒（普鲁士蓝）
 *
 * 切换通过在 <html> 上设置 data-theme 属性生效，
 * 配套 CSS 变量体系在 index.css 中已定义。
 * 选择持久化到 localStorage。
 */

export type ThemeName = "darkroom-amber" | "silver-halide" | "cyanotype";

const THEME_KEY = "film-atelier-theme";
const THEMES: { id: ThemeName; label: string; swatch: string[] }[] = [
  {
    id: "darkroom-amber",
    label: "暗房琥珀",
    swatch: ["#0f0e0c", "#1a1816", "#d4a574"],
  },
  {
    id: "silver-halide",
    label: "银盐冷调",
    swatch: ["#0c0d0f", "#16181b", "#b8c5d4"],
  },
  {
    id: "cyanotype",
    label: "蓝晒",
    swatch: ["#0a0e14", "#131a24", "#5da9c4"],
  },
];

function readInitialTheme(): ThemeName {
  if (typeof window === "undefined") return "darkroom-amber";
  const saved = window.localStorage.getItem(THEME_KEY) as ThemeName | null;
  if (saved && THEMES.some((t) => t.id === saved)) return saved;
  return "darkroom-amber";
}

export function ThemeSwitcher() {
  const [theme, setTheme] = useState<ThemeName>(readInitialTheme);
  const [open, setOpen] = useState(false);

  // 同步到 <html data-theme="..."> + 持久化
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    window.localStorage.setItem(THEME_KEY, theme);
  }, [theme]);

  // 点击外部关闭下拉
  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      const target = e.target as HTMLElement;
      if (!target.closest(".theme-switcher")) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  return (
    <div className="theme-switcher">
      <button
        type="button"
        className="theme-switcher-btn"
        onClick={() => setOpen((v) => !v)}
        aria-label="切换主题"
        aria-expanded={open}
        title="Film Atelier 主题"
      >
        <Palette size={14} />
        <span className="theme-switcher-label">主题</span>
      </button>
      {open && (
        <div className="theme-switcher-dropdown" role="menu">
          {THEMES.map((t) => (
            <button
              key={t.id}
              type="button"
              className="theme-switcher-option"
              onClick={() => {
                setTheme(t.id);
                setOpen(false);
              }}
              role="menuitemradio"
              aria-checked={theme === t.id}
            >
              <span className="theme-swatch">
                {t.swatch.map((c) => (
                  <span
                    key={c}
                    className="theme-swatch-cell"
                    style={{ background: c }}
                  />
                ))}
              </span>
              <span className="theme-option-label">{t.label}</span>
              {theme === t.id && <Check size={12} />}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
