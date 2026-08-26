// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
/**
 * 作品库画廊（套用 ToIV 作品库形态）：样本视频矩阵展示。
 * - 画廊网格（2/3/4 列断点自适应）+ 类型过滤 chips + 关键词搜索
 * - 点卡片开 VideoViewerModal 全屏播放（音画直出原生音轨）
 * - R18 条目仅在 R18 确认开启后出现（后端过滤）；卡片带 R18 徽章
 */
import { Film, Loader2, Play, RefreshCw, Search, ShieldAlert } from "lucide-react";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { VideoViewerModal } from "@/features/canvas/ui/VideoViewerModal";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import {
  useRefreshWorksLibrary,
  useWorksLibrary,
  workCoverUrl,
  workMediaUrl,
  type WorkItem,
} from "@/lib/queries/model-library";

const CATEGORY_FILTERS = [
  { key: "", labelKey: "settings.library.works.filterAll" },
  { key: "anime", labelKey: "settings.library.works.filterAnime" },
  { key: "real", labelKey: "settings.library.works.filterReal" },
  { key: "3d", labelKey: "settings.library.works.filter3d" },
] as const;

const FEATURE_FILTERS = [
  { key: "音画直出", labelKey: "settings.library.works.featureAvmux" },
  { key: "参考图", labelKey: "settings.library.works.featureRefImg" },
  { key: "参考视频", labelKey: "settings.library.works.featureRefVideo" },
  { key: "打斗", labelKey: "settings.library.works.featureCombat" },
  { key: "微表情", labelKey: "settings.library.works.featureMicroexp" },
  { key: "对白", labelKey: "settings.library.works.featureDialogue" },
  { key: "完整动作", labelKey: "settings.library.works.featureFullAct" },
] as const;

function categoryLabel(category: string, t: (k: string) => string): string {
  if (category === "anime") return t("settings.library.works.filterAnime");
  if (category === "real") return t("settings.library.works.filterReal");
  if (category === "3d") return t("settings.library.works.filter3d");
  return category;
}

function WorkCard({
  work,
  onOpen,
  testId,
}: {
  work: WorkItem;
  onOpen: (w: WorkItem) => void;
  testId?: string;
}) {
  const { t } = useTranslation();
  return (
    <button
      type="button"
      data-testid={testId}
      aria-label={`${work.title}`}
      onClick={() => onOpen(work)}
      className="group relative overflow-hidden rounded-lg border border-border bg-white/[0.03] text-left transition-colors hover:border-foreground/30"
    >
      <div className="relative aspect-video w-full bg-black/40">
        {work.has_cover !== false ? (
          <img
            src={workCoverUrl(work.id)}
            alt={work.title}
            loading="lazy"
            className="size-full object-cover transition-transform duration-300 group-hover:scale-[1.02]"
          />
        ) : (
          <div className="flex size-full items-center justify-center text-muted-foreground">
            <Film className="size-8" aria-hidden />
          </div>
        )}
        <div className="absolute inset-0 flex items-center justify-center bg-black/0 opacity-0 transition-all group-hover:bg-black/30 group-hover:opacity-100">
          <span className="flex size-10 items-center justify-center rounded-full bg-white/90 text-black shadow">
            <Play className="size-5 translate-x-px" aria-hidden />
          </span>
        </div>
        <div className="absolute left-2 top-2 flex gap-1">
          {work.nsfw ? (
            <Badge variant="default" className="bg-amber-500/90 text-[10px] text-black">
              R18
            </Badge>
          ) : null}
        </div>
        <div className="absolute bottom-2 right-2">
          <Badge variant="secondary" className="bg-black/70 text-[10px] text-white">
            {work.duration}
          </Badge>
        </div>
      </div>
      <div className="flex flex-col gap-1 p-2.5">
        <div className="flex items-center justify-between gap-2">
          <span className="truncate text-sm font-medium">{work.title}</span>
          <span className="shrink-0 text-[10px] text-muted-foreground">
            {categoryLabel(work.category, t)}
          </span>
        </div>
        <span className="truncate text-[11px] text-muted-foreground">{work.engine}</span>
      </div>
    </button>
  );
}

