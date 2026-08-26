import { fireEvent, render, screen } from "@testing-library/react";
import LeftRail from "./LeftRail";
import { useDramaStore } from "../../store/useDramaStore";

describe("LeftRail", () => {
  beforeEach(() => {
    useDramaStore.getState().reset();
  });

  it("渲染 5 个导航按钮（新建/画布/角色库/模型库/引擎）", () => {
    render(<LeftRail />);
    expect(screen.getByTitle("新建剧本")).toBeInTheDocument();
    expect(screen.getByTitle("画布")).toBeInTheDocument();
    expect(screen.getByTitle("角色库（主体库）")).toBeInTheDocument();
    expect(screen.getByTitle("模型库")).toBeInTheDocument();
    expect(screen.getByTitle("引擎")).toBeInTheDocument();
  });

  it("默认画布为 active 态（activePanel=null）", () => {
    render(<LeftRail />);
    expect(screen.getByTitle("画布").className).toContain("active");
    expect(screen.getByTitle("角色库（主体库）").className).not.toContain("active");
  });

  it("点击角色库 → activePanel=characters；再点 → 收起 null", () => {
    render(<LeftRail />);
    const btn = screen.getByTitle("角色库（主体库）");
    fireEvent.click(btn);
    expect(useDramaStore.getState().activePanel).toBe("characters");
    fireEvent.click(btn);
    expect(useDramaStore.getState().activePanel).toBeNull();
  });

  it("点击模型库 → activePanel=models，与角色库互斥切换", () => {
    render(<LeftRail />);
    fireEvent.click(screen.getByTitle("模型库"));
    expect(useDramaStore.getState().activePanel).toBe("models");
    // 从模型库切到角色库：不经过 null，直接互斥切换
    fireEvent.click(screen.getByTitle("角色库（主体库）"));
    expect(useDramaStore.getState().activePanel).toBe("characters");
  });

  it("点击画布 → 收起面板（activePanel=null）", () => {
    useDramaStore.getState().setActivePanel("models");
    render(<LeftRail />);
    fireEvent.click(screen.getByTitle("画布"));
    expect(useDramaStore.getState().activePanel).toBeNull();
  });

  it("点击新建剧本 → 打开 script 模态", () => {
    render(<LeftRail />);
    fireEvent.click(screen.getByTitle("新建剧本"));
    expect(useDramaStore.getState().modals.script).toBe(true);
  });

  it("globalLoading 时新建按钮禁用，面板切换不受影响", () => {
    useDramaStore.getState().startGlobalLoading("生成中");
    render(<LeftRail />);
    expect(screen.getByTitle("新建剧本")).toBeDisabled();
    expect(screen.getByTitle("角色库（主体库）")).not.toBeDisabled();
  });
});
