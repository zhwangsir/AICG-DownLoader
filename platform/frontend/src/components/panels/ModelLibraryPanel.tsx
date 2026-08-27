import { useCallback, useEffect, useRef, useState } from "react";
import {
  CheckCircle2,
  Download,
  Loader2,
  Lock,
  LockOpen,
  RefreshCw,
  Search,
  XCircle,
} from "lucide-react";
import {
  cancelDownloadTask,
  getDownloadTasks,
  getModelRegistry,
  getNasLibrary,
  searchCivitaiModels,
  startModelDownload,
  type CivitaiModel,
  type DownloadTask,
  type ModelLoraEntry,
  type NasModelEntry,
} from "../../api/client";
import { useDramaStore } from "../../store/useDramaStore";

/** 调试日志统一前缀 */
const DBG = "[ModelLibrary]";

type TabKey = "registry" | "nas" | "download";

/** Civitai 模型类型 → ComfyUI 落盘子目录 */
const CIVITAI_TYPE_TO_SUBDIR: Record<string, string> = {
  Checkpoint: "checkpoints",
  LORA: "loras",
  VAE: "vae",
  Controlnet: "controlnet",
  TextualInversion: "embeddings",
  Upscaler: "upscale_models",
};

const SUBDIR_OPTIONS = [
  "checkpoints",
  "loras",
  "vae",
  "clip",
  "clip_vision",
  "controlnet",
  "diffusion_models",
  "text_encoders",
  "upscale_models",
  "embeddings",
  "ipadapter",
  "unet",
];

function fmtSize(bytes: number): string {
  if (bytes >= 1 << 30) return `${(bytes / (1 << 30)).toFixed(2)} GB`;
  if (bytes >= 1 << 20) return `${(bytes / (1 << 20)).toFixed(1)} MB`;
  return `${(bytes / 1024).toFixed(0)} KB`;
}

