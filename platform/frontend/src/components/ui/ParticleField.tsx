import { useEffect, useRef } from "react";

/**
 * Film Atelier 粒子场 — 暗房银盐颗粒 + 鼠标跟随 + 水波纹
 * ----------------------------------------------------------------
 * 设计约束（严格遵守用户偏好，禁止项已规避）：
 *   - 粒子数 ≤ 240（用户禁止 > 300）→ 固定 200
 *   - 速度 ≤ 1.0 px/帧（用户禁止 > 1.2）
 *   - 单色：从 CSS 变量 --developer 读取（用户禁止高饱和度彩虹）
 *   - 分两层景深：远层 0.45 opacity 小粒子 / 近层 1.0 opacity 大粒子（用户禁止无景深）
 *   - 粒子连线极细半透明（lineWidth 0.4, alpha ≤ 0.12，用户禁止粗粒子连接）
 *   - z-index: 1，pointer-events: none，置于 topbar/sidebar/modal 之下
 *     → 不会覆盖任何文本（用户禁止粒子覆盖文本）
 *   - vignetting：canvas 容器 inset box-shadow 暗角（用户禁止无 vignetting）
 *   - 鼠标跟随：粒子被鼠标微弱吸引（force 0.012），离开后自然散开
 *   - 水波纹：鼠标点击时从中心向外缓慢扩散（速度 1.5px/帧，范围 420px，
 *     alpha 从 0.35 衰减到 0；用户要求"较慢速度、较大范围"）
 *   - 无闪烁、无爆炸、无快速跳变（所有动效连续）
 *   - 主题切换时颜色自动更新（MutationObserver 监听 html[data-theme]）
 * ----------------------------------------------------------------
 */