export function WorksGallery() {
  const { t } = useTranslation();
  const [category, setCategory] = useState("");
  const [feature, setFeature] = useState("");
  const [q, setQ] = useState("");
  const [searchInput, setSearchInput] = useState("");
  const [playing, setPlaying] = useState<WorkItem | null>(null);

  const params = useMemo(
    () => ({
      ...(category ? { category } : {}),
      ...(feature ? { feature } : {}),
      ...(q ? { q } : {}),
    }),
    [category, feature, q],
  );
  const worksQuery = useWorksLibrary(params);
  const refresh = useRefreshWorksLibrary();
  const items = worksQuery.data?.data?.items ?? [];

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex gap-1 rounded-md bg-white/[0.05] p-0.5">
          {CATEGORY_FILTERS.map((f) => (
            <button
              key={f.key || "all"}
              type="button"
              role="tab"
              aria-selected={category === f.key}
              data-testid={`works-category-${f.key || "all"}`}
              onClick={() => setCategory(f.key)}
              className={cn(
                "h-7 rounded px-2.5 text-xs font-medium transition-colors",
                category === f.key
                  ? "bg-white/[0.09] text-foreground"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              {t(f.labelKey)}
            </button>
          ))}
        </div>
        <div className="flex flex-wrap gap-1">
          {FEATURE_FILTERS.map((f) => (
            <button
              key={f.key}
              type="button"
              data-testid={`works-feature-${f.key}`}
              aria-pressed={feature === f.key}
              onClick={() => setFeature(feature === f.key ? "" : f.key)}
              className={cn(
                "h-7 rounded-full border px-2.5 text-xs transition-colors",
                feature === f.key
                  ? "border-foreground/40 bg-white/[0.09] text-foreground"
                  : "border-border text-muted-foreground hover:text-foreground",
              )}
            >
              {t(f.labelKey)}
            </button>
          ))}
        </div>
        <form
          className="relative ml-auto w-52"
          onSubmit={(e) => {
            e.preventDefault();
            setQ(searchInput.trim());
          }}
        >
          <Search
            className="absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground"
            aria-hidden
          />
          <Input
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            onBlur={() => setQ(searchInput.trim())}
            placeholder={t("settings.library.works.searchPlaceholder")}
            className="h-8 pl-8 text-sm"
            data-testid="works-search"
          />
        </form>
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="h-8 gap-1.5"
          onClick={() => refresh.mutate()}
          disabled={refresh.isPending}
          aria-label={t("settings.library.works.refresh")}
        >
          {refresh.isPending ? (
            <Loader2 className="size-3.5 animate-spin" aria-hidden />
          ) : (
            <RefreshCw className="size-3.5" aria-hidden />
          )}
        </Button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto pr-1">
        {worksQuery.isLoading ? (
          <div className="flex h-40 items-center justify-center text-muted-foreground">
            <Loader2 className="mr-2 size-4 animate-spin" aria-hidden />
            {t("settings.library.loading")}
          </div>
        ) : worksQuery.isError ? (
          <div className="flex h-40 flex-col items-center justify-center gap-2 text-muted-foreground">
            <ShieldAlert className="size-6" aria-hidden />
            <span className="text-sm">{t("settings.library.works.loadError")}</span>
            <Button variant="outline" size="sm" onClick={() => worksQuery.refetch()}>
              {t("settings.library.works.refresh")}
            </Button>
          </div>
        ) : items.length === 0 ? (
          <div className="flex h-40 flex-col items-center justify-center gap-1 text-muted-foreground">
            <Film className="size-6" aria-hidden />
            <span className="text-sm">{t("settings.library.works.empty")}</span>
          </div>
        ) : (
          <div
            className="grid gap-3"
            style={{
              gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))",
            }}
          >
            {items.map((w) => (
              <WorkCard key={w.id} work={w} onOpen={setPlaying} testId={`works-card-${w.id}`} />
            ))}
          </div>
        )}
      </div>

      <VideoViewerModal
        open={playing !== null}
        videoUrl={playing ? workMediaUrl(playing.id) : ""}
        title={playing ? `${playing.title} · ${playing.engine}` : undefined}
        onClose={() => setPlaying(null)}
      />
    </div>
  );
}
