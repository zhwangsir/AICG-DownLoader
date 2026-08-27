import { fireEvent, render, screen, within } from "@testing-library/react";
import App from "./App";
import { useDramaStore } from "./store/useDramaStore";
import type { ScriptData } from "./api/client";

// 限定查询到 topbar，避免与 Canvas 节点面板的同名按钮冲突
const getTopbar = () => {
  const title = screen.getByText("DashBox");
  return title.closest(".topbar") as HTMLElement;
};
/** 展开"流程"下拉菜单并返回菜单容器（顶栏按钮已收纳进下拉） */
const openFlowMenu = () => {
  fireEvent.click(within(getTopbar()).getByText("操作流程"));
  return getTopbar().querySelector(".dropdown-menu") as HTMLElement;
};

const sampleScript: ScriptData = {
  project_id: "p1",
  title: "测试短剧",
  genre: "都市悬疑",
  aspect_ratio: "9:16",
  total_episodes: 1,
  characters: [{ character_id: "c1", name: "Alice", role: "主角", age: 26, description: "主角", personality: "" }],
  scenes: [{ scene_id: 1, episode: 1, shot_type: "中景", description: "开场", prompt: "", negative_prompt: "", dialogue: "", emotion: "neutral", duration_seconds: 5, camera_movement: "static" }],
};

describe("App", () => {
  beforeEach(() => {
    useDramaStore.getState().reset();
  });

  it("renders topbar and initial status", () => {
    render(<App />);
    expect(screen.getByText("DashBox")).toBeInTheDocument();
    expect(screen.getByText("就绪")).toBeInTheDocument();
  });

  it("disables downstream buttons before script generation", () => {
    render(<App />);
    const menu = openFlowMenu();
    expect(within(menu).getByText("生成角色")).toBeDisabled();
    expect(within(menu).getByText("生成分镜")).toBeDisabled();
    expect(within(menu).getByText("生成视频")).toBeDisabled();
    expect(within(menu).getByText("生成配音")).toBeDisabled();
    expect(within(menu).getByText("生成字幕")).toBeDisabled();
    expect(within(menu).getByText("剧本质检")).toBeDisabled();
    expect(within(menu).getByText("视觉质检")).toBeDisabled();
    expect(within(menu).getByText("合成成片")).toBeDisabled();
    expect(within(getTopbar()).getByText("新建剧本")).not.toBeDisabled();
  });

  it("enables character/storyboard/voice/quality buttons after script is set", () => {
    useDramaStore.getState().setScriptData(sampleScript);
    render(<App />);
    const menu = openFlowMenu();
    expect(within(menu).getByText("生成角色")).not.toBeDisabled();
    expect(within(menu).getByText("生成分镜")).not.toBeDisabled();
    expect(within(menu).getByText("生成配音")).not.toBeDisabled();
    expect(within(menu).getByText("剧本质检")).not.toBeDisabled();
  });
});

describe("App 下拉菜单 dismiss 行为（2026-08-15 UI 打磨修复回归）", () => {
  beforeEach(() => {
    useDramaStore.getState().reset();
  });

  it("Escape 键关闭已打开的下拉菜单", () => {
    render(<App />);
    const menu = openFlowMenu();
    expect(menu).toBeInTheDocument();
    fireEvent.keyDown(document.body, { key: "Escape" });
    expect(getTopbar().querySelector(".dropdown-menu")).not.toBeInTheDocument();
  });

  it("点击菜单外部（document mousedown）关闭下拉菜单", () => {
    render(<App />);
    openFlowMenu();
    fireEvent.mouseDown(document.body);
    expect(getTopbar().querySelector(".dropdown-menu")).not.toBeInTheDocument();
  });

  it("点击菜单容器内部不关闭（事件目标在 wrapper 内）", () => {
    render(<App />);
    const menu = openFlowMenu();
    fireEvent.mouseDown(menu);
    expect(getTopbar().querySelector(".dropdown-menu")).toBeInTheDocument();
  });

  it("点击可用菜单项：关闭菜单并打开对应模态", () => {
    useDramaStore.getState().setScriptData(sampleScript);
    render(<App />);
    const menu = openFlowMenu();
    fireEvent.click(within(menu).getByText("生成角色"));
    expect(useDramaStore.getState().modals.character).toBe(true);
    expect(getTopbar().querySelector(".dropdown-menu")).not.toBeInTheDocument();
  });

  it("每个菜单项均带语义图标（.dropdown-item-icon）", () => {
    render(<App />);
    const menu = openFlowMenu();
    const items = menu.querySelectorAll(".dropdown-item");
    expect(items.length).toBe(8);
    items.forEach((item) => {
      expect(item.querySelector("svg.dropdown-item-icon")).toBeInTheDocument();
    });
  });

  it("菜单关闭后再次打开正常（dismiss 监听器正确清理/重建）", () => {
    render(<App />);
    openFlowMenu();
    fireEvent.keyDown(document.body, { key: "Escape" });
    expect(getTopbar().querySelector(".dropdown-menu")).not.toBeInTheDocument();
    // 再次打开 → 监听器重新挂载，Escape 仍可关闭
    const menu2 = openFlowMenu();
    expect(menu2).toBeInTheDocument();
    fireEvent.keyDown(document.body, { key: "Escape" });
    expect(getTopbar().querySelector(".dropdown-menu")).not.toBeInTheDocument();
  });
});

describe("App 顶栏与画布结构（2026-08-15 UI 打磨回归）", () => {
  beforeEach(() => {
    useDramaStore.getState().reset();
  });

  it("globalLoading 时顶栏三个动作按钮全部禁用", () => {
    useDramaStore.getState().startGlobalLoading("生成中");
    render(<App />);
    const topbar = getTopbar();
    expect(within(topbar).getByText("一键成片")).toBeDisabled();
    expect(within(topbar).getByText("操作流程")).toBeDisabled();
    expect(within(topbar).getByText("新建剧本")).toBeDisabled();
  });

  it("无剧本时显示空画布三步引导条", () => {
    const { container } = render(<App />);
    const guide = container.querySelector(".canvas-onboarding");
    expect(guide).toBeInTheDocument();
    expect(guide!.textContent).toContain("底部输入创意");
    expect(guide!.textContent).toContain("角色定妆照");
    expect(guide!.textContent).toContain("逐镜分镜");
  });

  it("有剧本后空画布引导条消失", () => {
    useDramaStore.getState().setScriptData(sampleScript);
    const { container } = render(<App />);
    expect(container.querySelector(".canvas-onboarding")).not.toBeInTheDocument();
  });

  it("批量操作按钮锚定在画布容器内部（去嵌套回归）", () => {
    const { container } = render(<App />);
    // .floating-actions 必须是 .canvas-container 的直接子节点
    expect(
      container.querySelector(".canvas-container > .floating-actions")
    ).toBeInTheDocument();
    // App 层不再额外包裹 .canvas-container（全文档唯一）
    expect(container.querySelectorAll(".canvas-container").length).toBe(1);
  });
});
