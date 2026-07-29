import { fireEvent, render, screen, within } from "@testing-library/react";
import App from "./App";
import { useDramaStore } from "./store/useDramaStore";
import type { ScriptData } from "./api/client";

// 限定查询到 topbar，避免与 Canvas 节点面板的同名按钮冲突
const getTopbar = () => {
  const title = screen.getByText("AI 短剧工作台");
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
    expect(screen.getByText("AI 短剧工作台")).toBeInTheDocument();
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
