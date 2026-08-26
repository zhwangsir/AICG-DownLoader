import { render, screen } from "@testing-library/react";
import { ProgressBar } from "./ProgressBar";

const base = {
  connected: true,
  status: "running",
  percent: 40,
  message: "分镜生成中",
  error: null as string | null,
};

describe("ProgressBar", () => {
  it("status 为空时不渲染任何内容", () => {
    const { container } = render(<ProgressBar {...base} status={null as unknown as string} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("运行中：显示连接态/消息/百分比，进度条无特殊态类名", () => {
    const { container } = render(<ProgressBar {...base} />);
    expect(screen.getByText(/● 已连接/)).toBeInTheDocument();
    expect(screen.getByText(/分镜生成中/)).toBeInTheDocument();
    expect(screen.getByText("40%")).toBeInTheDocument();
    const fill = container.querySelector(".progress-bar-fill") as HTMLElement;
    expect(fill.style.width).toBe("40%");
    expect(fill.className).toBe("progress-bar-fill");
    expect(container.querySelector(".progress-bar-error")).not.toBeInTheDocument();
  });

  it("未连接时显示「连接中」", () => {
    render(<ProgressBar {...base} connected={false} />);
    expect(screen.getByText(/○ 连接中/)).toBeInTheDocument();
  });

  it("message 为空时回退显示 status 文本", () => {
    render(<ProgressBar {...base} message="" />);
    expect(screen.getByText(/· running/)).toBeInTheDocument();
  });

  it("失败态：进度条加 failed 类并展示错误信息", () => {
    const { container } = render(
      <ProgressBar {...base} status="failed" percent={62} error="GPU 离线" />
    );
    expect(container.querySelector(".progress-bar-fill.failed")).toBeInTheDocument();
    expect(container.querySelector(".progress-bar-fill.completed")).not.toBeInTheDocument();
    expect(screen.getByText("GPU 离线")).toBeInTheDocument();
  });

  it("完成态：进度条加 completed 类", () => {
    const { container } = render(
      <ProgressBar {...base} status="completed" percent={100} message="完成" />
    );
    expect(container.querySelector(".progress-bar-fill.completed")).toBeInTheDocument();
    expect(screen.getByText("100%")).toBeInTheDocument();
  });

  it("边界百分比：0% 与超长错误文本正常渲染", () => {
    const longError = "错误".repeat(200);
    const { container } = render(
      <ProgressBar {...base} percent={0} error={longError} />
    );
    const fill = container.querySelector(".progress-bar-fill") as HTMLElement;
    expect(fill.style.width).toBe("0%");
    expect(screen.getByText(longError)).toBeInTheDocument();
  });
});
