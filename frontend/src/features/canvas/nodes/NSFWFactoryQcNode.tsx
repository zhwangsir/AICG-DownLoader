// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
/**
 * R18 制作工厂 · 工序 8 质检预览 —— 对上游合成成片跑 r18-factory/qc：
 * 时长/AV 同步/音轨/字幕 ASR 回读相似度/剧情 LLM 审查，报告卡片 + 成片预览。
 * srt 取工序⑦合成响应回传的真实时间轴 SRT（与烧录进成片的字幕同源）。
 */
import { memo, useCallback, useEffect } from 'react';
import type { NodeProps } from '@xyflow/react';
import { AlertTriangle, CheckCircle2, ClipboardCheck, Flame, Loader2, XCircle } from 'lucide-react';

import {
  CANVAS_NODE_TYPES,
  type NSFWFactoryComposeNodeData,
  type NSFWFactoryQcNodeData,
  type NSFWFactoryStoryboardNodeData,
} from '@/features/canvas/domain/canvasNodes';
import { resolveImageDisplayUrl } from '@/features/canvas/application/imageData';
import { useCanvasStore } from '@/stores/canvasStore';
import { readUrl } from '@/lib/url-params';
import {
  gatewayErrorMessage,
  useR18FactoryQc,
} from '@/lib/queries/model-library';
import {
  NSFWFactoryShell,
  useFactoryUpstream,
} from './nsfwFactoryShared';

type NSFWFactoryQcNodeProps = NodeProps & {
  id: string;
  data: NSFWFactoryQcNodeData;
  selected?: boolean;
};

const NODE_W = 460;
const NODE_H = 460;

function QcMetric({ label, ok }: { label: string; ok: boolean | null }) {
  return (
    <div
      className={`flex items-center gap-1 rounded px-1.5 py-1 ${
        ok === null
          ? 'bg-white/[0.05] text-text-muted'
          : ok
            ? 'bg-emerald-400/10 text-emerald-100/90'
            : 'bg-red-500/10 text-red-100/90'
      }`}
      title={label}
    >
      <span className="shrink-0">{ok === null ? '—' : ok ? '✓' : '✗'}</span>
      <span className="min-w-0 truncate">{label}</span>
    </div>
  );
}

