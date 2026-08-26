import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { vi } from "vitest";
import { PipelineModal } from "./PipelineModal";
import {
  runPipeline,
  cancelPipeline,
  type PipelineReport,
  type ScriptData,
} from "../../api/client";
import { useProgress } from "../../hooks/useProgress";
import { useDramaStore } from "../../store/useDramaStore";

// Mock 后端调用；useProgress 由 jsdom 无 EventSource 需整体 mock
vi.mock("../../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api/client")>();
  return {
    ...actual,
    runPipeline: vi.fn(),
    cancelPipeline: vi.fn(),
  };
});
vi.mock("../../hooks/useProgress", () => ({
  useProgress: vi.fn(),
}));

const mockRunPipeline = vi.mocked(runPipeline);
const mockCancelPipeline = vi.mocked(cancelPipeline);
const mockUseProgress = vi.mocked(useProgress);

function makeProgress(patch: Record<string, unknown> = {}) {
  return {
    connected: false,
    status: null,
    percent: 0,
    message: "",
    result: null,
    error: null,
    reset: vi.fn(),
    ...patch,
  } as ReturnType<typeof useProgress>;
}

const scriptData: ScriptData = {
  project_id: "p-123",
  title: "外卖惊魂",
  genre: "都市悬疑",
  aspect_ratio: "9:16",
  total_episodes: 1,
  characters: [
    {
      character_id: "c1",
      name: "林雪",
      role: "女主",
      age: 25,
      description: "冷静",
      personality: "内敛",
    },
  ],
  scenes: [],
};

function makeReport(patch: Partial<PipelineReport> = {}): PipelineReport {
  return {
    project_id: "p-123",
    premise: "都市悬疑",
    started_at: 1723800000,
    steps: {
      script: { data: scriptData },
      character: { skipped: true },
      storyboard: {},
      video: {},
      edit: { final_video_url: "/static/final.mp4" },
    },
    passed: true,
    total_elapsed_seconds: 12.34,
    ...patch,
  };
}

/** 走通「开始生成」→ 进入任务视图（运行中） */
async function startTask(onClose: () => void = vi.fn()) {
  mockRunPipeline.mockResolvedValue({
    task_id: "task-1",
    stream_url: "http://localhost:8123/api/tasks/task-1/stream",
  });
  const utils = render(<PipelineModal onClose={onClose} />);
  fireEvent.click(screen.getByText(/开始生成/));
  await screen.findByText(/执行中…/);
  return { ...utils, onClose };
}

