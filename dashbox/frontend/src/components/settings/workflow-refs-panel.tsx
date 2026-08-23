// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
/**
 * workflow JSON 模型引用面板：解析 draft JSON → loader 节点逐行
 * ModelNamePicker（选中回写 JSON），并提供「体检」按钮调后端预检。
 */
import { CircleCheck, CircleX, Download, Loader2, Stethoscope } from "lucide-react";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { ModelNamePicker } from "@/components/settings/model-name-picker";
import { Button } from "@/components/ui/button";
import {
  applyModelRef,
  extractModelRefs,
  type WorkflowModelRef,
} from "@/lib/comfyui-loaders";
import {
  useModelLibraryItems,
  usePreflightWorkflow,
  type PreflightResult,
} from "@/lib/queries/model-library";
import {
  filenameToQuery,
  useDownloadRequestStore,
} from "@/stores/downloadRequestStore";

export function WorkflowRefsPanel({
  draftText,
  onRewrite,
}: {
  draftText: string;
  /** 选中模型后回写 JSON 文本（父级负责 setDraft + commit） */
  onRewrite: (nextText: string) => void;
}) {
  const { t } = useTranslation();
  const preflight = usePreflightWorkflow();
  const [report, setReport] = useState<PreflightResult | null>(null);
  const { items } = useModelLibraryItems();
  const requestDownload = useDownloadRequestStore((s) => s.requestDownload);

  const parsed = useMemo(() => {
    try {
      const obj = JSON.parse(draftText) as unknown;
      if (obj && typeof obj === "object" && !Array.isArray(obj)) {
        return obj as Record<string, unknown>;
      }
    } catch {
      /* draft 未完成编辑时容忍 */
    }
    return null;
  }, [draftText]);

  const refs = useMemo(
    () => (parsed ? extractModelRefs(parsed) : []),
    [parsed],
  );

  /** 库外在位判定：缺失行显示「去下载」一键补齐 */
  const missingKeys = useMemo(() => {
    const keys = new Set<string>();
    for (const ref of refs) {
      const present = items.some(
        (e) => e.name === ref.filename && ref.expectedTypes.includes(e.type),
      );
      if (!present) keys.add(`${ref.nodeId}|${ref.field}`);
    }
    return keys;
  }, [items, refs]);

  const reportByKey = useMemo(() => {
    const map = new Map<string, { present: boolean; present_anywhere: boolean }>();
    for (const r of report?.refs ?? []) {
      map.set(`${r.node_id}|${r.field}`, {
        present: r.present,
        present_anywhere: r.present_anywhere,
      });
    }
    return map;
  }, [report]);

  if (!parsed) {
    return (
      <p className="mt-2 text-[11px] text-muted-foreground">
        {t("settings.library.refs.invalidJson")}
      </p>
    );
  }
  if (refs.length === 0) {
    return (
      <p className="mt-2 text-[11px] text-muted-foreground">
        {t("settings.library.refs.noRefs")}
      </p>
    );
  }

  const select = (ref: WorkflowModelRef, filename: string) => {
    const next = applyModelRef(parsed, ref, filename);
    onRewrite(JSON.stringify(next, null, 2));
  };

  const runPreflight = async () => {
    setReport(null);
    const resp = await preflight.mutateAsync(parsed);
    if (resp.ok && resp.data) {
      setReport(resp.data);
    }
  };

  return (
    <div className="mt-2 rounded-md border border-border/60 p-2">
      <div className="flex items-center justify-between gap-2">
        <p className="text-[11px] font-medium text-muted-foreground">
          {t("settings.library.refs.title", { count: refs.length })}
        </p>
        <Button
          type="button"
          size="sm"
          variant="outline"
          className="h-6 gap-1 px-2 text-[10px]"
          disabled={preflight.isPending}
          onClick={() => void runPreflight()}
        >
          {preflight.isPending ? (
            <Loader2 className="size-3 animate-spin" aria-hidden />
          ) : (
            <Stethoscope className="size-3" aria-hidden />
          )}
          {t("settings.library.refs.preflight")}
        </Button>
      </div>

      <ul className="mt-2 space-y-1.5">
        {refs.map((ref) => {
          const key = `${ref.nodeId}|${ref.field}`;
          const verdict = reportByKey.get(key);
          const missing = missingKeys.has(key);
          return (
            <li key={key} className="flex items-center gap-2">
              <span
                className="w-40 shrink-0 truncate font-mono text-[10px] text-muted-foreground"
                title={`${ref.classType}.${ref.field}`}
              >
                {t("settings.library.refs.node", { id: ref.nodeId })} {ref.field}
              </span>
              <div className="min-w-0 flex-1">
                <ModelNamePicker
                  value={ref.filename}
                  expectedTypes={ref.expectedTypes}
                  onChange={(name) => select(ref, name)}
                  ariaLabel={`${ref.classType}.${ref.field}`}
                />
              </div>
              {missing && (
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  className="h-6 shrink-0 gap-1 border-amber-400/40 px-2 text-[10px] text-amber-300 hover:bg-amber-400/10"
                  onClick={() =>
                    requestDownload(
                      filenameToQuery(ref.filename),
                      ref.expectedTypes[0],
                    )
                  }
                >
                  <Download className="size-3" aria-hidden />
                  {t("settings.library.refs.goToDownload")}
                </Button>
              )}
              {verdict &&
                (verdict.present ? (
                  <CircleCheck
                    className="size-3.5 shrink-0 text-emerald-400"
                    aria-label={t("settings.library.refs.present")}
                  />
                ) : (
                  <CircleX
                    className="size-3.5 shrink-0 text-red-400"
                    aria-label={t("settings.library.refs.missing")}
                  />
                ))}
            </li>
          );
        })}
      </ul>

      {report && (
        <p
          className={
            "mt-2 text-[11px] " +
            (report.missing_count > 0 ? "text-red-400" : "text-emerald-400")
          }
        >
          {report.missing_count > 0
            ? t("settings.library.refs.preflightMissing", {
                present: report.total - report.missing_count,
                total: report.total,
                names: report.missing.map((m) => m.filename).join("、"),
              })
            : t("settings.library.refs.preflightOk", { total: report.total })}
        </p>
      )}
    </div>
  );
}