export const NSFWFactoryQcNode = memo(({ id, data, selected }: NSFWFactoryQcNodeProps) => {
  const updateNodeData = useCanvasStore((state) => state.updateNodeData);

  const compose = useFactoryUpstream<NSFWFactoryComposeNodeData>(
    id,
    CANVAS_NODE_TYPES.nsfwFactoryCompose,
  );
  const storyboard = useFactoryUpstream<NSFWFactoryStoryboardNodeData>(
    id,
    CANVAS_NODE_TYPES.nsfwFactoryStoryboard,
  );
  const runQc = useR18FactoryQc();

  const composeUrl = compose?.composeUrl ?? null;
  const rows = storyboard?.rows ?? [];
  const report = data.report ?? null;
  const isRunning = data.isRunning === true;
  const error = data.error ?? null;

  const latest = useCallback((): NSFWFactoryQcNodeData => {
    const node = useCanvasStore.getState().nodes.find((n) => n.id === id);
    return (node?.data as NSFWFactoryQcNodeData | undefined) ?? data;
  }, [data, id]);

  const handleRunQc = useCallback(async () => {
    const d = latest();
    if (d.isRunning) return;
    const projectId = readUrl().project;
    if (!projectId) {
      updateNodeData(id, { error: '缺少项目上下文（project 参数）' });
      return;
    }
    if (!composeUrl) {
      updateNodeData(id, { error: '上游工序⑦尚未合成成片' });
      return;
    }
    // 字幕用工序⑦回传的真实时间轴 SRT（计划时长版会与成片渐漂，导致 ASR 回读误判）
    const composeSrt = (compose?.srt ?? '').trim();
    updateNodeData(id, { isRunning: true, error: null });
    try {
      const result = await runQc.mutateAsync({
        project_id: projectId,
        compose_url: composeUrl,
        ...(composeSrt ? { srt: composeSrt } : {}),
        ...(rows.length > 0
          ? {
              scenes: rows.map((row) => ({
                scene_no: row.shotNo,
                shot_description:
                  [row.actionDesc, row.sceneDesc].filter(Boolean).join('；') ||
                  row.imagePrompt.slice(0, 60),
                dialogue: row.dialogue,
                narration: row.narration,
                duration_sec: row.durationSec,
              })),
            }
          : {}),
        llm_review: true,
      });
      const qcReport = result.ok ? result.data : null;
      if (!qcReport) throw new Error('质检返回为空');
      updateNodeData(id, { report: qcReport, reportUrl: composeUrl, isRunning: false });
    } catch (e) {
      updateNodeData(id, { isRunning: false, error: gatewayErrorMessage(e, '质检失败') });
    }
  }, [compose, composeUrl, id, latest, rows, runQc, updateNodeData]);

  // 挂载时检测中断（刷新恢复）
  useEffect(() => {
    if (data.isRunning === true) {
      updateNodeData(id, { isRunning: false });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const asrPercent =
    report?.asr_similarity != null ? Math.round(report.asr_similarity * 100) : null;

  return (
    <NSFWFactoryShell
      id={id}
      type={CANVAS_NODE_TYPES.nsfwFactoryQc}
      data={data}
      width={NODE_W}
      height={NODE_H}
      stageNo={8}
      stageName="质检预览"
      selected={selected}
    >
      {!composeUrl ? (
        <div className="flex h-full w-full flex-col items-center justify-center gap-2 text-text-muted/55">
          <Flame className="h-8 w-8 text-amber-300/50" aria-hidden />
          <span className="px-6 text-center text-[12px] leading-5">
            工序⑧ 质检预览
            <br />
            连接工序⑦后期合成，成片就绪后开始质检
          </span>
        </div>
      ) : (
        <div className="flex h-full w-full flex-col gap-1.5 overflow-hidden px-3 pb-2 pt-7">
          <button
            type="button"
            disabled={isRunning}
            title="时长 / AV 同步 / 音轨 / 字幕回读 / 剧情 LLM 审查"
            onClick={(event) => {
              event.stopPropagation();
              void handleRunQc();
            }}
            className="nodrag flex h-9 shrink-0 items-center justify-center gap-1.5 rounded-md bg-amber-400/20 text-[12px] font-semibold text-amber-100 transition-colors hover:bg-amber-400/30 disabled:cursor-not-allowed disabled:opacity-45"
          >
            {isRunning ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <ClipboardCheck className="h-3.5 w-3.5" />
            )}
            {report ? '重新质检' : '开始质检'}
          </button>

          {report && (
            <div className="min-h-0 flex-1 space-y-1.5 overflow-y-auto pr-0.5">
              <div
                className={`flex items-center gap-2 rounded-md px-2 py-1.5 ${
                  report.passed
                    ? 'bg-emerald-400/15 text-emerald-100'
                    : 'bg-red-500/15 text-red-100'
                }`}
              >
                {report.passed ? (
                  <CheckCircle2 className="h-4 w-4 shrink-0" />
                ) : (
                  <XCircle className="h-4 w-4 shrink-0" />
                )}
                <span className="text-[13px] font-semibold">
                  {report.passed ? '质检通过' : '质检未通过'}
                </span>
                <span className="ml-auto shrink-0 text-[10px] tabular-nums text-text-muted">
                  {report.duration_sec}s / 预期 {report.expected_duration_sec}s
                </span>
              </div>

              <div className="grid grid-cols-2 gap-1.5">
                <QcMetric label="AV 同步" ok={report.av_sync_ok} />
                <QcMetric label="有音轨" ok={report.has_audio} />
                <QcMetric label="字幕烧录" ok={report.subtitle_ok} />
                <div
                  className={`flex items-center gap-1 rounded px-1.5 py-1 ${
                    asrPercent === null
                      ? 'bg-white/[0.05] text-text-muted'
                      : asrPercent >= 85
                        ? 'bg-emerald-400/10 text-emerald-100/90'
                        : 'bg-red-500/10 text-red-100/90'
                  }`}
                  title="字幕 ASR 回读相似度"
                >
                  <span className="shrink-0">{asrPercent === null ? '—' : `${asrPercent}%`}</span>
                  <span className="min-w-0 truncate">字幕回读</span>
                </div>
              </div>

              {report.llm && (
                <div className="rounded-md border border-white/[0.07] bg-white/[0.035] px-2 py-1.5">
                  <div
                    className={`text-[11px] font-medium ${
                      report.llm.passed ? 'text-emerald-200/90' : 'text-amber-200/90'
                    }`}
                  >
                    剧情审查 {report.llm.passed ? '✓ 通过' : `✗ ${report.llm.issues.length} 项问题`}
                  </div>
                  {report.llm.issues.length > 0 && (
                    <div className="mt-1 space-y-0.5">
                      {report.llm.issues.map((issue, idx) => (
                        <div
                          key={idx}
                          className="text-[10px] leading-4 text-text-muted"
                          title={issue.message}
                        >
                          <span className="shrink-0 rounded bg-white/[0.06] px-1 text-[9px]">
                            {issue.severity}
                            {issue.scene_no != null ? `·S${issue.scene_no}` : ''}
                          </span>{' '}
                          <span className="line-clamp-2">{issue.message}</span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>
          )}

          {composeUrl && (
            <div className="shrink-0 overflow-hidden rounded-md border border-white/[0.1]">
              {/* eslint-disable-next-line jsx-a11y/media-has-caption */}
              <video
                src={composeUrl.startsWith('data:') ? composeUrl : resolveImageDisplayUrl(composeUrl)}
                controls
                preload="metadata"
                className="max-h-40 w-full bg-black"
              />
            </div>
          )}

          {error && (
            <div className="shrink-0 truncate text-[10.5px] text-red-300/85" title={error}>
              <AlertTriangle className="mr-1 inline h-3 w-3" />
              {error}
            </div>
          )}
        </div>
      )}
    </NSFWFactoryShell>
  );
});

NSFWFactoryQcNode.displayName = 'NSFWFactoryQcNode';
