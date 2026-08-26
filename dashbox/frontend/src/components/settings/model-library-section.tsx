// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
/**
 * 设置-模型库页：NAS 本地模型浏览 / Civitai 搜索下载 / NSFW 门禁。
 * 数据来源：novelvideo.model_library（/api/v1/model-library/*）。
 */
import {
  Download,
  Film,
  HardDrive,
  ImagePlus,
  Loader2,
  Lock,
  LockOpen,
  RefreshCw,
  Search,
  ShieldAlert,
  ShieldOff,
  XCircle,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Progress } from "@/components/ui/progress";
import { Textarea } from "@/components/ui/textarea";
import { ModelNamePicker } from "@/components/settings/model-name-picker";
import { WorksGallery } from "@/components/settings/works-gallery";
import { cn } from "@/lib/utils";
import { useDownloadRequestStore } from "@/stores/downloadRequestStore";
import {
  useCancelModelDownload,
  useCivitaiSearch,
  useGenerateImage,
  useModelDownloadTasks,
  useModelLibrary,
  useNsfwMarks,
  useNsfwStatus,
  useRefreshModelLibrary,
  useSetNsfw,
  useSetNsfwMark,
  useStartModelDownload,
  type CivitaiFile,
  type CivitaiModel,
  type ModelDownloadTask,
} from "@/lib/queries/model-library";

const DOWNLOAD_SUBDIRS = [
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
] as const;

function formatSize(bytes: number): string {
  if (bytes >= 1 << 30) return `${(bytes / (1 << 30)).toFixed(2)} GB`;
  if (bytes >= 1 << 20) return `${(bytes / (1 << 20)).toFixed(1)} MB`;
  if (bytes >= 1 << 10) return `${(bytes / (1 << 10)).toFixed(0)} KB`;
  return `${bytes} B`;
}

function formatDate(mtime: number): string {
  return new Date(mtime * 1000).toLocaleDateString();
}

function formatSpeed(bps: number): string {
  return `${formatSize(bps)}/s`;
}

/** ky 对非 2xx 抛 HTTPError：优先读 ky v2 已解析的 error.data，再尝试响应体（FastAPI detail / 信封 error）。 */
async function requestErrorMessage(error: unknown, fallback: string): Promise<string> {
  const data = (error as { data?: { detail?: string; error?: string } } | null)?.data;
  if (data) {
    if (typeof data.detail === "string" && data.detail) return data.detail;
    if (typeof data.error === "string" && data.error) return data.error;
  }
  const response = (error as { response?: Response } | null)?.response;
  if (response && !response.bodyUsed) {
    try {
      const body = (await response.clone().json()) as {
        detail?: string;
        error?: string;
      };
      if (typeof body.detail === "string" && body.detail) return body.detail;
      if (typeof body.error === "string" && body.error) return body.error;
    } catch {
      /* fallthrough */
    }
  }
  return fallback;
}

