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

describe("PromptToolkit boost — 异常路径补缺", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("AI 补全抛异常（网络层 reject）：catch 分支显示异常字符串且提示词不变", async () => {
    mockAssist.mockRejectedValue(new Error("connection refused"));
    render(<Harness initial="少女" />);
    fireEvent.click(screen.getByText("AI 补全"));
    await waitFor(() =>
      expect(screen.getByText("Error: connection refused")).toBeInTheDocument()
    );
    expect(screen.getByTestId("current-text")).toHaveTextContent("少女");
  });

  it("异常抛出后 assisting 复位：按钮恢复可点击（finally 分支）", async () => {
    mockAssist.mockRejectedValue(new Error("boom"));
    render(<Harness initial="少女" />);
    fireEvent.click(screen.getByText("AI 补全"));
    await waitFor(() =>
      expect(screen.getByText("AI 补全")).not.toBeDisabled()
    );
  });
});