function fmtDate(mtime: number): string {
  const d = new Date(mtime * 1000);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function Hint({
  children,
  loading,
}: {
  children: React.ReactNode;
  loading?: boolean;
}) {
  return (
    <div className="asset-hint">
      {loading && <Loader2 size={16} className="spin" />}
      {children}
    </div>
  );
}

/**
 * M27 模型库面板（LibTV 式页签）：
 * - 注册表：LoRA 注册表（原 AssetLibraryPanel ModelList 逻辑迁移）
 * - NAS 模型：NAS 已有模型可视化浏览（名称/大小/类型/修改日期/NSFW 标记）
 * - 下载：Civitai 搜索 + 直链下载 + 后台任务进度（无缝写入 NAS）
 * 右上角 NSFW 锁：点击弹门禁（PIN），解锁后 NAS 模型/搜索含 NSFW 内容。
 */
export default function ModelLibraryPanel() {
  const [tab, setTab] = useState<TabKey>("registry");
  const nsfwEnabled = useDramaStore((s) => s.nsfwEnabled);
  const setModal = useDramaStore((s) => s.setModal);

  return (
    <div className="model-lib">
      <div className="model-lib-tabs" role="tablist">
        <button
          role="tab"
          aria-selected={tab === "registry"}
          className={"model-lib-tab" + (tab === "registry" ? " active" : "")}
          onClick={() => setTab("registry")}
        >
          注册表
        </button>
        <button
          role="tab"
          aria-selected={tab === "nas"}
          className={"model-lib-tab" + (tab === "nas" ? " active" : "")}
          onClick={() => setTab("nas")}
        >
          NAS 模型
        </button>
        <button
          role="tab"
          aria-selected={tab === "download"}
          className={"model-lib-tab" + (tab === "download" ? " active" : "")}
          onClick={() => setTab("download")}
        >
          下载
        </button>
        <button
          className={"model-lib-nsfw" + (nsfwEnabled ? " unlocked" : "")}
          title={nsfwEnabled ? "NSFW 已解锁（点击管理）" : "NSFW 已锁定（点击输入 PIN 解锁）"}
          onClick={() => setModal("nsfwGate", true)}
        >
          {nsfwEnabled ? <LockOpen size={13} /> : <Lock size={13} />}
          NSFW
        </button>
      </div>
      {tab === "registry" && <LoraRegistryList />}
      {tab === "nas" && <NasModelList />}
      {tab === "download" && <ModelDownloadTab />}
    </div>
  );
}

/** LoRA 注册表（原 AssetLibraryPanel ModelList 逻辑，2026-08-16 迁移） */
function LoraRegistryList() {
  const [loras, setLoras] = useState<ModelLoraEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [rootError, setRootError] = useState("");

  useEffect(() => {
    let cancelled = false;
    getModelRegistry()
      .then((reg) => {
        if (cancelled) return;
        setLoras(reg.loras ?? []);
        const srcErr = (reg.sources as { error?: string | null } | undefined)?.error;
        setRootError(typeof srcErr === "string" && srcErr ? srcErr : "");
      })
      .catch((e) => {
        if (cancelled) return;
        console.error(`${DBG} registry fetch:error —`, e);
        setError(String(e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (loading) return <Hint loading>加载模型注册表…</Hint>;
  if (error) return <Hint>加载失败：{error}</Hint>;
  if (loras.length === 0) {
    return (
      <>
        {rootError ? <Hint>模型树不可读：{rootError}</Hint> : null}
        <Hint>暂无 LoRA 注册记录。</Hint>
      </>
    );
  }

  return (
    <div className="asset-list">
      {rootError ? <Hint>模型树不可读：{rootError}</Hint> : null}
      {loras.map((m) => (
        <div key={m.filename} className="asset-card asset-card-model">
          <div className="asset-card-body">
            <div className="asset-card-name" title={m.filename}>
              <span className="asset-card-name-text">{m.name || m.filename}</span>
              <span
                className={"asset-badge" + (m.downloaded ? " downloaded" : "")}
                title={m.downloaded ? "下载器已下载" : "未下载（仅清单登记）"}
              >
                {m.downloaded ? <CheckCircle2 size={10} /> : <Download size={10} />}
                {m.downloaded ? "已下载" : "未下载"}
              </span>
            </div>
            {m.trigger_words?.length > 0 && (
              <div className="asset-card-chips">
                {m.trigger_words.slice(0, 4).map((w) => (
                  <span key={w} className="asset-chip" title={`触发词：${w}`}>
                    {w}
                  </span>
                ))}
                {m.trigger_words.length > 4 && (
                  <span className="asset-chip asset-chip-more">
                    +{m.trigger_words.length - 4}
                  </span>
                )}
              </div>
            )}
            <div className="asset-card-sub">
              权重 {m.weight} · {m.style_key || "通用"}
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}

/** NAS 模型库浏览：名称/大小/类型/修改日期 + 搜索/类型过滤/NSFW 过滤 */
function NasModelList() {
  const nsfwEnabled = useDramaStore((s) => s.nsfwEnabled);
  const setModal = useDramaStore((s) => s.setModal);
  const [items, setItems] = useState<NasModelEntry[]>([]);
  const [types, setTypes] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [q, setQ] = useState("");
  const [typeFilter, setTypeFilter] = useState("");
  const [includeNsfw, setIncludeNsfw] = useState(false);

  const load = useCallback(
    async (refresh = false) => {
      setLoading(true);
      setError("");
      try {
        const data = await getNasLibrary({
          type: typeFilter || undefined,
          q: q || undefined,
          include_nsfw: includeNsfw && nsfwEnabled,
          refresh,
        });
        setItems(data.items);
        setTypes(data.types);
        console.info(`${DBG} nas fetch:success — ${data.total} 条（cache=${data.cache_hit}）`);
      } catch (e) {
        console.error(`${DBG} nas fetch:error —`, e);
        setError(String(e));
      } finally {
        setLoading(false);
      }
    },
    [q, typeFilter, includeNsfw, nsfwEnabled]
  );

  // 搜索词防抖 400ms
  useEffect(() => {
    const timer = setTimeout(() => load(), 400);
    return () => clearTimeout(timer);
  }, [load]);

  return (
    <div className="model-lib-section">
      <div className="model-lib-toolbar">
        <div className="model-lib-search">
          <Search size={13} />
          <input
            placeholder="搜索模型名/路径…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
        </div>
        <select
          value={typeFilter}
          onChange={(e) => setTypeFilter(e.target.value)}
          title="类型过滤"
        >
          <option value="">全部类型</option>
          {types.map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </select>
        <button title="强制重扫 NAS" onClick={() => load(true)}>
          <RefreshCw size={13} />
        </button>
      </div>
      <label className="model-lib-nsfw-toggle">
        <input
          type="checkbox"
          checked={includeNsfw && nsfwEnabled}
          disabled={!nsfwEnabled}
          onChange={(e) => setIncludeNsfw(e.target.checked)}
        />
        显示 NSFW
        {!nsfwEnabled && (
          <button
            className="model-lib-nsfw-hint"
            onClick={(e) => {
              e.preventDefault();
              setModal("nsfwGate", true);
            }}
          >
            <Lock size={11} /> 解锁
          </button>
        )}
      </label>

      {loading ? (
        <Hint loading>扫描 NAS 模型库…</Hint>
      ) : error ? (
        <Hint>加载失败：{error}</Hint>
      ) : items.length === 0 ? (
        <Hint>无匹配模型。</Hint>
      ) : (
        <div className="asset-list">
          {items.map((m) => (
            <div key={`${m.root}/${m.rel_path}`} className="asset-card asset-card-model">
              <div className="asset-card-body">
                <div className="asset-card-name" title={m.rel_path}>
                  <span className="asset-card-name-text">{m.name}</span>
                  {m.nsfw && (
                    <span className="asset-badge asset-badge-nsfw" title="NSFW 内容">
                      18+
                    </span>
                  )}
                  <span className="asset-badge" title={`类型：${m.type}`}>
                    {m.type}
                  </span>
                </div>
                <div className="asset-card-sub">
                  {fmtSize(m.size)} · {fmtDate(m.mtime)} · {m.root}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/** 模型下载：Civitai 搜索 → 选版本文件下载；后台任务进度轮询 */
function ModelDownloadTab() {
  const nsfwEnabled = useDramaStore((s) => s.nsfwEnabled);
  const [q, setQ] = useState("");
  const [searching, setSearching] = useState(false);
  const [results, setResults] = useState<CivitaiModel[]>([]);
  const [searchError, setSearchError] = useState("");
  const [tasks, setTasks] = useState<DownloadTask[]>([]);
  const [notice, setNotice] = useState("");
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const refreshTasks = useCallback(async () => {
    try {
      setTasks(await getDownloadTasks());
    } catch (e) {
      console.warn(`${DBG} tasks poll:error —`, e);
    }
  }, []);

  // 任务轮询：存在进行中任务时 1.5s，否则 5s
  useEffect(() => {
    refreshTasks();
    const hasActive = tasks.some((t) => t.status === "pending" || t.status === "running");
    pollRef.current = setInterval(refreshTasks, hasActive ? 1500 : 5000);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [refreshTasks, tasks]);

  const doSearch = async () => {
    setSearching(true);
    setSearchError("");
    try {
      const data = await searchCivitaiModels({
        q,
        limit: 12,
        include_nsfw: nsfwEnabled,
      });
      setResults(data.items);
      console.info(`${DBG} civitai search:success — ${data.total} 条`);
    } catch (e) {
      console.error(`${DBG} civitai search:error —`, e);
      setSearchError(String(e));
    } finally {
      setSearching(false);
    }
  };

  const downloadFile = async (
    model: CivitaiModel,
    file: { name: string; download_url: string; sha256: string | null }
  ) => {
    setNotice("");
    try {
      await startModelDownload({
        download_url: file.download_url,
        filename: file.name,
        subdir: CIVITAI_TYPE_TO_SUBDIR[model.type] ?? "checkpoints",
        sha256: file.sha256,
        nsfw: model.nsfw,
      });
      setNotice(`已开始下载：${file.name}`);
      await refreshTasks();
    } catch (e) {
      setNotice(String(e));
    }
  };

  const cancelTask = async (taskId: string) => {
    try {
      await cancelDownloadTask(taskId);
      await refreshTasks();
    } catch (e) {
      setNotice(String(e));
    }
  };

  return (
    <div className="model-lib-section">
      <div className="model-lib-toolbar">
        <div className="model-lib-search">
          <Search size={13} />
          <input
            placeholder="搜索 Civitai 模型…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && doSearch()}
          />
        </div>
        <button className="model-lib-btn" disabled={searching} onClick={doSearch}>
          {searching ? <Loader2 size={13} className="spin" /> : "搜索"}
        </button>
      </div>
      {notice && <div className="model-lib-notice">{notice}</div>}
      {searchError && <Hint>搜索失败:{searchError}</Hint>}

      {tasks.length > 0 && (
        <div className="model-lib-tasks">
          <div className="model-lib-subtitle">下载任务</div>
          {tasks.map((t) => {
            const pct =
              t.total > 0 ? Math.min(100, Math.round((t.downloaded / t.total) * 100)) : 0;
            return (
              <div key={t.task_id} className="model-lib-task">
                <div className="model-lib-task-head" title={t.dest}>
                  <span className="model-lib-task-name">{t.filename}</span>
                  {(t.status === "pending" || t.status === "running") && (
                    <button title="取消" onClick={() => cancelTask(t.task_id)}>
                      <XCircle size={13} />
                    </button>
                  )}
                </div>
                <div className="model-lib-task-bar">
                  <div
                    className={"model-lib-task-fill " + t.status}
                    style={{ width: t.status === "done" ? "100%" : `${pct}%` }}
                  />
                </div>
                <div className="model-lib-task-meta">
                  {t.status === "running" &&
                    `${pct}% · ${fmtSize(t.downloaded)}/${t.total ? fmtSize(t.total) : "?"} · ${fmtSize(t.speed_bps)}/s`}
                  {t.status === "pending" && "排队中…"}
                  {t.status === "done" && "已完成"}
                  {t.status === "canceled" && "已取消"}
                  {t.status === "error" && `失败：${t.error}`}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {results.length > 0 && (
        <div className="asset-list">
          {results.map((m) => {
            const primary = m.versions[0]?.files.find((f) => f.primary) ?? m.versions[0]?.files[0];
            if (!primary) return null;
            return (
              <div key={m.id} className="asset-card asset-card-model">
                <div className="asset-card-body">
                  <div className="asset-card-name" title={m.name}>
                    <span className="asset-card-name-text">{m.name}</span>
                    {m.nsfw && (
                      <span className="asset-badge asset-badge-nsfw" title="NSFW 内容">
                        18+
                      </span>
                    )}
                    <span className="asset-badge" title={`类型：${m.type}`}>
                      {m.type}
                    </span>
                  </div>
                  <div className="asset-card-sub">
                    {m.versions[0]?.name} · {fmtSize(primary.size_kb * 1024)}
                  </div>
                  <button
                    className="model-lib-btn model-lib-btn-dl"
                    onClick={() => downloadFile(m, primary)}
                  >
                    <Download size={12} /> 下载到 NAS
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}
      {results.length === 0 && !searching && !searchError && (
        <Hint>输入关键词搜索 Civitai 模型，一键下载到 NAS 模型库。</Hint>
      )}
    </div>
  );
}
