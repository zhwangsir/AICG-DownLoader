import { LayoutGrid, Package, Plus, Server, Users } from "lucide-react";
import { useDramaStore } from "../../store/useDramaStore";

/**
 * LibTV 式左侧竖向图标导航栏（52px）。
 * 新建剧本 / 画布 / 角色库 / 模型库 / 引擎 —— 资产面板通过 store.activePanel 驱动右侧抽屉。
 */
export default function LeftRail() {
  const activePanel = useDramaStore((s) => s.activePanel);
  const setActivePanel = useDramaStore((s) => s.setActivePanel);
  const setModal = useDramaStore((s) => s.setModal);
  const globalLoading = useDramaStore((s) => s.globalLoading);

  const togglePanel = (panel: "characters" | "models" | "engine") =>
    setActivePanel(activePanel === panel ? null : panel);

  return (
    <nav className="left-rail" aria-label="主导航">
      <button
        className="left-rail-btn left-rail-btn-primary"
        title="新建剧本"
        disabled={globalLoading}
        onClick={() => setModal("script", true)}
      >
        <Plus size={18} strokeWidth={2.4} />
      </button>
      <div className="left-rail-sep" />
      <button
        className={
          "left-rail-btn" + (activePanel === null ? " active" : "")
        }
        title="画布"
        onClick={() => setActivePanel(null)}
      >
        <LayoutGrid size={18} />
      </button>
      <button
        className={
          "left-rail-btn" + (activePanel === "characters" ? " active" : "")
        }
        title="角色库（主体库）"
        onClick={() => togglePanel("characters")}
      >
        <Users size={18} />
      </button>
      <button
        className={
          "left-rail-btn" + (activePanel === "models" ? " active" : "")
        }
        title="模型库"
        onClick={() => togglePanel("models")}
      >
        <Package size={18} />
      </button>
      <div className="left-rail-sep" />
      <button
        className={
          "left-rail-btn" + (activePanel === "engine" ? " active" : "")
        }
        title="DashBox（主界面 :8080）"
        onClick={() => togglePanel("engine")}
      >
        <Server size={18} />
      </button>
    </nav>
  );
}
