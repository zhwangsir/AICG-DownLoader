// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
/**
 * 模型文件名选择器：从模型库（/api/v1/model-library/models）拉取真实文件，
 * 可搜索下拉替代手填；当前值不在库中时给出缺失警示但保留显示。
 * NSFW 条目遵循 R18 开关（后端未解锁时下拉不含 NSFW 模型）。
 */
import { Check, ChevronDown, Loader2, Search, TriangleAlert } from "lucide-react";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { Input } from "@/components/ui/input";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { cn } from "@/lib/utils";
import {
  useModelLibraryItems,
  type ModelLibraryEntry,
} from "@/lib/queries/model-library";

function formatSize(bytes: number): string {
  if (bytes >= 1 << 30) return `${(bytes / (1 << 30)).toFixed(2)} GB`;
  if (bytes >= 1 << 20) return `${(bytes / (1 << 20)).toFixed(1)} MB`;
  return `${(bytes / 1024).toFixed(0)} KB`;
}

export function ModelNamePicker({
  value,
  onChange,
  expectedTypes,
  disabled,
  ariaLabel,
}: {
  value: string;
  onChange: (filename: string) => void;
  /** 候选子目录（checkpoints/loras/...），下拉只列这些类型 */
  expectedTypes: string[];
  disabled?: boolean;
  ariaLabel?: string;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const { items, isLoading } = useModelLibraryItems(open);

  const candidates = useMemo(() => {
    const inType = items.filter((e) => expectedTypes.includes(e.type));
    if (!query.trim()) return inType;
    const q = query.trim().toLowerCase();
    return inType.filter(
      (e) =>
        e.name.toLowerCase().includes(q) || e.rel_path.toLowerCase().includes(q),
    );
  }, [items, expectedTypes, query]);

  const present = useMemo(() => {
    if (!value) return null;
    return items.some((e) => e.name === value && expectedTypes.includes(e.type));
  }, [items, value, expectedTypes]);

  const select = (entry: ModelLibraryEntry) => {
    onChange(entry.name);
    setOpen(false);
    setQuery("");
  };

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger
        disabled={disabled}
        aria-label={ariaLabel ?? t("settings.library.picker.button")}
        className={cn(
          "flex h-8 w-full items-center gap-1.5 rounded-md border border-border bg-transparent px-2 text-left text-xs transition-colors",
          disabled
            ? "cursor-not-allowed opacity-50"
            : "hover:border-muted-foreground/50",
          present === false && "border-amber-400/60",
        )}
      >
        <span className="min-w-0 flex-1 truncate font-mono">
          {value || t("settings.library.picker.empty")}
        </span>
        {present === false && (
          <TriangleAlert
            className="size-3.5 shrink-0 text-amber-400"
            aria-label={t("settings.library.picker.notInLibrary")}
          />
        )}
        <ChevronDown className="size-3.5 shrink-0 text-muted-foreground" aria-hidden />
      </PopoverTrigger>
      <PopoverContent className="w-80 p-2" align="start">
        <div className="relative mb-1.5">
          <Search
            className="absolute top-1/2 left-2 size-3.5 -translate-y-1/2 text-muted-foreground"
            aria-hidden
          />
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={t("settings.library.picker.searchPlaceholder")}
            className="h-7 pl-7 text-xs"
            aria-label={t("settings.library.picker.searchPlaceholder")}
          />
        </div>
        <div className="max-h-64 overflow-y-auto" role="listbox">
          {isLoading ? (
            <div className="flex h-16 items-center justify-center gap-2 text-xs text-muted-foreground">
              <Loader2 className="size-3.5 animate-spin" aria-hidden />
              {t("settings.library.loading")}
            </div>
          ) : candidates.length === 0 ? (
            <div className="flex h-16 items-center justify-center px-2 text-center text-xs text-muted-foreground">
              {t("settings.library.picker.noCandidates")}
            </div>
          ) : (
            candidates.map((entry) => (
              <button
                key={`${entry.root}/${entry.rel_path}`}
                type="button"
                role="option"
                aria-selected={entry.name === value}
                onClick={() => select(entry)}
                className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-xs hover:bg-white/[0.06]"
              >
                <Check
                  className={cn(
                    "size-3.5 shrink-0",
                    entry.name === value ? "text-cyan-300" : "opacity-0",
                  )}
                  aria-hidden
                />
                <span className="min-w-0 flex-1">
                  <span className="block truncate font-mono">{entry.name}</span>
                  <span className="block text-[10px] text-muted-foreground">
                    {entry.root} · {entry.type}
                    {entry.nsfw ? " · NSFW" : ""}
                  </span>
                </span>
                <span className="shrink-0 font-mono text-[10px] text-muted-foreground">
                  {formatSize(entry.size)}
                </span>
              </button>
            ))
          )}
        </div>
      </PopoverContent>
    </Popover>
  );
}
