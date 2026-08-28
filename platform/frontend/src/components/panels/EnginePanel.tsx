import { useCallback, useEffect, useState } from "react";
import { ExternalLink, Loader2, RefreshCw } from "lucide-react";
import { getPanelStatus, type PanelStatus } from "../../api/client";

const DBG = "[EnginePanel]";
const WEB = "http://127.0.0.1:8080";
const API = "http://127.0.0.1:8780";

function ListenBadge({ up }: { up: boolean | undefined }) {
  if (typeof up !== "boolean") return null;
  return (
    <span
      className={
        "engine-listen " + (up ? "engine-listen-up" : "engine-listen-down")
      }
    >
      {up ? "在线" : "离线"}
    </span>
  );
}

/**
 * Bundled DashBox finishing engine (DramaClaw upstream) — launch/status/link only.
 * Does not scrape or rebrand the engine UI.
 */
export default function EnginePanel() {
  const [status, setStatus] = useState<PanelStatus | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  const load = useCallback(() => {
    setLoading(true);
    getPanelStatus()
      .then((s) => {
        setStatus(s);
        setError("");
      })
      .catch((e: unknown) => {
        console.warn(DBG, e);
        setError(e instanceof Error ? e.message : String(e));
      })
      .finally(() => {
        setLoading(false);
      });
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const web = status?.dashbox.web || WEB;
  const api = status?.dashbox.api || API;

  return (
    <div className="engine-panel">
      <p className="engine-panel-lead">
        <strong>DashBox 引擎</strong>（Web :8080 / API :8780）是成片引擎模块。短剧 pipeline 在 :8100。
        上游 <strong>DramaClaw / DashBox</strong> 为第三方 Elastic License 2.0，不抓取其页面、不改其品牌文件。
      </p>

      <div className="engine-panel-toolbar">
        <button
          type="button"
          className="engine-refresh"
          onClick={load}
          disabled={loading}
        >
          <RefreshCw size={12} className={loading ? "spin" : undefined} />
          刷新
        </button>
      </div>

      <div className="engine-link-card">
        <div className="engine-link-label">
          Web UI
          <ListenBadge up={status?.dashbox.web_listening} />
        </div>
        <a className="engine-link" href={web} target="_blank" rel="noreferrer">
          {web}
          <ExternalLink size={12} />
        </a>
      </div>
      <div className="engine-link-card">
        <div className="engine-link-label">
          API
          <ListenBadge up={status?.dashbox.api_listening} />
        </div>
        <a className="engine-link" href={api} target="_blank" rel="noreferrer">
          {api}
          <ExternalLink size={12} />
        </a>
      </div>

      <p className="engine-panel-hint">
        启动：仓库根目录 <code>./start-aigcpannel.sh</code>
        （AIGCPannel：短剧后端 :8100 + DashBox 引擎 :8080；引擎-only 仍可用 <code>./start-engine.sh --up</code>）。
      </p>

      {loading && (
        <div className="engine-panel-status">
          <Loader2 size={14} className="spin" />
          读取面板状态…
        </div>
      )}
      {error && (
        <div className="engine-panel-status engine-panel-error">
          后端未就绪：{error}
        </div>
      )}
      {status && !loading && (
        <ul className="engine-panel-meta">
          <li>产品：{status.product}</li>
          <li>后端：{status.backend}</li>
          <li>
            config.json：{status.downloader_config_readable ? "可读" : "不可读"}
          </li>
          <li>
            models.json：{status.models_json_readable ? "可读" : "不可读"}
          </li>
          {(status.nas_model_roots ?? []).map((r) => (
            <li key={r.path}>
              模型树：{r.readable ? "可读" : "不可读"} — {r.path}
            </li>
          ))}
          {status.nas_model_roots_error ? (
            <li className="engine-panel-error">{status.nas_model_roots_error}</li>
          ) : null}
        </ul>
      )}
    </div>
  );
}
