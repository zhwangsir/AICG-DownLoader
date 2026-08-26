import { fireEvent, render, screen } from "@testing-library/react";
import { vi } from "vitest";
import AgentBar from "./AgentBar";
import { useDramaStore } from "../../store/useDramaStore";

// TaskCenter 内部依赖 EventSource（jsdom 无），此处仅验证 AgentBar 自身逻辑
vi.mock("../task/TaskCenter", () => ({
  default: () => <div data-testid="task-center-stub" />,
}));

describe("AgentBar", () => {
  beforeEach(() => {
    useDramaStore.getState().reset();
  });

  it("渲染输入框/发送按钮/任务中心触发器", () => {
    render(<AgentBar />);
    expect(screen.getByPlaceholderText(/说出你的创意/)).toBeInTheDocument();
    expect(screen.getByTitle("以该创意新建剧本")).toBeInTheDocument();
    expect(screen.getByTestId("task-center-stub")).toBeInTheDocument();
  });

  it("输入文本后点击发送：写入 draftPremise、打开剧本模态并清空输入", () => {
    render(<AgentBar />);
    const input = screen.getByPlaceholderText(/说出你的创意/);
    fireEvent.change(input, { target: { value: "  霸道总裁爱上我  " } });
    fireEvent.click(screen.getByTitle("以该创意新建剧本"));
    const state = useDramaStore.getState();
    expect(state.draftPremise).toBe("霸道总裁爱上我");
    expect(state.modals.script).toBe(true);
    expect((input as HTMLInputElement).value).toBe("");
  });

  it("回车提交创意（非输入法组合态）", () => {
    render(<AgentBar />);
    const input = screen.getByPlaceholderText(/说出你的创意/);
    fireEvent.change(input, { target: { value: "悬疑短剧" } });
    fireEvent.keyDown(input, { key: "Enter", isComposing: false });
    expect(useDramaStore.getState().draftPremise).toBe("悬疑短剧");
    expect(useDramaStore.getState().modals.script).toBe(true);
  });

  it("输入法组合态（isComposing）回车不提交", () => {
    render(<AgentBar />);
    const input = screen.getByPlaceholderText(/说出你的创意/);
    fireEvent.change(input, { target: { value: "拼音输入中" } });
    fireEvent.keyDown(input, { key: "Enter", isComposing: true });
    expect(useDramaStore.getState().draftPremise).toBe("");
    expect(useDramaStore.getState().modals.script).toBe(false);
    expect((input as HTMLInputElement).value).toBe("拼音输入中");
  });

  it("空白文本：发送按钮禁用，回车不触发提交", () => {
    render(<AgentBar />);
    const input = screen.getByPlaceholderText(/说出你的创意/);
    const sendBtn = screen.getByTitle("以该创意新建剧本");
    expect(sendBtn).toBeDisabled();
    fireEvent.change(input, { target: { value: "   " } });
    expect(sendBtn).toBeDisabled();
    fireEvent.keyDown(input, { key: "Enter" });
    expect(useDramaStore.getState().modals.script).toBe(false);
  });

  it("globalLoading 时输入框与发送均禁用，提交被拦截", () => {
    useDramaStore.getState().startGlobalLoading("生成中");
    render(<AgentBar />);
    const input = screen.getByPlaceholderText(/说出你的创意/);
    expect(input).toBeDisabled();
    expect(screen.getByTitle("以该创意新建剧本")).toBeDisabled();
    // 即便强行回车也不提交
    fireEvent.change(input, { target: { value: "创意" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(useDramaStore.getState().draftPremise).toBe("");
    expect(useDramaStore.getState().modals.script).toBe(false);
  });
});
