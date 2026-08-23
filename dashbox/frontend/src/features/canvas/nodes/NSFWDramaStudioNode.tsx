// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
/**
 * R18 短剧工厂 —— 单节点全流程：梗概 → LLM 分镜（可暂停改词）→ 批量首帧
 * （IPAdapter 锚定）→ TTS 情感配音（emotion 指令）→ 逐镜头视频 → 成片合成
 * （concat + 分层混音 + 字幕烧录）。
 *
 * 五阶段状态机持久化在 node data：刷新页面后产物（scenes/frameUrls/
 * shotOutputs/composeUrl）仍在，显示「继续拍摄」从第一个未完成镜头接续。
 * 复用既有 hooks：r18-script/plan、generate-image、r18-tts、generate-video、
 * r18-compose（纯前端编排，页面需保持打开）。
 */
import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Handle,
  Position,
  useUpdateNodeInternals,
  type NodeProps,
} from '@xyflow/react';
import {
  AlertTriangle,
  Clapperboard,
  Flame,
  Loader2,
  Play,
  RotateCcw,
  ShieldAlert,
  Upload,
} from 'lucide-react';

import {
  CANVAS_NODE_TYPES,
  type NSFWDramaStudioNodeData,
} from '@/features/canvas/domain/canvasNodes';
import { resolveNodeDisplayName } from '@/features/canvas/domain/nodeDisplay';
import { resolveImageDisplayUrl } from '@/features/canvas/application/imageData';
import { NodeHeader, NODE_HEADER_FLOATING_POSITION_CLASS } from '@/features/canvas/ui/NodeHeader';
import {
  CANVAS_NODE_INPUT_BODY_FRAME_CLASS,
  CANVAS_NODE_INPUT_SURFACE_CLASS,
  CANVAS_NODE_OPS_PANEL_CLASS,
  canvasNodeFrameClass,
} from '@/features/canvas/ui/nodeFrameStyles';
import {
  NODE_GENERATE_BUTTON_BASE_CLASS,
  NODE_GENERATE_BUTTON_DISABLED_CLASS,
  NODE_GENERATE_BUTTON_ENABLED_CLASS,
  NODE_TEXT_CONTROL_TRIGGER_CLASS,
} from '@/features/canvas/ui/nodeControlStyles';
import { useCanvasStore } from '@/stores/canvasStore';
import { readUrl } from '@/lib/url-params';
import {
  gatewayErrorMessage,
  R18_TTS_VOICE_OPTIONS,
  useGenerateImage,
  useGenerateVideo,
  useNsfwStatus,
  useR18Compose,
  useR18ScriptPlan,
  useR18Tts,
  type R18SceneData,
} from '@/lib/queries/model-library';
import { parseCharactersText } from '@/features/canvas/nodes/NSFWScriptNode';
import {
  shotLengthFrames,
  shotVideoSize,
} from '@/features/canvas/nodes/NSFWVideoBatchNode';
import { ModelNamePicker } from '@/components/settings/model-name-picker';
import { uploadFreezoneImage } from '@/api/ops';
import { useUpstreamImages } from '@/features/canvas/application/useUpstreamGraph';

type NSFWDramaStudioNodeProps = NodeProps & {
  id: string;
  data: NSFWDramaStudioNodeData;
  selected?: boolean;
};

const DEFAULT_WIDTH = 480;
const DEFAULT_HEIGHT = 480;
const PANEL_HEIGHT = 330;
const PANEL_GAP = 12;
const PLOT_PRESET = 'h3-clean';
const FRAMES_CONCURRENCY = 3;

const DURATION_OPTIONS = [60, 90, 120, 180];
const SIZE_PRESETS: ReadonlyArray<{ value: string; label: string }> = [
  { value: '832x1216', label: '竖 2:3' },
  { value: '1216x832', label: '横 3:2' },
  { value: '1024x1024', label: '方 1:1' },
];

