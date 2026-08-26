import { useEffect, useMemo, useRef, useState } from "react";
import {
  CheckCircle2,
  ChevronDown,
  ListTodo,
  Loader2,
  Square,
  X,
  XCircle,
} from "lucide-react";
import { cancelPipeline } from "../../api/client";
import { useProgress } from "../../hooks/useProgress";
import { useDramaStore, type TaskEntry } from "../../store/useDramaStore";

/** 终态任务自动清理延迟（DramaClaw 任务中心：完成即归档，不长期占位） */
const AUTO_CLEAR_MS = 10_000;
/** 面板内最多展示的任务条数（超出滚动） */
const MAX_VISIBLE = 6;

/**
 * DramaClaw 式任务中心：全局长任务统一视图。
 * - 折叠态：AgentBar 右侧 pill，显示运行中任务数/最近消息
 * - 展开态：上弹面板，逐任务进度条/消息/取消（pipeline 可取消）/手动清除
 * - pipeline SSE 由本组件统一订阅（PipelineStreamWatcher），
 *   模态关闭后任务进度仍全局可见（「后台运行」核心场景）
 */
export default function TaskCenter() {
  const tasks = useDramaStore((s) => s.tasks);
  const pipelineStreams = useDramaStore((s) => s.pipelineStreams);
  const removeTask = useDramaStore((s) => s.removeTask);
  const clearFinishedTasks = useDramaStore((s) => s.clearFinishedTasks);

  const [expanded, setExpanded] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);

  const runningCount = useMemo(
    () => tasks.filter((t) => t.status === "running").length,
    [tasks]
  );
  const latest = useMemo(
    () => [...tasks].sort((a, b) => b.startedAt - a.startedAt)[0] ?? null,
    [tasks]
  );
  const finishedCount = tasks.length - runningCount;

  // 有运行中任务时自动展开；全部结束后折回
  useEffect(() => {
    if (runningCount > 0) setExpanded(true);
  }, [runningCount]);

  // 点外关闭
  useEffect(() => {
    if (!expanded) return;
    const onPointerDown = (e: MouseEvent) => {
      if (!wrapRef.current?.contains(e.target as globalThis.Node)) {
        setExpanded(false);
      }
    };
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") setExpanded(false);
    };
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [expanded]);

  // 终态任务自动清理
  useEffect(() => {
    const finished = tasks.filter((t) => t.status !== "running");
    if (finished.length === 0) return;
    const timer = setTimeout(() => {
      finished.forEach((t) => removeTask(t.id));
    }, AUTO_CLEAR_MS);
    return () => clearTimeout(timer);
  }, [tasks, removeTask]);

  // 无任务且无流时不渲染（保持 AgentBar 干净）
  if (tasks.length === 0 && Object.keys(pipelineStreams).length === 0) {
    return null;
  }

  return (
    <div className="task-center" ref={wrapRef} data-testid="task-center">
      {/* pipeline SSE 订阅器：不可见，进度写回 store */}
      {Object.entries(pipelineStreams).map(([taskId, streamUrl]) => (
        <PipelineStreamWatcher key={taskId} taskId={taskId} streamUrl={streamUrl} />
      ))}

      <button
        type="button"
        className={"task-center-trigger" + (runningCount > 0 ? " running" : "")}
        title={expanded ? "收起任务中心" : "展开任务中心"}
        onClick={() => setExpanded(!expanded)}
      >
        {runningCount > 0 ? (
          <Loader2 size={13} className="spin" />
        ) : (
          <ListTodo size={13} />
        )}
        <span className="task-center-trigger-text">
          {runningCount > 0
            ? `${runningCount} 个任务运行中`
            : latest
            ? latest.status === "completed"
              ? "任务全部完成"
              : "任务失败"
            : "任务中心"}
        </span>
        <ChevronDown
          size={12}
          style={{
            transform: expanded ? "rotate(180deg)" : "none",
            transition: "transform 0.2s var(--ease-smooth)",
          }}
        />
      </button>

      {expanded && (
        <div className="task-center-panel" data-testid="task-center-panel">
          <div className="task-center-head">
            <span>任务中心</span>
            {finishedCount > 0 && (
              <button
                type="button"
                className="task-center-clear"
                onClick={clearFinishedTasks}
              >
                清除已结束（{finishedCount}）
              </button>
            )}
          </div>
          <div
            className="task-center-list"
            style={{ maxHeight: MAX_VISIBLE * 64 }}
          >
            {tasks.length === 0 ? (
              <div className="task-center-empty">暂无任务</div>
            ) : (
              [...tasks]
                .sort((a, b) => b.startedAt - a.startedAt)
                .map((t) => <TaskRow key={t.id} task={t} onRemove={removeTask} />)
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function TaskRow({
  task,
  onRemove,
}: {
  task: TaskEntry;
  onRemove: (id: string) => void;
}) {
  const [cancelling, setCancelling] = useState(false);
  const running = task.status === "running";

  const handleCancel = async () => {
    if (task.kind !== "pipeline" || cancelling) return;
    setCancelling(true);
    try {
      await cancelPipeline(task.id);
    } catch {
      // 取消请求失败不阻断；任务状态由 SSE 终态回写
    } finally {
      setCancelling(false);
    }
  };

  return (
    <div className={"task-entry" + (running ? " running" : "")} data-testid={`task-${task.id}`}>
      <div className="task-entry-icon">
        {task.status === "completed" ? (
          <CheckCircle2 size={14} className="task-ok" />
        ) : task.status === "failed" ? (
          <XCircle size={14} className="task-fail" />
        ) : (
          <Loader2 size={14} className="spin task-run" />
        )}
      </div>
      <div className="task-entry-body">
        <div className="task-entry-title">
          <span className="task-entry-label">{task.label}</span>
          <span className="task-entry-percent">{Math.round(task.percent)}%</span>
        </div>
        <div className="task-entry-track">
          <div
            className={
              "task-entry-fill" +
              (task.status === "failed" ? " failed" : "") +
              (task.status === "completed" ? " completed" : "")
            }
            style={{ width: `${Math.max(2, Math.min(100, task.percent))}%` }}
          />
        </div>
        <div className="task-entry-msg" title={task.error || task.message}>
          {task.error || task.message || "等待中…"}
        </div>
      </div>
      {running && task.kind === "pipeline" && (
        <button
          type="button"
          className="task-entry-action"
          title="取消任务"
          disabled={cancelling}
          onClick={handleCancel}
        >
          <Square size={11} />
        </button>
      )}
      {!running && (
        <button
          type="button"
          className="task-entry-action"
          title="移除"
          onClick={() => onRemove(task.id)}
        >
          <X size={12} />
        </button>
      )}
    </div>
  );
}

/** pipeline SSE 订阅器：把流进度写回 store.tasks，终态后注销流 */
function PipelineStreamWatcher({
  taskId,
  streamUrl,
}: {
  taskId: string;
  streamUrl: string;
}) {
  const progress = useProgress(streamUrl);
  const patchTask = useDramaStore((s) => s.patchTask);
  const setPipelineStream = useDramaStore((s) => s.setPipelineStream);

  useEffect(() => {
    const status = progress.status;
    if (!status) return;
    const terminal = status === "completed" || status === "failed";
    patchTask(taskId, {
      // pending 视为 running（任务已创建未开跑）
      status: terminal ? (status as "completed" | "failed") : "running",
      percent: progress.percent,
      message: progress.message,
      error: progress.error ?? undefined,
    });
    if (terminal) {
      setPipelineStream(taskId, null);
    }
  }, [
    progress.status,
    progress.percent,
    progress.message,
    progress.error,
    taskId,
    patchTask,
    setPipelineStream,
  ]);

  return null;
}