describe("PipelineModal（一键全链路成片）", () => {
  beforeEach(() => {
    useDramaStore.getState().reset();
    vi.clearAllMocks();
    mockUseProgress.mockReturnValue(makeProgress());
  });

  it("初始渲染表单：默认创意/题材/画风/开关，AI 标识开启时显示备案号", () => {
    render(<PipelineModal onClose={vi.fn()} />);
    expect(screen.getByText("一键全链路成片")).toBeInTheDocument();
    const premise = screen.getByPlaceholderText(
      /一句话创意/
    ) as HTMLTextAreaElement;
    expect(premise.value).toContain("外卖员");
    const combos = screen.getAllByRole("combobox") as HTMLSelectElement[];
    expect(combos[0].value).toBe("都市悬疑"); // 题材
    expect(combos[1].value).toBe("写实电影感"); // 画风
    const spins = screen.getAllByRole("spinbutton") as HTMLInputElement[];
    expect(spins[0].value).toBe("1"); // 集数
    expect(spins[1].value).toBe("3"); // 每集分镜数
    // 默认开关：质检开、定妆照关、视觉对照关、AI 标识开
    expect(
      screen.getByRole("checkbox", { name: /生成角色定妆照/ })
    ).not.toBeChecked();
    expect(
      screen.getByRole("checkbox", { name: /成片后执行文本质检/ })
    ).toBeChecked();
    expect(
      screen.getByRole("checkbox", { name: /视觉漂移对照/ })
    ).not.toBeChecked();
    expect(
      screen.getByRole("checkbox", { name: /烧录「AI生成」标识/ })
    ).toBeChecked();
    // 备案号输入框在 aiLabel 开启时可见
    expect(screen.getByPlaceholderText(/京网微剧备字/)).toBeInTheDocument();
  });

  it("遮罩点击关闭（仅断订阅），模态内部点击不冒泡", () => {
    const onClose = vi.fn();
    const { container } = render(<PipelineModal onClose={onClose} />);
    fireEvent.click(container.querySelector(".modal")!);
    expect(onClose).not.toHaveBeenCalled();
    fireEvent.click(container.querySelector(".modal-overlay")!);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("空白创意点击开始生成 → 显示错误且不调后端", () => {
    render(<PipelineModal onClose={vi.fn()} />);
    fireEvent.change(screen.getByPlaceholderText(/一句话创意/), {
      target: { value: "   " },
    });
    fireEvent.click(screen.getByText(/开始生成/));
    expect(screen.getByText("请输入一句话创意")).toBeInTheDocument();
    expect(mockRunPipeline).not.toHaveBeenCalled();
  });

  it("表单交互：题材/画风/变现模式/复选框/备案号均可修改并体现在提交参数", async () => {
    mockRunPipeline.mockResolvedValue({
      task_id: "task-x",
      stream_url: "http://h/s",
    });
    render(<PipelineModal onClose={vi.fn()} />);
    fireEvent.change(screen.getByPlaceholderText(/一句话创意/), {
      target: { value: "  古风甜宠，小厨娘逆袭  " },
    });
    const combos = screen.getAllByRole("combobox");
    fireEvent.change(combos[0], { target: { value: "古风仙侠" } });
    fireEvent.change(combos[1], { target: { value: "日系动漫" } });
    fireEvent.click(screen.getByText("IAP 付费解锁"));
    fireEvent.click(screen.getByRole("checkbox", { name: /生成角色定妆照/ }));
    fireEvent.click(screen.getByRole("checkbox", { name: /视觉漂移对照/ }));
    fireEvent.click(screen.getByRole("checkbox", { name: /成片后执行文本质检/ }));
    fireEvent.change(screen.getByPlaceholderText(/京网微剧备字/), {
      target: { value: " 京备001 " },
    });
    fireEvent.click(screen.getByText(/开始生成/));
    await waitFor(() => expect(mockRunPipeline).toHaveBeenCalled());
    expect(mockRunPipeline).toHaveBeenCalledWith({
      premise: "古风甜宠，小厨娘逆袭",
      genre: "古风仙侠",
      style: "日系动漫",
      episodes: 1,
      scenes_per_episode: 3,
      monetization_mode: "iap",
      generate_character_refs: true,
      run_quality_check: false,
      run_visual_check: true,
      ai_label_enabled: true,
      license_number: "京备001",
    });
  });

  it("变现模式切到 IAP 后再切回 IAA，提交参数为 iaa", async () => {
    mockRunPipeline.mockResolvedValue({
      task_id: "task-iaa",
      stream_url: "http://h/s",
    });
    render(<PipelineModal onClose={vi.fn()} />);
    fireEvent.change(screen.getByPlaceholderText(/一句话创意/), {
      target: { value: "都市逆袭，外卖小哥翻身" },
    });
    const iaaChip = screen.getByText("IAA 免费+广告");
    const iapChip = screen.getByText("IAP 付费解锁");
    // 初始 IAA 激活（fontWeight 600），IAP 未激活（500）
    expect(iaaChip.style.fontWeight).toBe("600");
    expect(iapChip.style.fontWeight).toBe("500");
    fireEvent.click(iapChip);
    expect(iapChip.style.fontWeight).toBe("600");
    expect(iaaChip.style.fontWeight).toBe("500");
    fireEvent.click(iaaChip);
    expect(iaaChip.style.fontWeight).toBe("600");
    expect(iapChip.style.fontWeight).toBe("500");
    fireEvent.click(screen.getByText(/开始生成/));
    await waitFor(() => expect(mockRunPipeline).toHaveBeenCalled());
    // 全字段精确断言（与 iap 用例口径一致），确认切回 IAA 未污染其他表单字段
    expect(mockRunPipeline).toHaveBeenCalledWith({
      premise: "都市逆袭，外卖小哥翻身",
      genre: "都市悬疑",
      style: "写实电影感",
      episodes: 1,
      scenes_per_episode: 3,
      monetization_mode: "iaa",
      generate_character_refs: false,
      run_quality_check: true,
      run_visual_check: false,
      ai_label_enabled: true,
      license_number: "",
    });
    expect(mockCancelPipeline).not.toHaveBeenCalled();
  });

  it("集数/分镜数输入钳制到 1-10，非数字回退 1", () => {
    render(<PipelineModal onClose={vi.fn()} />);
    const [episodes, scenes] = screen.getAllByRole(
      "spinbutton"
    ) as HTMLInputElement[];
    fireEvent.change(episodes, { target: { value: "99" } });
    expect(episodes.value).toBe("10");
    fireEvent.change(episodes, { target: { value: "0" } });
    expect(episodes.value).toBe("1");
    fireEvent.change(scenes, { target: { value: "abc" } });
    expect(scenes.value).toBe("1");
    fireEvent.change(scenes, { target: { value: "7" } });
    expect(scenes.value).toBe("7");
  });

  it("关闭 AI 标识后备案号输入框隐藏", () => {
    render(<PipelineModal onClose={vi.fn()} />);
    fireEvent.click(screen.getByRole("checkbox", { name: /烧录「AI生成」标识/ }));
    expect(screen.queryByPlaceholderText(/京网微剧备字/)).not.toBeInTheDocument();
  });

  it("启动成功：登记全局任务与 SSE 流，切换到任务视图", async () => {
    await startTask();
    const state = useDramaStore.getState();
    expect(state.statusInfo).toContain("task-1");
    const task = state.tasks.find((t) => t.id === "task-1");
    expect(task).toMatchObject({ kind: "pipeline", status: "running" });
    expect(task?.label).toContain("一键成片：");
    expect(state.pipelineStreams["task-1"]).toContain("/api/tasks/task-1/stream");
    expect(screen.getByText("task-1")).toBeInTheDocument();
    // 长创意标签截断 + 省略号分支
    expect(screen.getByText(/后台运行/)).toBeInTheDocument();
  });

  it("超长创意标签截断为 18 字 + 省略号", async () => {
    mockRunPipeline.mockResolvedValue({
      task_id: "task-long",
      stream_url: "http://h/s",
    });
    render(<PipelineModal onClose={vi.fn()} />);
    fireEvent.change(screen.getByPlaceholderText(/一句话创意/), {
      target: { value: "这是一个非常非常长的一句话创意输入超过十八个字需要被截断" },
    });
    fireEvent.click(screen.getByText(/开始生成/));
    await waitFor(() => expect(mockRunPipeline).toHaveBeenCalled());
    const task = useDramaStore.getState().tasks.find((t) => t.id === "task-long");
    expect(task?.label.endsWith("…")).toBe(true);
    expect(task?.label.length).toBe("一键成片：".length + 18 + 1);
  });

  it("启动中按钮禁用并显示加载态", async () => {
    let resolveRun: (v: { task_id: string; stream_url: string }) => void = () => {};
    mockRunPipeline.mockReturnValue(
      new Promise((r) => {
        resolveRun = r;
      })
    );
    render(<PipelineModal onClose={vi.fn()} />);
    fireEvent.click(screen.getByText(/开始生成/));
    const btn = await screen.findByText(/启动中…/);
    expect(btn.closest("button")).toBeDisabled();
    resolveRun({ task_id: "t", stream_url: "http://h/s" });
    await screen.findByText(/执行中…/);
  });

  it("启动失败：异常写入错误区", async () => {
    mockRunPipeline.mockRejectedValue(new Error("后端 500"));
    render(<PipelineModal onClose={vi.fn()} />);
    fireEvent.click(screen.getByText(/开始生成/));
    await screen.findByText(/Error: 后端 500/);
    // 仍在表单视图
    expect(screen.getByText(/开始生成/)).toBeInTheDocument();
  });

  it("运行中取消任务：调用 cancelPipeline 并提示", async () => {
    mockCancelPipeline.mockResolvedValue({
      success: true,
      data: { cancel_requested: true },
    });
    await startTask();
    fireEvent.click(screen.getByText(/取消任务/));
    await waitFor(() =>
      expect(mockCancelPipeline).toHaveBeenCalledWith("task-1")
    );
    expect(useDramaStore.getState().statusInfo).toContain("已请求取消");
  });

  it("取消中显示加载态；取消失败写入错误区", async () => {
    let rejectCancel: (e: Error) => void = () => {};
    mockCancelPipeline.mockReturnValue(
      new Promise((_, rej) => {
        rejectCancel = rej;
      })
    );
    await startTask();
    fireEvent.click(screen.getByText(/取消任务/));
    const cancellingBtn = await screen.findByText(/取消中…/);
    expect(cancellingBtn.closest("button")).toBeDisabled();
    rejectCancel(new Error("取消超时"));
    await screen.findByText(/Error: 取消超时/);
  });

  it("运行中点「后台运行」：提示后台运行、reset 并关闭", async () => {
    const onClose = vi.fn();
    await startTask(onClose);
    const progress = mockUseProgress.mock.results[0].value;
    fireEvent.click(screen.getByText(/后台运行/));
    expect(useDramaStore.getState().statusInfo).toContain("仍在后台运行");
    expect(progress.reset).toHaveBeenCalled();
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("完成态：执行报告渲染步骤芯片（完成✓/跳过/未执行）、总耗时、成片视频", async () => {
    const { rerender } = await startTask();
    mockUseProgress.mockReturnValue(
      makeProgress({
        status: "completed",
        percent: 100,
        message: "完成",
        result: makeReport(),
      })
    );
    rerender(<PipelineModal onClose={vi.fn()} />);
    expect(screen.getByText(/已结束/)).toBeInTheDocument();
    expect(screen.getByText("执行报告")).toBeInTheDocument();
    // 剧本完成✓、角色定妆照跳过、质检未执行无标记
    expect(screen.getByText("剧本 ✓")).toBeInTheDocument();
    expect(screen.getByText("角色定妆照（跳过）")).toBeInTheDocument();
    expect(screen.getByText("质检")).toBeInTheDocument();
    expect(screen.getByText(/总耗时 12.3s/)).toBeInTheDocument();
    const video = document.querySelector("video") as HTMLVideoElement;
    expect(video.src).toContain("/static/final.mp4");
    expect(screen.getByText("打开成片文件")).toBeInTheDocument();
    // project_id 写入 store 供锚点重拍
    await waitFor(() =>
      expect(useDramaStore.getState().pipelineProjectId).toBe("p-123")
    );
  });

  it("完成且 passed：「加载到画布」回填剧本并关闭", async () => {
    const onClose = vi.fn();
    const { rerender } = await startTask(onClose);
    mockUseProgress.mockReturnValue(
      makeProgress({ status: "completed", percent: 100, result: makeReport() })
    );
    rerender(<PipelineModal onClose={onClose} />);
    fireEvent.click(await screen.findByText(/加载到画布/));
    const state = useDramaStore.getState();
    expect(state.scriptData?.title).toBe("外卖惊魂");
    expect(state.statusInfo).toContain("已加载到画布");
    expect(state.statusInfo).toContain("1 角色");
    expect(onClose).toHaveBeenCalled();
  });

  it("报告未通过或无剧本数据：不显示「加载到画布」", async () => {
    const { rerender } = await startTask();
    mockUseProgress.mockReturnValue(
      makeProgress({
        status: "failed",
        percent: 40,
        result: makeReport({ passed: false, error: "分镜生成失败" }),
      })
    );
    rerender(<PipelineModal onClose={vi.fn()} />);
    expect(screen.queryByText(/加载到画布/)).not.toBeInTheDocument();
    expect(screen.getByText("分镜生成失败")).toBeInTheDocument();
    // 失败终态也保存 project_id
    await waitFor(() =>
      expect(useDramaStore.getState().pipelineProjectId).toBe("p-123")
    );
  });

  it("报告缺 edit 成片与总耗时字段：不渲染视频与耗时", async () => {
    const { rerender } = await startTask();
    const report = makeReport({ total_elapsed_seconds: undefined });
    delete report.steps.edit;
    mockUseProgress.mockReturnValue(
      makeProgress({ status: "completed", percent: 100, result: report })
    );
    rerender(<PipelineModal onClose={vi.fn()} />);
    expect(screen.getByText("执行报告")).toBeInTheDocument();
    expect(document.querySelector("video")).not.toBeInTheDocument();
    expect(screen.queryByText(/总耗时/)).not.toBeInTheDocument();
  });

  it("终态但 result 为 null：不渲染执行报告", async () => {
    const { rerender } = await startTask();
    mockUseProgress.mockReturnValue(
      makeProgress({ status: "completed", percent: 100, result: null })
    );
    rerender(<PipelineModal onClose={vi.fn()} />);
    expect(screen.queryByText("执行报告")).not.toBeInTheDocument();
    // 无 canvasScript → 无加载到画布，仅有 关闭/再来一条
    expect(screen.queryByText(/加载到画布/)).not.toBeInTheDocument();
    expect(screen.getByText("关闭")).toBeInTheDocument();
  });

  it("「再来一条」：reset 进度并回到表单", async () => {
    const { rerender } = await startTask();
    mockUseProgress.mockReturnValue(
      makeProgress({ status: "completed", percent: 100, result: makeReport() })
    );
    rerender(<PipelineModal onClose={vi.fn()} />);
    const reset = mockUseProgress.mock.results.at(-1)?.value.reset;
    fireEvent.click(screen.getByText(/再来一条/));
    expect(reset).toHaveBeenCalled();
    expect(screen.getByText(/开始生成/)).toBeInTheDocument();
  });

  it("终态后点「关闭」：不再提示后台运行", async () => {
    const onClose = vi.fn();
    const { rerender } = await startTask(onClose);
    mockUseProgress.mockReturnValue(
      makeProgress({ status: "completed", percent: 100, result: makeReport() })
    );
    rerender(<PipelineModal onClose={onClose} />);
    fireEvent.click(screen.getByText("关闭"));
    expect(useDramaStore.getState().statusInfo).not.toContain("仍在后台运行");
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("卸载不报错（本地无 SSE 订阅需清理）", async () => {
    const { unmount } = await startTask();
    expect(() => unmount()).not.toThrow();
  });
});