function toAbsoluteUrl(url: string): string {
  if (/^https?:\/\//i.test(url) || url.startsWith('data:')) return url;
  return `${window.location.origin}${url.startsWith('/') ? '' : '/'}${url}`;
}

export const NSFWDramaStudioNode = memo(({ id, data, selected }: NSFWDramaStudioNodeProps) => {
  const updateNodeInternals = useUpdateNodeInternals();
  const updateNodeData = useCanvasStore((state) => state.updateNodeData);
  const setSelectedNode = useCanvasStore((state) => state.setSelectedNode);

  const { data: nsfwStatusData, isLoading: nsfwLoading } = useNsfwStatus();
  const nsfwEnabled = nsfwStatusData?.data?.nsfw_enabled === true;

  const planScript = useR18ScriptPlan();
  const generateImage = useGenerateImage();
  const synthesizeTts = useR18Tts();
  const generateVideo = useGenerateVideo();
  const composeFinal = useR18Compose();
  const upstreamImages = useUpstreamImages(id);

  const resolvedTitle = useMemo(
    () => resolveNodeDisplayName(CANVAS_NODE_TYPES.nsfwDramaStudio, data),
    [data],
  );

  // ── 输入 ──
  const synopsis = data.synopsis ?? '';
  const charactersText = data.charactersText ?? '';
  const styleHint = data.styleHint ?? '';
  const durationSec = data.durationSec ?? 90;
  const aspect = data.aspect ?? '9:16';
  const checkpoint = data.checkpoint ?? '';
  const size = data.size ?? '832x1216';
  const voice = data.voice || R18_TTS_VOICE_OPTIONS[0].value;
  const autoConfirm = data.autoConfirm === true;
  const anchorUploadUrl = data.anchorUploadUrl ?? null;
  const anchorImageUrl = anchorUploadUrl ?? upstreamImages[0] ?? null;

  // ── 阶段产物 ──
  const scenes = Array.isArray(data.scenes) ? data.scenes : [];
  const frameUrls = data.frameUrls ?? {};
  const shotOutputs = data.shotOutputs ?? {};
  const composeUrl = data.composeUrl ?? null;
  const pipeline = data.pipeline ?? 'idle';
  const error = data.error ?? null;

  const [isUploading, setIsUploading] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const runningRef = useRef(false);

  useEffect(() => {
    updateNodeInternals(id);
  }, [id, updateNodeInternals]);

  /** 从 store 读最新 data（长流水线防闭包过期）。 */
  const latest = useCallback((): NSFWDramaStudioNodeData => {
    const node = useCanvasStore.getState().nodes.find((n) => n.id === id);
    return (node?.data as NSFWDramaStudioNodeData | undefined) ?? data;
  }, [data, id]);

  // ── 阶段执行器 ──
  const stagePlan = useCallback(async () => {
    const d = latest();
    const result = await planScript.mutateAsync({
      synopsis: d.synopsis.trim(),
      characters: parseCharactersText(d.charactersText),
      ...(d.styleHint.trim() ? { style_hint: d.styleHint.trim() } : {}),
      duration_sec: d.durationSec,
      aspect: d.aspect,
    });
    const plan = result.ok ? result.data : null;
    if (!plan || !plan.scenes?.length) throw new Error('分镜规划返回为空');
    updateNodeData(id, {
      planTitle: plan.title ?? '',
      scenes: plan.scenes,
      frameUrls: {},
      shotOutputs: {},
      composeUrl: null,
      pipeline: d.autoConfirm === true ? 'frames' : 'await_confirm',
    });
  }, [id, latest, planScript, updateNodeData]);

  const stageFrames = useCallback(async () => {
    const d = latest();
    const projectId = readUrl().project;
    if (!projectId) throw new Error('缺少项目上下文（project 参数）');
    if (!d.checkpoint.trim()) throw new Error('先在面板选择底模');
    const pending = d.scenes.filter((s) => !d.frameUrls[s.scene_no]);
    const queue = [...pending];
    const worker = async () => {
      for (;;) {
        const scene = queue.shift();
        if (!scene) return;
        const result = await generateImage.mutateAsync({
          prompt: scene.image_prompt,
          checkpoint: d.checkpoint.trim(),
          size: d.size,
          project_id: projectId,
          ...(anchorImageUrl ? { reference_url: toAbsoluteUrl(anchorImageUrl) } : {}),
        });
        const url = result.ok ? (result.data.url ?? '') : '';
        if (!url) throw new Error(`镜头 S${scene.scene_no} 首帧生成返回为空`);
        const cur = latest();
        updateNodeData(id, {
          frameUrls: { ...cur.frameUrls, [scene.scene_no]: url },
        });
      }
    };
    await Promise.all(
      Array.from({ length: Math.min(FRAMES_CONCURRENCY, queue.length) }, worker),
    );
    updateNodeData(id, { pipeline: 'shots' });
  }, [anchorImageUrl, generateImage, id, latest, updateNodeData]);

  const stageShots = useCallback(async () => {
    const d = latest();
    const projectId = readUrl().project;
    if (!projectId) throw new Error('缺少项目上下文（project 参数）');
    // 1) TTS（audio=tts 且有词且未生成）
    for (const scene of d.scenes) {
      const text = (scene.dialogue || scene.narration || '').trim();
      if (scene.audio !== 'tts' || !text) continue;
      if (d.shotOutputs[scene.scene_no]?.audioUrl) continue;
      const result = await synthesizeTts.mutateAsync({
        text,
        voice: d.voice,
        // 情感指令：LLM 规划的 emotion → CosyVoice instruct2（后端映射
        // 「温柔」→「请用温柔缠绵、语速稍缓的语气说」丰富指令）
        emotion: scene.emotion ?? '',
        // 旁白（无角色对白）自动 1.05 语速，对白 1.0
        source: scene.dialogue?.trim() ? 'dialogue' : 'narration',
        project_id: projectId,
      });
      const url = result.ok ? (result.data.url ?? '') : '';
      if (!url) throw new Error(`镜头 S${scene.scene_no} 配音失败`);
      const cur = latest();
      updateNodeData(id, {
        shotOutputs: {
          ...cur.shotOutputs,
          [scene.scene_no]: { videoUrl: cur.shotOutputs[scene.scene_no]?.videoUrl ?? '', audioUrl: url },
        },
      });
    }
    // 2) 视频（顺序逐镜头）
    for (const scene of d.scenes) {
      const frameUrl = d.frameUrls[scene.scene_no];
      if (!frameUrl) continue; // 缺首帧镜头跳过（不阻断）
      if (d.shotOutputs[scene.scene_no]?.videoUrl) continue;
      const presetId = scene.kind === 'action' && scene.preset_id ? scene.preset_id : PLOT_PRESET;
      const route: 'wan' | 'h3' = presetId.startsWith('wan22') ? 'wan' : 'h3';
      const { width, height } = shotVideoSize(d.size, route);
      const prompt =
        scene.video_prompt?.trim() ||
        'subtle motion, gentle breathing, slow camera push in, cinematic';
      const result = await generateVideo.mutateAsync({
        preset_id: presetId,
        prompt,
        first_frame_url: toAbsoluteUrl(frameUrl),
        width,
        height,
        length: shotLengthFrames(scene.duration_sec || 5, route),
        project_id: projectId,
      });
      const url = result.ok ? (result.data.url ?? '') : '';
      if (!url) throw new Error(`镜头 S${scene.scene_no} 视频生成失败`);
      const cur = latest();
      updateNodeData(id, {
        shotOutputs: {
          ...cur.shotOutputs,
          [scene.scene_no]: { videoUrl: url, audioUrl: cur.shotOutputs[scene.scene_no]?.audioUrl ?? null },
        },
      });
    }
    updateNodeData(id, { pipeline: 'composing' });
  }, [generateVideo, id, latest, synthesizeTts, updateNodeData]);

  const stageCompose = useCallback(async () => {
    const d = latest();
    const projectId = readUrl().project;
    if (!projectId) throw new Error('缺少项目上下文（project 参数）');
    const ready = d.scenes
      .map((s) => ({ scene: s, out: d.shotOutputs[s.scene_no] }))
      .filter((x) => x.out?.videoUrl);
    if (ready.length === 0) throw new Error('没有任何已完成的镜头视频，无法合成');
    const result = await composeFinal.mutateAsync({
      project_id: projectId,
      title: d.planTitle || resolvedTitle,
      shots: ready.map(({ scene, out }) => ({
        video_url: out.videoUrl,
        ...(out.audioUrl ? { tts_url: out.audioUrl } : {}),
        audio_mode: scene.audio ?? 'none',
      })),
      // 逐镜头字幕文本：后端按真实视频时长重建 SRT 烧录（计划时长版会与
      // 成片渐漂——H3 出片时长远非整数、xfade 重叠未计入）
      subtitles: ready.map(({ scene }) => (scene.dialogue || scene.narration || '').trim()),
    });
    const out = result.ok ? result.data : null;
    if (!out?.url) throw new Error('合成返回为空');
    updateNodeData(id, {
      composeUrl: out.url,
      composeDurationSec: out.duration_sec ?? null,
      composeSrt: out.srt ?? '',
      pipeline: 'done',
    });
  }, [composeFinal, id, latest, resolvedTitle, updateNodeData]);

  /** 主流水线：从当前阶段推进到底（产物在则跳过）。 */
  const runPipeline = useCallback(
    async (from?: NSFWDramaStudioNodeData['pipeline']) => {
      if (runningRef.current) return;
      runningRef.current = true;
      updateNodeData(id, { error: null, interrupted: false });
      try {
        let stage = from ?? latest().pipeline;
        if (stage === 'idle' || stage === 'error' || stage === 'await_confirm') {
          // idle/error：从剧本开始（产物已有 scenes 时跳过 plan）
          const d = latest();
          if (!d.synopsis?.trim()) throw new Error('先输入剧情梗概');
          if (stage !== 'await_confirm' && (!d.scenes || d.scenes.length === 0)) {
            updateNodeData(id, { pipeline: 'planning' });
            await stagePlan();
            stage = latest().pipeline;
          }
        }
        for (;;) {
          if (stage === 'await_confirm') return; // 暂停等用户确认
          if (stage === 'frames') {
            await stageFrames();
            stage = latest().pipeline;
          } else if (stage === 'shots') {
            await stageShots();
            stage = latest().pipeline;
          } else if (stage === 'composing') {
            await stageCompose();
            return;
          } else if (stage === 'done') {
            return;
          } else if (stage === 'planning') {
            await stagePlan();
            stage = latest().pipeline;
          } else {
            return;
          }
        }
      } catch (e) {
        updateNodeData(id, {
          pipeline: 'error',
          error: gatewayErrorMessage(e, '流水线中断'),
        });
      } finally {
        runningRef.current = false;
      }
    },
    [id, latest, stageCompose, stageFrames, stagePlan, stageShots, updateNodeData],
  );

  // 挂载时检测中断（刷新恢复）：中间态 → 显示继续按钮（不自动跑，避免误触发）
  useEffect(() => {
    const p = data.pipeline ?? 'idle';
    if (['planning', 'frames', 'shots', 'composing'].includes(p) && !runningRef.current) {
      updateNodeData(id, { interrupted: true });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleStart = useCallback(() => {
    const d = latest();
    if (!nsfwEnabled) return;
    if (!d.synopsis?.trim()) {
      updateNodeData(id, { error: '先输入剧情梗概' });
      return;
    }
    if (!d.checkpoint?.trim()) {
      updateNodeData(id, { error: '先在面板选择底模' });
      return;
    }
    // 全新开始：清空旧产物
    updateNodeData(id, {
      planTitle: '',
      scenes: [],
      frameUrls: {},
      shotOutputs: {},
      composeUrl: null,
      composeDurationSec: null,
      pipeline: 'planning',
    });
    void runPipeline('planning');
  }, [id, latest, nsfwEnabled, runPipeline, updateNodeData]);

  const handleResume = useCallback(() => {
    void runPipeline();
  }, [runPipeline]);

  /** 确认剧本后继续（await_confirm → frames）。 */
  const handleConfirmPlan = useCallback(() => {
    updateNodeData(id, { pipeline: 'frames' });
    void runPipeline('frames');
  }, [id, runPipeline, updateNodeData]);

  const handleUploadFile = useCallback(
    async (file: File) => {
      const projectId = readUrl().project;
      if (!projectId) return;
      setIsUploading(true);
      try {
        const uploaded = await uploadFreezoneImage(projectId, file, file.name);
        updateNodeData(id, { anchorUploadUrl: uploaded.url });
      } catch (e) {
        console.error('[nsfw-drama-studio] upload failed', e);
      } finally {
        setIsUploading(false);
      }
    },
    [id, updateNodeData],
  );

  /** 确认阶段可编辑镜头提示词。 */
  const updateSceneField = useCallback(
    (sceneNo: number, field: 'image_prompt' | 'dialogue' | 'emotion', value: string) => {
      const cur = latest();
      updateNodeData(id, {
        scenes: cur.scenes.map((s) =>
          s.scene_no === sceneNo ? { ...s, [field]: value } : s,
        ),
      });
    },
    [id, latest, updateNodeData],
  );

  // ── 派生统计 ──
  const framesDone = scenes.filter((s) => frameUrls[s.scene_no]).length;
  const videoDone = scenes.filter((s) => shotOutputs[s.scene_no]?.videoUrl).length;
  const audioDone = scenes.filter(
    (s) => s.audio !== 'tts' || shotOutputs[s.scene_no]?.audioUrl,
  ).length;
  const isRunning = ['planning', 'frames', 'shots', 'composing'].includes(pipeline) && !data.interrupted;

  const STAGES: ReadonlyArray<{ key: string; label: string; done: boolean; busy: boolean }> = [
    { key: 'plan', label: '剧本', done: scenes.length > 0, busy: pipeline === 'planning' },
    { key: 'frames', label: '首帧', done: scenes.length > 0 && framesDone === scenes.length, busy: pipeline === 'frames' },
    { key: 'tts', label: '配音', done: scenes.length > 0 && audioDone === scenes.length, busy: false },
    { key: 'video', label: '出片', done: scenes.length > 0 && videoDone === scenes.length, busy: pipeline === 'shots' },
    { key: 'compose', label: '合成', done: Boolean(composeUrl), busy: pipeline === 'composing' },
  ];

  // ── R18 未开启：锁定态 ──
  if (!nsfwLoading && !nsfwEnabled) {
    return (
      <div
        className={`relative flex h-full w-full flex-col items-center justify-center gap-2 rounded-[var(--node-radius)] border border-amber-400/30 bg-amber-950/25 ${CANVAS_NODE_INPUT_SURFACE_CLASS}`}
        style={{ width: DEFAULT_WIDTH, height: DEFAULT_HEIGHT }}
        onClick={() => setSelectedNode(id)}
      >
        <Handle type="target" position={Position.Left} id="target" className="!h-2 !w-2 !border-0 !bg-[rgb(148,163,184)]" />
        <Handle type="source" position={Position.Right} id="source" className="!h-2 !w-2 !border-0 !bg-[rgb(148,163,184)]" />
        <NodeHeader
          className={NODE_HEADER_FLOATING_POSITION_CLASS}
          icon={<Flame className="h-4 w-4 text-amber-300/80" />}
          titleText={resolvedTitle}
          editable
          onTitleChange={(nextTitle) => updateNodeData(id, { displayName: nextTitle })}
        />
        <ShieldAlert className="h-8 w-8 text-amber-300/70" aria-hidden />
        <div className="px-6 text-center text-[12px] leading-5 text-amber-100/75">
          R18 内容未开启。请前往「设置 → 模型库」开启 R18 后使用本节点。
        </div>
      </div>
    );
  }

  return (
    <div
      className="group relative h-full w-full overflow-visible"
      style={{ width: DEFAULT_WIDTH, height: DEFAULT_HEIGHT }}
      onClick={() => setSelectedNode(id)}
    >
      <Handle type="target" position={Position.Left} id="target" className="!h-2 !w-2 !border-0 !bg-[rgb(148,163,184)]" />
      <Handle type="source" position={Position.Right} id="source" className="!h-2 !w-2 !border-0 !bg-[rgb(148,163,184)]" />

      <NodeHeader
        className={NODE_HEADER_FLOATING_POSITION_CLASS}
        icon={<Flame className="h-4 w-4 text-amber-300/80" />}
        titleText={data.planTitle || resolvedTitle}
        editable
        onTitleChange={(nextTitle) => updateNodeData(id, { displayName: nextTitle })}
      />

      <div
        className={`relative flex h-full w-full flex-col overflow-hidden rounded-[var(--node-radius)] border transition-colors ${
          CANVAS_NODE_INPUT_SURFACE_CLASS
        } ${canvasNodeFrameClass({ selected })} ${CANVAS_NODE_INPUT_BODY_FRAME_CLASS}`}
      >
        {pipeline === 'idle' && (
          <div className="flex h-full w-full flex-col items-center justify-center gap-2 text-text-muted/55">
            <Clapperboard className="h-9 w-9 text-amber-300/50" aria-hidden />
            <span className="px-8 text-center text-[12px] leading-5">
              R18 短剧工厂
              <br />
              输入梗概与角色卡，一键拍完一部短剧
              <br />
              （剧本 → 首帧 → 配音 → 出片 → 合成）
            </span>
          </div>
        )}

        {pipeline !== 'idle' && (
          <div className="flex h-full w-full flex-col overflow-hidden px-3 pb-2 pt-7">
            {/* 阶段流水行 */}
            <div className="mb-2 flex shrink-0 items-center gap-1">
              {STAGES.map((st, i) => (
                <div key={st.key} className="flex min-w-0 flex-1 items-center gap-1">
                  <div
                    className={`flex h-6 min-w-0 flex-1 items-center justify-center gap-1 rounded-md px-1.5 text-[10.5px] font-medium ${
                      st.busy
                        ? 'bg-amber-400/20 text-amber-100'
                        : st.done
                          ? 'bg-emerald-400/15 text-emerald-100/90'
                          : 'bg-white/[0.05] text-text-muted/60'
                    }`}
                    title={st.label}
                  >
                    {st.busy ? (
                      <Loader2 className="h-2.5 w-2.5 animate-spin" />
                    ) : st.done ? (
                      '✓'
                    ) : (
                      i + 1
                    )}
                    {st.label}
                  </div>
                </div>
              ))}
            </div>

            {/* await_confirm：剧本确认（可编辑） */}
            {pipeline === 'await_confirm' && (
              <div className="flex min-h-0 flex-1 flex-col gap-1.5">
                <div className="shrink-0 text-[11px] text-amber-200/85">
                  剧本已就绪（{scenes.length} 镜头）。选中节点可在下方面板微调每镜头提示词/对白/情绪，或直接继续拍摄。
                </div>
                <div className="min-h-0 flex-1 space-y-1 overflow-y-auto pr-0.5">
                  {scenes.map((scene) => (
                    <div key={scene.scene_no} className="rounded-md border border-white/[0.07] bg-white/[0.035] px-2 py-1">
                      <div className="flex items-center gap-1.5 text-[10.5px] text-text-muted">
                        <span className="tabular-nums">#{scene.scene_no}</span>
                        <span className={`rounded px-1 ${scene.kind === 'action' ? 'bg-rose-500/20 text-rose-200' : scene.kind === 'portrait' ? 'bg-amber-500/20 text-amber-100' : 'bg-slate-500/20 text-slate-200'}`}>
                          {scene.kind === 'action' ? '动作' : scene.kind === 'portrait' ? '定妆' : '剧情'}
                        </span>
                        {scene.preset_id && <span className="truncate rounded bg-rose-500/10 px-1 text-rose-200/80">{scene.preset_id}</span>}
                        <span className="ml-auto tabular-nums">{scene.duration_sec}s · {scene.emotion}</span>
                      </div>
                      <div className="mt-0.5 line-clamp-2 text-[10.5px] text-text-muted/80">{scene.shot_description}</div>
                    </div>
                  ))}
                </div>
                <button
                  type="button"
                  onClick={(event) => {
                    event.stopPropagation();
                    handleConfirmPlan();
                  }}
                  className="nodrag flex h-9 shrink-0 items-center justify-center gap-1.5 rounded-md bg-amber-400/20 text-[12px] font-semibold text-amber-100 transition-colors hover:bg-amber-400/30"
                >
                  <Play className="h-3.5 w-3.5" />
                  确认剧本 · 开始拍摄
                </button>
              </div>
            )}

            {/* 运行中/中断/错误/完成：镜头进度列表 */}
            {['planning', 'frames', 'shots', 'composing', 'done', 'error'].includes(pipeline) && (
              <div className="flex min-h-0 flex-1 flex-col gap-1.5">
                <div className="flex shrink-0 items-center gap-2 text-[11px] text-text-muted">
                  {isRunning && <Loader2 className="h-3 w-3 animate-spin text-amber-300/80" />}
                  <span className="tabular-nums">
                    首帧 {framesDone}/{scenes.length} · 配音 {audioDone}/{scenes.length} · 视频 {videoDone}/{scenes.length}
                  </span>
                </div>
                <div className="min-h-0 flex-1 space-y-1 overflow-y-auto pr-0.5">
                  {scenes.map((scene) => {
                    const frame = frameUrls[scene.scene_no];
                    const out = shotOutputs[scene.scene_no];
                    return (
                      <div key={scene.scene_no} className="flex items-center gap-2 rounded-md border border-white/[0.07] bg-white/[0.035] px-2 py-1">
                        {frame ? (
                          // eslint-disable-next-line @next/next/no-img-element
                          <img src={resolveImageDisplayUrl(frame)} alt="" className="h-9 w-14 shrink-0 rounded border border-white/10 object-cover" draggable={false} />
                        ) : (
                          <div className="flex h-9 w-14 shrink-0 items-center justify-center rounded border border-dashed border-white/12 text-[9px] text-text-muted/50">
                            S{scene.scene_no}
                          </div>
                        )}
                        <div className="min-w-0 flex-1">
                          <div className="flex items-center gap-1 text-[10px] text-text-muted">
                            <span className="tabular-nums">#{scene.scene_no}</span>
                            <span className="min-w-0 flex-1 truncate text-text-dark/80">
                              {scene.title || scene.shot_description}
                            </span>
                          </div>
                          <div className="mt-0.5 flex items-center gap-1.5 text-[9.5px]">
                            <span className={out?.videoUrl ? 'text-emerald-200/80' : 'text-text-muted/50'}>
                              {out?.videoUrl ? '✓视频' : frame ? '待出片' : '待首帧'}
                            </span>
                            {scene.audio === 'tts' && (
                              <span className={out?.audioUrl ? 'text-cyan-200/80' : 'text-text-muted/50'}>
                                {out?.audioUrl ? '✓配音' : '待配音'}
                              </span>
                            )}
                            {scene.audio === 'native' && <span className="text-text-muted/50">原生音画</span>}
                            {scene.emotion && <span className="ml-auto shrink-0 text-amber-200/70">{scene.emotion}</span>}
                          </div>
                        </div>
                      </div>
                    );
                  })}
                </div>

                {/* 中断/错误 → 继续按钮 */}
                {(data.interrupted || pipeline === 'error') && (
                  <button
                    type="button"
                    onClick={(event) => {
                      event.stopPropagation();
                      handleResume();
                    }}
                    className="nodrag flex h-9 shrink-0 items-center justify-center gap-1.5 rounded-md bg-amber-400/20 text-[12px] font-semibold text-amber-100 transition-colors hover:bg-amber-400/30"
                  >
                    <RotateCcw className="h-3.5 w-3.5" />
                    {pipeline === 'error' ? '重试（从失败处继续）' : '继续拍摄（断点接续）'}
                  </button>
                )}
                {error && (
                  <div className="shrink-0 truncate text-[10.5px] text-red-300/85" title={error}>
                    <AlertTriangle className="mr-1 inline h-3 w-3" />
                    {error}
                  </div>
                )}
              </div>
            )}

            {/* 成片播放器 */}
            {composeUrl && (
              <div className="mt-2 shrink-0 overflow-hidden rounded-md border border-amber-400/25">
                {/* eslint-disable-next-line jsx-a11y/media-has-caption */}
                <video
                  src={composeImageDisplay(composeUrl)}
                  controls
                  preload="metadata"
                  className="max-h-44 w-full bg-black"
                />
                <div className="flex items-center justify-between px-2 py-1 text-[10px] text-text-muted">
                  <span className="truncate">成片 · {data.composeDurationSec ? `${data.composeDurationSec}s` : ''}</span>
                  <a
                    href={composeImageDisplay(composeUrl)}
                    download
                    className="text-amber-200/85 hover:text-amber-100"
                    onClick={(event) => event.stopPropagation()}
                  >
                    下载 mp4
                  </a>
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      {/* ── 输入面板 ── */}
      {selected && nsfwEnabled && (
        <div
          className={`nodrag absolute left-1/2 z-10 flex -translate-x-1/2 flex-col gap-2 rounded-[var(--node-radius)] p-3 ${CANVAS_NODE_OPS_PANEL_CLASS}`}
          style={{ top: `calc(100% + ${PANEL_GAP}px)`, height: PANEL_HEIGHT, width: Math.max(DEFAULT_WIDTH, 560) }}
          onClick={(event) => event.stopPropagation()}
        >
          <div className="flex items-center gap-2">
            <div className="min-w-0 flex-1">
              <ModelNamePicker
                value={checkpoint}
                onChange={(next) => updateNodeData(id, { checkpoint: next })}
                expectedTypes={['checkpoints']}
                ariaLabel="R18 底模"
                getOptionDisabledReason={(entry) =>
                  entry.sdxl_incompatible ? (entry.sdxl_incompatible_reason ?? '不兼容 SDXL 工作流') : null
                }
              />
            </div>
            {SIZE_PRESETS.map((preset) => (
              <button
                key={preset.value}
                type="button"
                title={preset.value}
                onClick={() => updateNodeData(id, { size: preset.value })}
                className={`h-7 shrink-0 rounded-md px-2 text-[11px] transition-colors ${
                  size === preset.value
                    ? 'bg-white/[0.13] text-text-dark ring-1 ring-white/24'
                    : 'bg-white/[0.07] text-text-muted/95 hover:bg-white/[0.11]'
                }`}
              >
                {preset.label}
              </button>
            ))}
          </div>

          <textarea
            value={synopsis}
            onChange={(event) => updateNodeData(id, { synopsis: event.target.value })}
            placeholder="剧情梗概：场景、人物关系、想要的表现方向…（必填）"
            className="min-h-0 w-full flex-1 resize-none border-none bg-transparent px-1 text-sm leading-5 text-text-dark outline-none placeholder:text-text-muted/45"
          />

          <div className="flex gap-2">
            <textarea
              value={charactersText}
              onChange={(event) => updateNodeData(id, { charactersText: event.target.value })}
              placeholder={'角色卡（每行 名字：外貌描述）\n林薇：28岁，黑色长直发，白色衬衫裙'}
              className="h-12 min-w-0 flex-1 resize-none rounded-md border border-white/10 bg-white/[0.05] px-2 py-1 text-[11px] leading-4 text-text-dark outline-none placeholder:text-text-muted/45 focus:border-white/25"
            />
            <div className="flex shrink-0 flex-col gap-1">
              <select
                value={voice}
                disabled={isRunning}
                onChange={(event) => updateNodeData(id, { voice: event.target.value })}
                className="h-7 rounded-md border border-white/10 bg-white/[0.05] px-1.5 text-[10.5px] text-text-dark outline-none focus:border-white/25"
              >
                {R18_TTS_VOICE_OPTIONS.map((opt) => (
                  <option key={opt.value} value={opt.value} className="bg-neutral-800">
                    {opt.label}
                  </option>
                ))}
              </select>
              <div className="flex items-center gap-1">
                {DURATION_OPTIONS.map((v) => (
                  <button
                    key={v}
                    type="button"
                    onClick={() => updateNodeData(id, { durationSec: v })}
                    className={`h-6 rounded px-1.5 text-[10px] transition-colors ${
                      durationSec === v ? 'bg-white/[0.13] text-text-dark' : 'bg-white/[0.07] text-text-muted/95 hover:bg-white/[0.11]'
                    }`}
                  >
                    {v >= 60 ? `${v / 60}min` : `${v}s`}
                  </button>
                ))}
              </div>
            </div>
          </div>

          <div className="flex items-center justify-between gap-2">
            <div className="flex min-w-0 items-center gap-2">
              <button
                type="button"
                disabled={isUploading}
                title="上传定妆照（IPAdapter 锚定全部首帧，角色一致）"
                onClick={(event) => {
                  event.stopPropagation();
                  fileInputRef.current?.click();
                }}
                className={NODE_TEXT_CONTROL_TRIGGER_CLASS}
              >
                {isUploading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Upload className="h-3.5 w-3.5" />}
                <span>{anchorUploadUrl ? '换定妆照' : '定妆照'}</span>
              </button>
              {anchorImageUrl && (
                // eslint-disable-next-line @next/next/no-img-element
                <img src={resolveImageDisplayUrl(anchorImageUrl)} alt="" className="h-7 w-7 rounded border border-white/15 object-cover" draggable={false} />
              )}
              <label className="flex shrink-0 cursor-pointer items-center gap-1 text-[10.5px] text-text-muted/85" title="剧本生成后不停顿，直接连续拍完">
                <input
                  type="checkbox"
                  checked={autoConfirm}
                  onChange={(event) => updateNodeData(id, { autoConfirm: event.target.checked })}
                  className="h-3 w-3 accent-amber-400"
                />
                全自动不停顿
              </label>
            </div>
            <button
              type="button"
              disabled={isRunning}
              title={synopsis.trim() ? '一键开拍（全流程）' : '先输入剧情梗概'}
              onClick={(event) => {
                event.stopPropagation();
                handleStart();
              }}
              className={`${NODE_GENERATE_BUTTON_BASE_CLASS} ${
                isRunning ? NODE_GENERATE_BUTTON_DISABLED_CLASS : NODE_GENERATE_BUTTON_ENABLED_CLASS
              }`}
            >
              {isRunning ? <Loader2 className="h-4 w-4 animate-spin" /> : <Clapperboard className="h-4 w-4" />}
            </button>
          </div>
        </div>
      )}

      <input
        ref={fileInputRef}
        type="file"
        accept="image/*"
        className="hidden"
        onChange={(event) => {
          const file = event.target.files?.[0];
          event.target.value = '';
          if (file) void handleUploadFile(file);
        }}
      />
    </div>
  );
});

function composeImageDisplay(url: string): string {
  return url.startsWith('data:') ? url : resolveImageDisplayUrl(url);
}

NSFWDramaStudioNode.displayName = 'NSFWDramaStudioNode';
