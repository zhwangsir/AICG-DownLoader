import { useEffect, useState } from "react";
import { Loader2, Lock, LockOpen, X } from "lucide-react";
import {
  getCharacterLibrary,
  resolveStaticUrl,
  type CharacterAssetEntry,
} from "../../api/client";
import { useDramaStore } from "../../store/useDramaStore";
import ModelLibraryPanel from "./ModelLibraryPanel";
import EnginePanel from "./EnginePanel";

/** 调试日志统一前缀：排查数据加载与状态切换时按此前缀过滤控制台 */
const DBG = "[AssetLibrary]";

/**
 * LibTV 式右侧资产抽屉（320px）：
 * - 角色库 Tab：主体库定妆照 + 外观锁定卡 + 锁定状态（M24.1 @引用可视化的资产侧）
 * - 模型库 Tab：LoRA 注册表 + 触发词 chips + 下载状态（M24.3 注册表 UI 落地）
 *
 * 调试日志（console，统一 `[AssetLibrary]` 前缀过滤）：
 * - `panel` activePanel 每次变化（含收起 null）【info 级，DevTools 默认可见】
 * - `fetch:start/success/error` 数据加载生命周期（含耗时与条目数）【info/error 级】
 * - `render` 各状态分支（loading/error/empty/list）【debug 级，需开 Verbose】
 * - `fetch:cancelled` StrictMode 双调用丢弃 / 缩略图加载失败【debug/warn 级】
 */
export default function AssetLibraryPanel() {
  const activePanel = useDramaStore((s) => s.activePanel);
  const setActivePanel = useDramaStore((s) => s.setActivePanel);

  useEffect(() => {
    console.info(`${DBG} panel →`, activePanel ?? "(closed)");
  }, [activePanel]);

  if (!activePanel) return null;
  return (
    <aside className="asset-drawer" aria-label="资产库">
      <div className="asset-drawer-header">
        <span className="asset-drawer-title">
          {activePanel === "characters" ? "角色库" : activePanel === "engine" ? "引擎" : "模型库"}
        </span>
        <button
          className="asset-drawer-close"
          title="收起"
          onClick={() => {
            console.info(`${DBG} panel close clicked (was ${activePanel})`);
            setActivePanel(null);
          }}
        >
          <X size={15} />
        </button>
      </div>
      {activePanel === "characters" ? <CharacterList /> : activePanel === "engine" ? <EnginePanel /> : <ModelLibraryPanel />}
    </aside>
  );
}

function CharacterList() {
  const [items, setItems] = useState<CharacterAssetEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  /** 缩略图裂图降级：记录加载失败的 character_id，回退占位块 */
  const [brokenThumbs, setBrokenThumbs] = useState<Record<string, true>>({});

  useEffect(() => {
    let cancelled = false;
    const t0 = performance.now();
    console.info(`${DBG} characters fetch:start`);
    getCharacterLibrary()
      .then((list) => {
        if (cancelled) return console.debug(`${DBG} characters fetch:cancelled (stale response dropped)`);
        console.info(
          `${DBG} characters fetch:success — ${list.length} 条，耗时 ${(performance.now() - t0).toFixed(1)}ms`,
          JSON.stringify(
            list.map((c) => ({ id: c.character_id, name: c.name, locked: c.locked, hasFront: !!c.reference_images?.front }))
          )
        );
        setItems(list);
      })
      .catch((e) => {
        if (cancelled) return;
        console.error(`${DBG} characters fetch:error —`, e);
        setError(String(e));
      })
      .finally(() => {
        if (!cancelled) {
          console.debug(`${DBG} characters loading:false`);
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (loading) {
    console.debug(`${DBG} characters render:loading`);
    return <PanelHint icon="loading">加载角色资产…</PanelHint>;
  }
  if (error) {
    console.debug(`${DBG} characters render:error —`, error);
    return <PanelHint icon="error">加载失败：{error}</PanelHint>;
  }
  if (items.length === 0) {
    console.debug(`${DBG} characters render:empty`);
    return <PanelHint>暂无角色资产。生成角色定妆照后自动登记入库。</PanelHint>;
  }
  console.debug(`${DBG} characters render:list — ${items.length} 条`);

  return (
    <div className="asset-list">
      {items.map((c) => (
        <div key={c.character_id} className="asset-card">
          <div className="asset-card-thumb">
            {c.reference_images?.front && !brokenThumbs[c.character_id] ? (
              <img
                src={resolveStaticUrl(c.reference_images.front)}
                alt={c.name}
                onError={() => {
                  console.warn(`${DBG} 角色缩略图加载失败: ${c.name} ← ${c.reference_images.front}`);
                  setBrokenThumbs((prev) => ({ ...prev, [c.character_id]: true }));
                }}
              />
            ) : (
              <div className="asset-card-thumb-empty">
                {c.reference_images?.front ? "图失效" : "无图"}
              </div>
            )}
          </div>
          <div className="asset-card-body">
            <div className="asset-card-name">
              <span className="asset-card-name-text">{c.name}</span>
              <span
                className={"asset-badge" + (c.locked ? " locked" : "")}
                title={c.locked ? "已锁定：分镜/视频强制引用外观锁定卡" : "未锁定"}
              >
                {c.locked ? <Lock size={10} /> : <LockOpen size={10} />}
                {c.locked ? "锁定" : "未锁"}
              </span>
            </div>
            {c.role && <div className="asset-card-sub">{c.role}</div>}
            {c.appearance_lock && (
              <div className="asset-card-lock" title={c.appearance_lock}>
                {c.appearance_lock.slice(0, 60)}
                {c.appearance_lock.length > 60 ? "…" : ""}
              </div>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}

function PanelHint({
  children,
  icon,
}: {
  children: React.ReactNode;
  icon?: "loading" | "error";
}) {
  return (
    <div className="asset-hint">
      {icon === "loading" && <Loader2 size={16} className="spin" />}
      {children}
    </div>
  );
}