export function ModelLibrarySection() {
  const { t } = useTranslation();
  const [tab, setTab] = useState<"nas" | "download" | "studio" | "works">("nas");
  const [gateOpen, setGateOpen] = useState(false);
  const nsfwQuery = useNsfwStatus();
  const nsfwEnabled = nsfwQuery.data?.data?.nsfw_enabled ?? false;
  const pendingDownload = useDownloadRequestStore((s) => s.pending);
  const clearDownload = useDownloadRequestStore((s) => s.clear);
  const [prefill, setPrefill] = useState<{ query: string; subdir?: string } | null>(null);

  // 缺失一键补齐：消费跨页下载请求 → 切下载页签 + 预填搜索词/目标目录
  useEffect(() => {
    if (pendingDownload) {
      setTab("download");
      setPrefill({ query: pendingDownload.query, subdir: pendingDownload.subdir });
      clearDownload();
    }
  }, [pendingDownload, clearDownload]);

  return (
    <div className="flex h-full flex-col gap-4 p-5">
      <div className="flex items-center gap-2">
        <div className="flex gap-1 rounded-md bg-white/[0.05] p-0.5">
          <button
            type="button"
            role="tab"
            aria-selected={tab === "nas"}
            onClick={() => setTab("nas")}
            className={cn(
              "flex h-8 items-center gap-1.5 rounded px-3 text-sm font-medium transition-colors",
              tab === "nas"
                ? "bg-white/[0.09] text-foreground"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            <HardDrive className="size-3.5" aria-hidden />
            {t("settings.library.tabs.nas")}
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={tab === "download"}
            onClick={() => setTab("download")}
            className={cn(
              "flex h-8 items-center gap-1.5 rounded px-3 text-sm font-medium transition-colors",
              tab === "download"
                ? "bg-white/[0.09] text-foreground"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            <Download className="size-3.5" aria-hidden />
            {t("settings.library.tabs.download")}
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={tab === "studio"}
            onClick={() => setTab("studio")}
            className={cn(
              "flex h-8 items-center gap-1.5 rounded px-3 text-sm font-medium transition-colors",
              tab === "studio"
                ? "bg-white/[0.09] text-foreground"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            <ImagePlus className="size-3.5" aria-hidden />
            {t("settings.library.tabs.studio")}
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={tab === "works"}
            onClick={() => setTab("works")}
            className={cn(
              "flex h-8 items-center gap-1.5 rounded px-3 text-sm font-medium transition-colors",
              tab === "works"
                ? "bg-white/[0.09] text-foreground"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            <Film className="size-3.5" aria-hidden />
            {t("settings.library.tabs.works")}
          </button>
        </div>
        <div className="ml-auto">
          <Button
            type="button"
            variant={nsfwEnabled ? "secondary" : "outline"}
            size="sm"
            onClick={() => setGateOpen(true)}
            aria-label={t("settings.library.nsfw.button")}
            className="gap-1.5"
          >
            {nsfwEnabled ? (
              <LockOpen className="size-3.5" aria-hidden />
            ) : (
              <Lock className="size-3.5" aria-hidden />
            )}
            NSFW
            <Badge
              variant={nsfwEnabled ? "default" : "secondary"}
              className="ml-0.5 px-1.5 text-[10px]"
            >
              {nsfwEnabled
                ? t("settings.library.nsfw.unlocked")
                : t("settings.library.nsfw.locked")}
            </Badge>
          </Button>
        </div>
      </div>

      {tab === "nas" ? (
        <NasModelList nsfwEnabled={nsfwEnabled} />
      ) : tab === "studio" ? (
        <ImageStudioTab />
      ) : tab === "works" ? (
        <WorksGallery />
      ) : (
        <ModelDownloadTab prefill={prefill} />
      )}

      <NsfwGateDialog open={gateOpen} onOpenChange={setGateOpen} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// NAS 模型列表
// ---------------------------------------------------------------------------

function NasModelList({ nsfwEnabled }: { nsfwEnabled: boolean }) {
  const { t } = useTranslation();
  const [typeFilter, setTypeFilter] = useState<string>("");
  const [query, setQuery] = useState("");
  const libraryQuery = useModelLibrary(
    {
      type: typeFilter || undefined,
      q: query || undefined,
      includeNsfw: nsfwEnabled,
    },
    true,
  );
  const refresh = useRefreshModelLibrary();
  const marksQuery = useNsfwMarks(nsfwEnabled);
  const setMark = useSetNsfwMark();
  const marks = marksQuery.data?.data?.marks ?? {};

  const data = libraryQuery.data?.data;
  const items = data?.items ?? [];
  const types = data?.types ?? [];

  /** 点击行内标记按钮：已覆盖且与现状一致 → 清除覆盖回退关键词；否则写入反向覆盖 */
  const handleToggleMark = (m: (typeof items)[number]) => {
    const override = marks[m.rel_path];
    const clearOnly = override !== undefined && override === m.nsfw;
    setMark.mutate({ rel_path: m.rel_path, nsfw: clearOnly ? null : !m.nsfw });
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3">
      <div className="flex items-center gap-2">
        <div className="relative flex-1">
          <Search
            className="absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-muted-foreground"
            aria-hidden
          />
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={t("settings.library.nas.searchPlaceholder")}
            className="h-8 pl-8 text-sm"
            aria-label={t("settings.library.nas.searchPlaceholder")}
          />
        </div>
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={refresh.isPending}
          onClick={() => refresh.mutate()}
          aria-label={t("settings.library.nas.refresh")}
        >
          {refresh.isPending ? (
            <Loader2 className="size-3.5 animate-spin" aria-hidden />
          ) : (
            <RefreshCw className="size-3.5" aria-hidden />
          )}
        </Button>
      </div>

      <div className="flex flex-wrap gap-1.5" role="group" aria-label={t("settings.library.nas.typeFilter")}>
        <button
          type="button"
          onClick={() => setTypeFilter("")}
          className={cn(
            "rounded-full border px-2.5 py-0.5 text-xs transition-colors",
            !typeFilter
              ? "border-cyan-400/50 bg-cyan-400/15 text-cyan-300"
              : "border-border text-muted-foreground hover:text-foreground",
          )}
        >
          {t("settings.library.nas.allTypes")}
        </button>
        {types.map((tp) => (
          <button
            key={tp}
            type="button"
            onClick={() => setTypeFilter(tp === typeFilter ? "" : tp)}
            className={cn(
              "rounded-full border px-2.5 py-0.5 text-xs transition-colors",
              typeFilter === tp
                ? "border-cyan-400/50 bg-cyan-400/15 text-cyan-300"
                : "border-border text-muted-foreground hover:text-foreground",
            )}
          >
            {tp}
          </button>
        ))}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto rounded-md border border-border">
        {libraryQuery.isLoading ? (
          <div className="flex h-32 items-center justify-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="size-4 animate-spin" aria-hidden />
            {t("settings.library.loading")}
          </div>
        ) : libraryQuery.isError ? (
          <div className="flex h-32 items-center justify-center px-4 text-center text-sm text-red-400">
            {t("settings.library.nas.loadFailed")}
          </div>
        ) : items.length === 0 ? (
          <div className="flex h-32 items-center justify-center px-4 text-center text-sm text-muted-foreground">
            {t("settings.library.nas.empty")}
          </div>
        ) : (
          <ul className="divide-y divide-border">
            {items.map((m) => (
              <li
                key={`${m.root}/${m.rel_path}`}
                className="flex items-center gap-3 px-3 py-2 text-sm"
              >
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="truncate font-medium" title={m.rel_path}>
                      {m.name}
                    </span>
                    {m.nsfw && (
                      <Badge variant="destructive" className="px-1.5 text-[10px]">
                        NSFW
                      </Badge>
                    )}
                  </div>
                  <div className="mt-0.5 text-xs text-muted-foreground">
                    {m.root} · {formatDate(m.mtime)}
                  </div>
                </div>
                <Badge variant="secondary" className="shrink-0 font-mono text-[10px]">
                  {m.type}
                </Badge>
                <span className="w-20 shrink-0 text-right font-mono text-xs text-muted-foreground">
                  {formatSize(m.size)}
                </span>
                {nsfwEnabled && (
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon-sm"
                    className={cn(
                      "size-7 shrink-0",
                      m.nsfw
                        ? "text-red-400 hover:text-red-300"
                        : "text-muted-foreground hover:text-foreground",
                    )}
                    aria-label={t("settings.library.nas.toggleMark")}
                    title={
                      marks[m.rel_path] !== undefined
                        ? t("settings.library.nas.clearMark")
                        : m.nsfw
                          ? t("settings.library.nas.markSfw")
                          : t("settings.library.nas.markNsfw")
                    }
                    disabled={setMark.isPending}
                    onClick={() => handleToggleMark(m)}
                  >
                    {m.nsfw ? (
                      <ShieldOff className="size-3.5" aria-hidden />
                    ) : (
                      <ShieldAlert className="size-3.5" aria-hidden />
                    )}
                  </Button>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="text-xs text-muted-foreground">
        {t("settings.library.nas.total", { count: data?.total ?? 0 })}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 下载页签
// ---------------------------------------------------------------------------

function ModelDownloadTab({
  prefill,
}: {
  prefill?: { query: string; subdir?: string } | null;
}) {
  const { t } = useTranslation();
  const [searchInput, setSearchInput] = useState(prefill?.query ?? "");
  const [submittedQ, setSubmittedQ] = useState(prefill?.query ?? "");
  // 预填请求到达时覆盖搜索词并立即触发搜索
  useEffect(() => {
    if (prefill?.query) {
      setSearchInput(prefill.query);
      setSubmittedQ(prefill.query);
    }
  }, [prefill]);
  const searchQuery = useCivitaiSearch({ q: submittedQ }, Boolean(submittedQ));
  const tasksQuery = useModelDownloadTasks();
  const tasks = tasksQuery.data?.data?.items ?? [];

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3">
      <form
        className="flex items-center gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          setSubmittedQ(searchInput.trim());
        }}
      >
        <div className="relative flex-1">
          <Search
            className="absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-muted-foreground"
            aria-hidden
          />
          <Input
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            placeholder={t("settings.library.download.searchPlaceholder")}
            className="h-8 pl-8 text-sm"
            aria-label={t("settings.library.download.searchPlaceholder")}
          />
        </div>
        <Button type="submit" size="sm" disabled={!searchInput.trim()}>
          {t("settings.library.download.search")}
        </Button>
      </form>

      <div className="min-h-0 flex-1 overflow-y-auto">
        {submittedQ && (
          <div className="mb-4">
            {searchQuery.isLoading ? (
              <div className="flex h-24 items-center justify-center gap-2 text-sm text-muted-foreground">
                <Loader2 className="size-4 animate-spin" aria-hidden />
                {t("settings.library.loading")}
              </div>
            ) : searchQuery.isError ? (
              <div className="flex h-24 items-center justify-center text-sm text-red-400">
                {t("settings.library.download.searchFailed")}
              </div>
            ) : (searchQuery.data?.data?.items ?? []).length === 0 ? (
              <div className="flex h-24 items-center justify-center text-sm text-muted-foreground">
                {t("settings.library.download.noResults")}
              </div>
            ) : (
              <ul className="space-y-2">
                {(searchQuery.data?.data?.items ?? []).map((m) => (
                  <CivitaiResultCard
                    key={m.id ?? m.name}
                    model={m}
                    preferredSubdir={prefill?.subdir}
                  />
                ))}
              </ul>
            )}
          </div>
        )}

        <div>
          <h3 className="mb-2 text-sm font-medium text-muted-foreground">
            {t("settings.library.download.tasks")}
          </h3>
          {tasks.length === 0 ? (
            <div className="rounded-md border border-dashed border-border px-3 py-6 text-center text-sm text-muted-foreground">
              {t("settings.library.download.noTasks")}
            </div>
          ) : (
            <ul className="space-y-2">
              {tasks.map((task) => (
                <DownloadTaskRow key={task.task_id} task={task} />
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}

function CivitaiResultCard({
  model,
  preferredSubdir,
}: {
  model: CivitaiModel;
  /** 缺失补齐跳转时预选的目标子目录（优先于按 Civitai 类型猜测） */
  preferredSubdir?: string;
}) {
  const { t } = useTranslation();
  const files = useMemo(() => {
    const all: Array<{ file: CivitaiFile; versionName: string }> = [];
    for (const v of model.versions) {
      for (const f of v.files) {
        all.push({ file: f, versionName: v.name });
      }
    }
    all.sort((a, b) => Number(b.file.primary) - Number(a.file.primary));
    return all.slice(0, 3);
  }, [model]);

  const [selected, setSelected] = useState(0);
  const [subdir, setSubdir] = useState<string>(
    preferredSubdir && (DOWNLOAD_SUBDIRS as readonly string[]).includes(preferredSubdir)
      ? preferredSubdir
      : guessSubdir(model.type),
  );
  const startDownload = useStartModelDownload();
  const [error, setError] = useState("");

  const current = files[selected];

  return (
    <li className="rounded-md border border-border p-3">
      <div className="flex items-center gap-2">
        <span className="min-w-0 flex-1 truncate text-sm font-medium" title={model.name}>
          {model.name}
        </span>
        <Badge variant="secondary" className="text-[10px]">
          {model.type}
        </Badge>
        {model.nsfw && (
          <Badge variant="destructive" className="text-[10px]">
            NSFW
          </Badge>
        )}
      </div>

      {current && (
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <select
            value={selected}
            onChange={(e) => setSelected(Number(e.target.value))}
            className="h-8 max-w-56 rounded-md border border-border bg-transparent px-2 text-xs"
            aria-label={t("settings.library.download.version")}
          >
            {files.map((f, i) => (
              <option key={`${f.versionName}-${f.file.name}`} value={i} className="bg-neutral-900">
                {f.versionName} · {f.file.name} ({formatSize(f.file.size_kb * 1024)})
              </option>
            ))}
          </select>
          <select
            value={subdir}
            onChange={(e) => setSubdir(e.target.value)}
            className="h-8 rounded-md border border-border bg-transparent px-2 text-xs"
            aria-label={t("settings.library.download.targetDir")}
          >
            {DOWNLOAD_SUBDIRS.map((d) => (
              <option key={d} value={d} className="bg-neutral-900">
                {d}
              </option>
            ))}
          </select>
          <Button
            type="button"
            size="sm"
            className="gap-1.5"
            disabled={startDownload.isPending}
            onClick={async () => {
              setError("");
              try {
                const resp = await startDownload.mutateAsync({
                  download_url: current.file.download_url,
                  filename: current.file.name,
                  subdir,
                  sha256: current.file.sha256,
                  nsfw: model.nsfw,
                });
                if (!resp.ok) {
                  setError(resp.error ?? t("settings.library.download.startFailed"));
                }
              } catch (e) {
                setError(
                  await requestErrorMessage(e, t("settings.library.download.startFailed")),
                );
              }
            }}
          >
            {startDownload.isPending ? (
              <Loader2 className="size-3.5 animate-spin" aria-hidden />
            ) : (
              <Download className="size-3.5" aria-hidden />
            )}
            {t("settings.library.download.start")}
          </Button>
        </div>
      )}
      {error && <div className="mt-2 text-xs text-red-400">{error}</div>}
    </li>
  );
}

function guessSubdir(civitaiType: string): string {
  const t = civitaiType.toLowerCase();
  if (t.includes("lora") || t.includes("locon") || t.includes("lycoris")) return "loras";
  if (t.includes("checkpoint") || t.includes("model")) return "checkpoints";
  if (t.includes("vae")) return "vae";
  if (t.includes("controlnet")) return "controlnet";
  if (t.includes("embedding") || t.includes("textual")) return "embeddings";
  if (t.includes("upscale")) return "upscale_models";
  return "checkpoints";
}

function DownloadTaskRow({ task }: { task: ModelDownloadTask }) {
  const { t } = useTranslation();
  const cancel = useCancelModelDownload();
  const percent =
    task.total > 0 ? Math.min(100, (task.downloaded / task.total) * 100) : 0;
  const active = task.status === "pending" || task.status === "running";

  return (
    <li className="rounded-md border border-border p-3">
      <div className="flex items-center gap-2 text-sm">
        <span className="min-w-0 flex-1 truncate font-medium" title={task.dest}>
          {task.filename}
        </span>
        <Badge variant="secondary" className="font-mono text-[10px]">
          {task.subdir}
        </Badge>
        <TaskStatusBadge status={task.status} />
        {active && (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="h-6 w-6 p-0"
            disabled={cancel.isPending}
            onClick={() => cancel.mutate(task.task_id)}
            aria-label={t("settings.library.download.cancel")}
          >
            <XCircle className="size-4" aria-hidden />
          </Button>
        )}
      </div>
      {task.status === "running" && (
        <div className="mt-2 flex items-center gap-2">
          <Progress value={percent} className="h-1.5 flex-1" />
          <span className="w-28 shrink-0 text-right font-mono text-xs text-muted-foreground">
            {percent.toFixed(0)}% · {formatSpeed(task.speed_bps)}
          </span>
        </div>
      )}
      {task.status === "error" && task.error && (
        <div className="mt-1.5 text-xs text-red-400">{task.error}</div>
      )}
    </li>
  );
}

function TaskStatusBadge({ status }: { status: ModelDownloadTask["status"] }) {
  const { t } = useTranslation();
  const map = {
    pending: { label: t("settings.library.download.status.pending"), cls: "secondary" },
    running: { label: t("settings.library.download.status.running"), cls: "default" },
    done: { label: t("settings.library.download.status.done"), cls: "default" },
    error: { label: t("settings.library.download.status.error"), cls: "destructive" },
    canceled: { label: t("settings.library.download.status.canceled"), cls: "secondary" },
  } as const;
  const item = map[status];
  return (
    <Badge variant={item.cls as "default"} className="text-[10px]">
      {item.label}
    </Badge>
  );
}

// ---------------------------------------------------------------------------
// 生图测试台（checkpoint 选择 + 提示词 → local_gateway 出图）
// ---------------------------------------------------------------------------

const STUDIO_SIZES = [
  { value: "832x1216", key: "sizePortrait" },
  { value: "1216x832", key: "sizeLandscape" },
  { value: "1024x1024", key: "sizeSquare" },
] as const;

function ImageStudioTab() {
  const { t } = useTranslation();
  const [checkpoint, setCheckpoint] = useState("majicMIX realistic 麦橘写实_v7.safetensors");
  const [prompt, setPrompt] = useState("");
  const [negative, setNegative] = useState(
    "lowres, bad anatomy, bad hands, deformed, worst quality, watermark, text",
  );
  const [size, setSize] = useState("832x1216");
  const [error, setError] = useState("");
  const generate = useGenerateImage();

  const b64 = (generate.data?.ok ? generate.data.data.data?.[0]?.b64_json : undefined) ?? "";
  const canSubmit = prompt.trim().length > 0 && checkpoint.trim().length > 0;

  const submit = async () => {
    setError("");
    if (!canSubmit) {
      setError(t("settings.library.studio.emptyPrompt"));
      return;
    }
    try {
      await generate.mutateAsync({
        prompt: prompt.trim(),
        negative_prompt: negative.trim(),
        checkpoint: checkpoint.trim(),
        size,
      });
    } catch (e) {
      setError(await requestErrorMessage(e, t("settings.library.studio.nsfwBlocked")));
    }
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto">
      <div className="grid gap-3 md:grid-cols-2">
        <div className="space-y-1.5">
          <label className="text-xs font-medium text-muted-foreground" htmlFor="studio-checkpoint">
            {t("settings.library.studio.checkpoint")}
          </label>
          <ModelNamePicker
            value={checkpoint}
            onChange={setCheckpoint}
            expectedTypes={["checkpoints"]}
            ariaLabel={t("settings.library.studio.checkpoint")}
            getOptionDisabledReason={(entry) =>
              entry.sdxl_incompatible
                ? (entry.sdxl_incompatible_reason ?? "不兼容 SDXL 工作流")
                : null
            }
          />
        </div>
        <div className="space-y-1.5">
          <span className="text-xs font-medium text-muted-foreground">
            {t("settings.library.studio.size")}
          </span>
          <div className="flex gap-1 rounded-md bg-white/[0.05] p-0.5" id="studio-size">
            {STUDIO_SIZES.map((s) => (
              <button
                key={s.value}
                type="button"
                aria-selected={size === s.value}
                onClick={() => setSize(s.value)}
                className={cn(
                  "h-8 flex-1 rounded px-2 text-xs font-medium transition-colors",
                  size === s.value
                    ? "bg-white/[0.09] text-foreground"
                    : "text-muted-foreground hover:text-foreground",
                )}
              >
                {t(`settings.library.studio.${s.key}`)}
              </button>
            ))}
          </div>
        </div>
      </div>

      <div className="space-y-1.5">
        <label className="text-xs font-medium text-muted-foreground" htmlFor="studio-prompt">
          {t("settings.library.studio.prompt")}
        </label>
        <Textarea
          id="studio-prompt"
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          placeholder={t("settings.library.studio.promptPlaceholder")}
          rows={3}
          className="text-xs"
        />
      </div>

      <div className="space-y-1.5">
        <label className="text-xs font-medium text-muted-foreground" htmlFor="studio-negative">
          {t("settings.library.studio.negative")}
        </label>
        <Textarea
          id="studio-negative"
          value={negative}
          onChange={(e) => setNegative(e.target.value)}
          placeholder={t("settings.library.studio.negativePlaceholder")}
          rows={2}
          className="text-xs"
        />
      </div>

      <div className="flex items-center gap-2">
        <Button
          type="button"
          size="sm"
          onClick={() => void submit()}
          disabled={generate.isPending}
          className="gap-1.5"
        >
          {generate.isPending ? (
            <Loader2 className="size-3.5 animate-spin" aria-hidden />
          ) : (
            <ImagePlus className="size-3.5" aria-hidden />
          )}
          {generate.isPending
            ? t("settings.library.studio.generating")
            : t("settings.library.studio.generate")}
        </Button>
        {b64 && (
          <a
            href={`data:image/png;base64,${b64}`}
            download={`studio_${Date.now()}.png`}
            className="text-xs text-muted-foreground underline-offset-2 hover:underline"
          >
            {t("settings.library.studio.download")}
          </a>
        )}
        {error && (
          <span className="text-xs text-red-400" role="alert">
            {error}
          </span>
        )}
      </div>

      {b64 && (
        <div className="mt-1 overflow-hidden rounded-md border border-border">
          <img
            src={`data:image/png;base64,${b64}`}
            alt={t("settings.library.studio.result")}
            className="max-h-[50vh] w-full object-contain"
          />
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// NSFW（R18 确认）对话框
// ---------------------------------------------------------------------------

function NsfwGateDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { t } = useTranslation();
  const nsfwQuery = useNsfwStatus(open);
  const setNsfw = useSetNsfw();
  const enabled = nsfwQuery.data?.data?.nsfw_enabled ?? false;
  const [error, setError] = useState("");

  const submit = async (targetEnabled: boolean) => {
    setError("");
    try {
      const resp = await setNsfw.mutateAsync({ enabled: targetEnabled });
      if (!resp.ok) {
        setError(resp.error ?? t("settings.library.nsfw.failed"));
        return;
      }
    } catch (e) {
      setError(await requestErrorMessage(e, t("settings.library.nsfw.failed")));
      return;
    }
    setError("");
    onOpenChange(false);
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(v) => {
        if (!v) setError("");
        onOpenChange(v);
      }}
    >
      <DialogContent className="max-w-sm" aria-label={t("settings.library.nsfw.title")}>
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <ShieldAlert className="size-4 text-amber-400" aria-hidden />
            {t("settings.library.nsfw.title")}
          </DialogTitle>
        </DialogHeader>

        <div className="space-y-3">
          <div className="rounded-md border border-amber-400/30 bg-amber-400/10 px-3 py-2.5 text-sm text-amber-200">
            {enabled
              ? t("settings.library.nsfw.r18Enabled")
              : t("settings.library.nsfw.r18Warning")}
          </div>

          {error && <div className="text-sm text-red-400">{error}</div>}

          <div className="flex items-center gap-2 pt-1">
            {enabled ? (
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={setNsfw.isPending}
                onClick={() => void submit(false)}
              >
                {setNsfw.isPending ? (
                  <Loader2 className="size-3.5 animate-spin" aria-hidden />
                ) : (
                  <Lock className="size-3.5" aria-hidden />
                )}
                {t("settings.library.nsfw.hideR18")}
              </Button>
            ) : (
              <Button
                type="button"
                size="sm"
                disabled={setNsfw.isPending}
                onClick={() => void submit(true)}
              >
                {setNsfw.isPending ? (
                  <Loader2 className="size-3.5 animate-spin" aria-hidden />
                ) : (
                  <LockOpen className="size-3.5" aria-hidden />
                )}
                {t("settings.library.nsfw.confirmR18")}
              </Button>
            )}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
