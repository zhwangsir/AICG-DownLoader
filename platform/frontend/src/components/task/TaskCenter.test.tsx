import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { vi } from "vitest";
import TaskCenter from "./TaskCenter";
import { useDramaStore, type TaskEntry } from "../../store/useDramaStore";
import { useProgress } from "../../hooks/useProgress";
import { cancelPipeline } from "../../api/client";

// Mock useProgress（jsdom 无 EventSource）与 cancelPipeline API
vi.mock("../../hooks/useProgress", () => ({
  useProgress: vi.fn(),
}));
vi.mock("../../api/client", () => ({
  cancelPipeline: vi.fn(),
}));

const mockUseProgress = vi.mocked(useProgress);
const mockCancelPipeline = vi.mocked(cancelPipeline);

const idleProgress = {
  connected: false,
  status: null,
  percent: 0,
  message: "",
  result: null,
  error: null,
  reset: vi.fn(),
};

function makeTask(patch: Partial<TaskEntry> = {}): TaskEntry {
  return {
    id: "t1",
    label: "一键成片：测试",
    kind: "pipeline",
    status: "running",
    percent: 30,
    message: "分镜生成中",
    startedAt: Date.now(),
    ...patch,
  };
}

describe("TaskCenter（DramaClaw 式任务中心）", () => {
  beforeEach(() => {
    useDramaStore.getState().reset();
    vi.clearAllMocks();
    mockUseProgress.mockReturnValue(idleProgress);
  });

  it("无任务时不渲染", () => {
    const { container } = render(<TaskCenter />);
    expect(container.querySelector(".task-center")).not.toBeInTheDocument();
  });

  it("运行中任务：触发器显示数量并自动展开面板", () => {
    useDramaStore.getState().upsertTask(makeTask());
    render(<TaskCenter />);
    expect(screen.getByText("1 个任务运行中")).toBeInTheDocument();
    // 自动展开
    expect(screen.getByTestId("task-center-panel")).toBeInTheDocument();
    expect(screen.getByText("一键成片：测试")).toBeInTheDocument();
    expect(screen.getByText("分镜生成中")).toBeInTheDocument();
  });

  it("任务条目：进度条宽度与百分比文本", () => {
    useDramaStore.getState().upsertTask(makeTask({ percent: 45 }));
    render(<TaskCenter />);
    expect(screen.getByText("45%")).toBeInTheDocument();
    const fill = document.querySelector(".task-entry-fill") as HTMLElement;
    expect(fill.style.width).toBe("45%");
  });

  it("全部完成：触发器显示「任务全部完成」，进度条绿色态", () => {
    useDramaStore
      .getState()
      .upsertTask(makeTask({ status: "completed", percent: 100, message: "完成" }));
    render(<TaskCenter />);
    expect(screen.getByText("任务全部完成")).toBeInTheDocument();
    // 无运行中任务时不自动展开 → 点击展开后验证绿色进度条
    fireEvent.click(screen.getByText("任务全部完成"));
    expect(document.querySelector(".task-entry-fill.completed")).toBeInTheDocument();
  });

  it("失败任务：触发器显示「任务失败」，错误消息展示", () => {
    useDramaStore
      .getState()
      .upsertTask(makeTask({ status: "failed", percent: 62, error: "GPU 离线" }));
    render(<TaskCenter />);
    expect(screen.getByText("任务失败")).toBeInTheDocument();
    fireEvent.click(screen.getByText("任务失败"));
    expect(screen.getByText("GPU 离线")).toBeInTheDocument();
    expect(document.querySelector(".task-entry-fill.failed")).toBeInTheDocument();
  });

  it("折叠/展开切换；点外与 Escape 关闭面板", () => {
    useDramaStore.getState().upsertTask(makeTask({ status: "completed", percent: 100 }));
    render(<TaskCenter />);
    const trigger = screen.getByText("任务全部完成");
    // 默认不自动展开（无运行中任务）→ 点击展开
    expect(screen.queryByTestId("task-center-panel")).not.toBeInTheDocument();
    fireEvent.click(trigger);
    expect(screen.getByTestId("task-center-panel")).toBeInTheDocument();
    // Escape 关闭
    fireEvent.keyDown(document.body, { key: "Escape" });
    expect(screen.queryByTestId("task-center-panel")).not.toBeInTheDocument();
    // 再展开 → 点外关闭
    fireEvent.click(trigger);
    expect(screen.getByTestId("task-center-panel")).toBeInTheDocument();
    fireEvent.mouseDown(document.body);
    expect(screen.queryByTestId("task-center-panel")).not.toBeInTheDocument();
  });

  it("pipeline 运行中任务显示取消按钮，点击调用 cancelPipeline", async () => {
    mockCancelPipeline.mockResolvedValue({ success: true, data: { cancel_requested: true } });
    useDramaStore.getState().upsertTask(makeTask({ id: "pipe-1" }));
    render(<TaskCenter />);
    const cancelBtn = screen.getByTitle("取消任务");
    fireEvent.click(cancelBtn);
    await waitFor(() => expect(mockCancelPipeline).toHaveBeenCalledWith("pipe-1"));
  });

  it("非 pipeline 任务不显示取消按钮；终态任务显示移除按钮", () => {
    useDramaStore
      .getState()
      .upsertTask(makeTask({ id: "v1", kind: "video", status: "running" }));
    useDramaStore
      .getState()
      .upsertTask(makeTask({ id: "b1", kind: "batch", status: "completed", percent: 100, startedAt: Date.now() + 1 }));
    render(<TaskCenter />);
    expect(screen.queryByTitle("取消任务")).not.toBeInTheDocument();
    const removeBtn = screen.getByTitle("移除");
    fireEvent.click(removeBtn);
    expect(useDramaStore.getState().tasks.find((t) => t.id === "b1")).toBeUndefined();
  });

  it("「清除已结束」按钮移除全部终态任务", () => {
    const store = useDramaStore.getState();
    store.upsertTask(makeTask({ id: "r1", status: "running" }));
    store.upsertTask(makeTask({ id: "c1", status: "completed", percent: 100, startedAt: Date.now() + 1 }));
    store.upsertTask(makeTask({ id: "f1", status: "failed", percent: 70, startedAt: Date.now() + 2 }));
    render(<TaskCenter />);
    fireEvent.click(screen.getByText(/清除已结束/));
    const ids = useDramaStore.getState().tasks.map((t) => t.id);
    expect(ids).toEqual(["r1"]);
  });

  it("终态任务 10s 后自动清理", async () => {
    vi.useFakeTimers();
    try {
      useDramaStore
        .getState()
        .upsertTask(makeTask({ status: "completed", percent: 100 }));
      render(<TaskCenter />);
      expect(useDramaStore.getState().tasks.length).toBe(1);
      await vi.advanceTimersByTimeAsync(10_500);
      expect(useDramaStore.getState().tasks.length).toBe(0);
    } finally {
      vi.useRealTimers();
    }
  });

  it("pipelineStreams 有流时即使 tasks 为空也渲染（watcher 挂载）", () => {
    useDramaStore.getState().setPipelineStream("p1", "http://x/stream");
    render(<TaskCenter />);
    expect(screen.getByTestId("task-center")).toBeInTheDocument();
  });

  it("watcher：SSE 进度写回 store.tasks", () => {
    mockUseProgress.mockReturnValue({
      ...idleProgress,
      status: "running",
      percent: 55,
      message: "视频生成中",
    });
    useDramaStore.getState().upsertTask(makeTask({ id: "p2", percent: 0 }));
    useDramaStore.getState().setPipelineStream("p2", "http://x/stream");
    render(<TaskCenter />);
    const t = useDramaStore.getState().tasks.find((x) => x.id === "p2");
    expect(t?.percent).toBe(55);
    expect(t?.message).toBe("视频生成中");
    expect(t?.status).toBe("running");
  });

  it("watcher：pending 状态映射为 running", () => {
    mockUseProgress.mockReturnValue({
      ...idleProgress,
      status: "pending",
      percent: 0,
      message: "排队中",
    });
    useDramaStore.getState().upsertTask(makeTask({ id: "p3" }));
    useDramaStore.getState().setPipelineStream("p3", "http://x/stream");
    render(<TaskCenter />);
    expect(useDramaStore.getState().tasks.find((x) => x.id === "p3")?.status).toBe("running");
  });

  it("watcher：终态回写后注销 pipelineStream", () => {
    mockUseProgress.mockReturnValue({
      ...idleProgress,
      status: "completed",
      percent: 100,
      message: "完成",
    });
    useDramaStore.getState().upsertTask(makeTask({ id: "p4" }));
    useDramaStore.getState().setPipelineStream("p4", "http://x/stream");
    render(<TaskCenter />);
    const state = useDramaStore.getState();
    expect(state.tasks.find((x) => x.id === "p4")?.status).toBe("completed");
    expect(state.pipelineStreams["p4"]).toBeUndefined();
  });
});
