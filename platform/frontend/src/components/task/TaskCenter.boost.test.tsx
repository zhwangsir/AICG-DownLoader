import { fireEvent, render, screen } from "@testing-library/react";
import { vi } from "vitest";
import TaskCenter from "./TaskCenter";
import { useDramaStore } from "../../store/useDramaStore";
import { useProgress } from "../../hooks/useProgress";

// Mock useProgress（jsdom 无 EventSource）与 cancelPipeline API
vi.mock("../../hooks/useProgress", () => ({
  useProgress: vi.fn(),
}));
vi.mock("../../api/client", () => ({
  cancelPipeline: vi.fn(),
}));

const mockUseProgress = vi.mocked(useProgress);

const idleProgress = {
  connected: false,
  status: null,
  percent: 0,
  message: "",
  result: null,
  error: null,
  reset: vi.fn(),
};

describe("TaskCenter boost — 空任务列表明细补缺", () => {
  beforeEach(() => {
    useDramaStore.getState().reset();
    vi.clearAllMocks();
    mockUseProgress.mockReturnValue(idleProgress);
  });

  it("仅有流登记（tasks 为空）时展开面板显示「暂无任务」", () => {
    useDramaStore.getState().setPipelineStream("px", "http://x/stream");
    render(<TaskCenter />);
    // 无任务 → 触发器呈默认文案，不自动展开
    expect(screen.getByText("任务中心")).toBeInTheDocument();
    expect(screen.queryByTestId("task-center-panel")).not.toBeInTheDocument();
    // 手动展开 → 空态行
    fireEvent.click(screen.getByText("任务中心"));
    expect(screen.getByTestId("task-center-panel")).toBeInTheDocument();
    expect(screen.getByText("暂无任务")).toBeInTheDocument();
  });
});
