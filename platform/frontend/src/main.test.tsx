import { screen, waitFor } from "@testing-library/react";

/**
 * main.tsx 入口测试：jsdom 下提供 #root 挂载点后动态 import 入口模块，
 * 验证 createRoot(...).render 真实挂载 App（StrictMode 包裹）。
 */
describe("main.tsx 入口", () => {
  it("挂载 App 根组件到 #root", async () => {
    const rootEl = document.createElement("div");
    rootEl.id = "root";
    document.body.appendChild(rootEl);
    await import("./main");
    await waitFor(() =>
      expect(screen.getByText("AIGCPannel")).toBeInTheDocument()
    );
    // 渲染确实发生在注入的 #root 内
    expect(rootEl.querySelector(".app-container")).toBeInTheDocument();
  });
});