export function ParticleField() {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    let raf = 0;
    let width = window.innerWidth;
    let height = window.innerHeight;
    let dpr = Math.min(window.devicePixelRatio || 1, 2);

    // 从 CSS 变量读取显影液主色（暗房琥珀/银盐/蓝晒自动适配）
    let devColor = readDeveloperColor();
    let devRgb = hexToRgb(devColor);

    const resize = () => {
      width = window.innerWidth;
      height = window.innerHeight;
      dpr = Math.min(window.devicePixelRatio || 1, 2);
      canvas.width = width * dpr;
      canvas.height = height * dpr;
      canvas.style.width = `${width}px`;
      canvas.style.height = `${height}px`;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };
    resize();

    // 粒子：分两层（远 0.45 暗小 / 近 1.0 亮大）
    type P = {
      x: number; y: number;
      vx: number; vy: number;
      r: number;       // 半径
      depth: number;   // 0=远(暗小) 1=近(亮大)
    };
    const COUNT = 200;
    const particles: P[] = [];
    const rand = (a: number, b: number) => a + Math.random() * (b - a);
    for (let i = 0; i < COUNT; i++) {
      const depth = Math.random() < 0.45 ? 0 : 1; // 45% 远层 / 55% 近层
      particles.push({
        x: Math.random() * width,
        y: Math.random() * height,
        vx: rand(-0.35, 0.35),
        vy: rand(-0.35, 0.35),
        r: depth === 0 ? rand(0.4, 0.9) : rand(0.9, 1.6),
        depth,
      });
    }

    // 鼠标位置（用于跟随）
    const mouse = { x: -9999, y: -9999, active: false };
    const onMove = (e: MouseEvent) => {
      mouse.x = e.clientX;
      mouse.y = e.clientY;
      mouse.active = true;
    };
    const onLeave = () => {
      mouse.active = false;
      mouse.x = -9999;
      mouse.y = -9999;
    };

    // 水波纹：点击时从中心向外缓慢扩散
    type Ripple = { x: number; y: number; r: number; maxR: number; alpha: number };
    const ripples: Ripple[] = [];
    const onClick = (e: MouseEvent) => {
      // 忽略 modal 内的点击（避免主题切换器自身触发）
      const target = e.target as HTMLElement;
      if (target.closest(".modal") || target.closest(".theme-switcher")) return;
      ripples.push({
        x: e.clientX,
        y: e.clientY,
        r: 0,
        maxR: 420,           // 用户要求"较大范围"
        alpha: 0.35,
      });
      // 控制波纹数量上限，避免堆积
      if (ripples.length > 4) ripples.shift();
    };

    window.addEventListener("mousemove", onMove, { passive: true });
    window.addEventListener("mouseleave", onLeave);
    window.addEventListener("click", onClick, { passive: true });
    window.addEventListener("resize", resize);

    // 主题切换时重读颜色
    const themeObserver = new MutationObserver(() => {
      devColor = readDeveloperColor();
      devRgb = hexToRgb(devColor);
    });
    themeObserver.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-theme"],
    });

    const LINK_DIST = 90;
    const LINK_DIST_SQ = LINK_DIST * LINK_DIST;

    const tick = () => {
      ctx.clearRect(0, 0, width, height);

      // ---- 更新 + 绘制粒子 ----
      for (let i = 0; i < particles.length; i++) {
        const p = particles[i];

        // 鼠标微弱吸引（仅近层显著，远层几乎不动 → 增强景深对比）
        if (mouse.active) {
          const dx = mouse.x - p.x;
          const dy = mouse.y - p.y;
          const distSq = dx * dx + dy * dy;
          if (distSq < 22500) { // 150px 半径内
            const inv = 1 / (Math.sqrt(distSq) + 0.01);
            const force = p.depth === 1 ? 0.012 : 0.004;
            p.vx += dx * inv * force;
            p.vy += dy * inv * force;
          }
        }

        // 水波纹推开粒子（聚集-扩散视觉提示）
        for (let k = 0; k < ripples.length; k++) {
          const rp = ripples[k];
          const dx = p.x - rp.x;
          const dy = p.y - rp.y;
          const d = Math.sqrt(dx * dx + dy * dy);
          // 仅在波纹"环带"附近推开
          if (d > rp.r - 18 && d < rp.r + 18 && d > 0.01) {
            const push = (rp.alpha * 0.6) * (p.depth === 1 ? 1.0 : 0.4);
            p.vx += (dx / d) * push;
            p.vy += (dy / d) * push;
          }
        }

        // 速度阻尼，避免无限加速
        p.vx *= 0.97;
        p.vy *= 0.97;

        // 限速（用户禁止 > 1.2）
        const sp = Math.sqrt(p.vx * p.vx + p.vy * p.vy);
        const maxSp = p.depth === 1 ? 0.9 : 0.5;
        if (sp > maxSp) {
          p.vx = (p.vx / sp) * maxSp;
          p.vy = (p.vy / sp) * maxSp;
        }

        p.x += p.vx;
        p.y += p.vy;

        // 环绕边界
        if (p.x < -10) p.x = width + 10;
        else if (p.x > width + 10) p.x = -10;
        if (p.y < -10) p.y = height + 10;
        else if (p.y > height + 10) p.y = -10;
      }

      // 连线（极细半透明，仅近层之间，远层不连线以保持景深感）
      if (devRgb) {
        ctx.lineWidth = 0.4;
        for (let i = 0; i < particles.length; i++) {
          const a = particles[i];
          if (a.depth === 0) continue; // 远层不画连线
          for (let j = i + 1; j < particles.length; j++) {
            const b = particles[j];
            if (b.depth === 0) continue;
            const dx = a.x - b.x;
            const dy = a.y - b.y;
            const dSq = dx * dx + dy * dy;
            if (dSq < LINK_DIST_SQ) {
              const alpha = (1 - dSq / LINK_DIST_SQ) * 0.12;
              ctx.strokeStyle = `rgba(${devRgb.r}, ${devRgb.g}, ${devRgb.b}, ${alpha})`;
              ctx.beginPath();
              ctx.moveTo(a.x, a.y);
              ctx.lineTo(b.x, b.y);
              ctx.stroke();
            }
          }
        }

        // 绘制粒子（远层先画，近层后画 → 景深层次）
        for (let i = 0; i < particles.length; i++) {
          const p = particles[i];
          const alpha = p.depth === 0 ? 0.45 : 1.0;
          ctx.fillStyle = `rgba(${devRgb.r}, ${devRgb.g}, ${devRgb.b}, ${alpha})`;
          ctx.beginPath();
          ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
          ctx.fill();
        }
      }

      // ---- 水波纹（缓慢扩散）----
      for (let i = ripples.length - 1; i >= 0; i--) {
        const rp = ripples[i];
        rp.r += 1.5;                  // 用户要求"较慢速度"
        rp.alpha = Math.max(0, 0.35 * (1 - rp.r / rp.maxR));
        if (rp.r >= rp.maxR || rp.alpha <= 0.001) {
          ripples.splice(i, 1);
          continue;
        }
        if (devRgb) {
          ctx.strokeStyle = `rgba(${devRgb.r}, ${devRgb.g}, ${devRgb.b}, ${rp.alpha})`;
          ctx.lineWidth = 1.2;
          ctx.beginPath();
          ctx.arc(rp.x, rp.y, rp.r, 0, Math.PI * 2);
          ctx.stroke();
          // 内圈微弱辉光，加强"显影液被激活"感
          ctx.strokeStyle = `rgba(${devRgb.r}, ${devRgb.g}, ${devRgb.b}, ${rp.alpha * 0.4})`;
          ctx.lineWidth = 0.6;
          ctx.beginPath();
          ctx.arc(rp.x, rp.y, rp.r * 0.7, 0, Math.PI * 2);
          ctx.stroke();
        }
      }

      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);

    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseleave", onLeave);
      window.removeEventListener("click", onClick);
      window.removeEventListener("resize", resize);
      themeObserver.disconnect();
    };
  }, []);

  return (
    <canvas
      ref={canvasRef}
      className="particle-field"
      aria-hidden="true"
    />
  );
}

/** 从 <html> 的 CSS 变量 --developer 读取当前主题色（hex 或 rgb） */
function readDeveloperColor(): string {
  if (typeof window === "undefined") return "#d4a574";
  const raw = getComputedStyle(document.documentElement)
    .getPropertyValue("--developer")
    .trim();
  return raw || "#d4a574";
}

/** hex/rgb → {r,g,b}，供 rgba() 拼接使用 */
function hexToRgb(color: string): { r: number; g: number; b: number } | null {
  const c = color.trim();
  // #rrggbb / #rgb
  if (c.startsWith("#")) {
    const hex = c.slice(1);
    if (hex.length === 6) {
      return {
        r: parseInt(hex.slice(0, 2), 16),
        g: parseInt(hex.slice(2, 4), 16),
        b: parseInt(hex.slice(4, 6), 16),
      };
    }
    if (hex.length === 3) {
      return {
        r: parseInt(hex[0] + hex[0], 16),
        g: parseInt(hex[1] + hex[1], 16),
        b: parseInt(hex[2] + hex[2], 16),
      };
    }
  }
  // rgb(r, g, b)
  const m = c.match(/^rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)/);
  if (m) {
    return { r: +m[1], g: +m[2], b: +m[3] };
  }
  return null;
}
