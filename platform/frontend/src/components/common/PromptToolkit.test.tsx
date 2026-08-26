import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { vi } from "vitest";
import { useState } from "react";
import { PromptToolkit } from "./PromptToolkit";

// Mock agentAssist（保留模块其余导出真实实现）
vi.mock("../../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api/client")>();
  return { ...actual, agentAssist: vi.fn() };
});

import { agentAssist } from "../../api/client";
const mockAssist = vi.mocked(agentAssist);

/** 受控包装：模拟真实父组件的 text 状态 */
function Harness({ initial = "" }: { initial?: string }) {
  const [text, setText] = useState(initial);
  return (
    <>
      <PromptToolkit text={text} onChange={setText} />
      <output data-testid="current-text">{text}</output>
    </>
  );
}

describe("PromptToolkit", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("渲染风格预设 chips（前 6 个）与 AI 补全按钮", () => {
    render(<Harness />);
    expect(screen.getByText("写实电影感")).toBeInTheDocument();
    expect(screen.getByText("日系动漫")).toBeInTheDocument();
    expect(screen.getByText("油画质感")).toBeInTheDocument();
    // 仅渲染前 6 个，第 7 个起不渲染
    expect(screen.queryByText("水彩插画")).toBeInTheDocument(); // 第 6 个
    expect(screen.queryByText("黑白银盐")).not.toBeInTheDocument(); // 第 7 个
    expect(screen.getByText("AI 补全")).toBeInTheDocument();
  });

  it("点击风格 chip 附加风格词到提示词末尾", () => {
    render(<Harness initial="一个少女站在天台" />);
    fireEvent.click(screen.getByText("赛博朋克"));
    expect(screen.getByTestId("current-text")).toHaveTextContent(
      "一个少女站在天台, 赛博朋克"
    );
  });

  it("已含风格词时不重复注入且 chip 呈 active 态", () => {
    render(<Harness initial="画面, 国风水墨 风格" />);
    const chip = screen.getByText("国风水墨");
    expect(chip.className).toContain("active");
    fireEvent.click(chip);
    expect(screen.getByTestId("current-text")).toHaveTextContent(
      "画面, 国风水墨 风格"
    ); // 未变化
  });

  it("权重语法 (word:1.2) 实时解析为预览 chips", () => {
    render(<Harness initial="masterpiece, (cyberpunk:1.3), (rain)" />);
    const tokens = screen.getByTestId("weight-tokens");
    expect(tokens).toHaveTextContent("cyberpunk ×1.3");
    expect(tokens).toHaveTextContent("rain ×1.1"); // 无权重默认 1.1
  });

  it("无权重语法时不渲染预览区", () => {
    render(<Harness initial="plain prompt" />);
    expect(screen.queryByTestId("weight-tokens")).not.toBeInTheDocument();
  });

  it("AI 补全成功：expand 结果写回提示词", async () => {
    mockAssist.mockResolvedValue({
      success: true,
      data: { text: "补全后的详细提示词, masterpiece, best quality", action: "expand", context: "短剧分镜画面提示词" },
      error: null,
      elapsed_seconds: 1,
    } as Awaited<ReturnType<typeof agentAssist>>);
    render(<Harness initial="少女" />);
    fireEvent.click(screen.getByText("AI 补全"));
    await waitFor(() =>
      expect(screen.getByTestId("current-text")).toHaveTextContent(
        "补全后的详细提示词, masterpiece, best quality"
      )
    );
    expect(mockAssist).toHaveBeenCalledWith(
      expect.objectContaining({ action: "expand", text: "少女" })
    );
  });

  it("AI 补全失败：显示错误且提示词不变", async () => {
    mockAssist.mockResolvedValue({
      success: false,
      data: null,
      error: "LLM 离线",
      elapsed_seconds: 1,
    } as Awaited<ReturnType<typeof agentAssist>>);
    render(<Harness initial="少女" />);
    fireEvent.click(screen.getByText("AI 补全"));
    await waitFor(() => expect(screen.getByText("LLM 离线")).toBeInTheDocument());
    expect(screen.getByTestId("current-text")).toHaveTextContent("少女");
  });

  it("空文本时 AI 补全按钮禁用", () => {
    render(<Harness initial="" />);
    expect(screen.getByText("AI 补全")).toBeDisabled();
  });
});
