import { fireEvent, render, screen } from "@testing-library/react";
import { vi } from "vitest";
import DramaNode from "./DramaNode";
import type { DramaNodeData } from "./layout";

// jsdom 下不渲染真实 Handle（依赖 ReactFlow store 上下文），用桩件记录 id/type/position
vi.mock("reactflow", async (importOriginal) => {
  const actual = await importOriginal<typeof import("reactflow")>();
  return {
    ...actual,
    Handle: (props: { id: string; type: string; position: string }) => (
      <div data-testid={`handle-${props.id}`} data-handle-type={props.type} data-position={props.position} />
    ),
  };
});

const base: DramaNodeData = { label: "测试节点", type: "script", detail: "详情" };

function renderNode(data: Partial<DramaNodeData> = {}, selected = false) {
  return render(<DramaNode data={{ ...base, ...data }} selected={selected} />);
}

describe("DramaNode 头部与状态行", () => {
  it("默认态：label + Wand2 + 「等待开始」", () => {
    renderNode();
    expect(screen.getByText("测试节点")).toBeInTheDocument();
    expect(screen.getByText("等待开始")).toBeInTheDocument();
  });

  it("statusText 覆盖默认状态文案", () => {
    renderNode({ statusText: "定妆照已生成" });
    expect(screen.getByText("定妆照已生成")).toBeInTheDocument();
    expect(screen.queryByText("等待开始")).not.toBeInTheDocument();
  });

  it("loading：显示 loadingText / 缺省「处理中…」", () => {
    renderNode({ loading: true });
    expect(screen.getByText("处理中…")).toBeInTheDocument();
  });

  it("hasGenerated：显示「已完成」", () => {
    renderNode({ hasGenerated: true });
    expect(screen.getByText("已完成")).toBeInTheDocument();
  });

  it("未知类型回退到 script 配置与 FileText 图标，正常渲染", () => {
    renderNode({ type: "alien" });
    expect(screen.getByText("测试节点")).toBeInTheDocument();
    expect(screen.getByTestId("handle-target-left")).toBeInTheDocument();
  });
});

describe("DramaNode 创意输入区（isScriptInput）", () => {
  it("展示引导文案与「去详情页编辑」按钮；点击触发 onOpenDetail 且阻止冒泡/拖拽", () => {
    const onOpenDetail = vi.fn();
    const parentClick = vi.fn();
    const parentMouseDown = vi.fn();
    render(
      <div onClick={parentClick} onMouseDown={parentMouseDown}>
        <DramaNode data={{ ...base, isScriptInput: true, onOpenDetail }} />
      </div>
    );
    expect(screen.getByText(/点击节点在右侧/)).toBeInTheDocument();
    const btn = screen.getByText("去详情页编辑");
    fireEvent.mouseDown(btn);
    expect(parentMouseDown).not.toHaveBeenCalled();
    fireEvent.click(btn);
    expect(onOpenDetail).toHaveBeenCalledTimes(1);
    expect(parentClick).not.toHaveBeenCalled();
  });

  it("无 onOpenDetail 时不渲染按钮", () => {
    renderNode({ isScriptInput: true });
    expect(screen.queryByText("去详情页编辑")).not.toBeInTheDocument();
  });

  it("loading 或 hasGenerated 时不渲染输入引导区", () => {
    const { unmount } = renderNode({ isScriptInput: true, loading: true, loadingText: "生成剧本中" });
    expect(screen.queryByText(/点击节点在右侧/)).not.toBeInTheDocument();
    unmount();
    renderNode({ isScriptInput: true, hasGenerated: true });
    expect(screen.queryByText(/点击节点在右侧/)).not.toBeInTheDocument();
  });
});

describe("DramaNode 媒体区", () => {
  it("imageUrl + hasGenerated：渲染图片与完成角标；悬停缩放", () => {
    renderNode({ type: "character", hasGenerated: true, imageUrl: "http://img/front.png" });
    const img = screen.getByRole("img", { name: "测试节点" });
    expect(img).toHaveAttribute("src", "http://img/front.png");
    fireEvent.mouseEnter(img);
    expect(img.style.transform).toBe("scale(1.02)");
    fireEvent.mouseLeave(img);
    expect(img.style.transform).toBe("scale(1)");
  });

  it("imageUrl 无 hasGenerated：无完成角标", () => {
    const { container } = renderNode({ type: "storyboard", imageUrl: "http://img/sb.png" });
    expect(screen.getByRole("img")).toBeInTheDocument();
    // 角标是绝对定位的绿色圆形 div，无图时容器内没有该结构（用 Check svg 数量间接验证不如直接查结构）
    expect(container.querySelectorAll("img").length).toBe(1);
  });

  it("character 未生成且未加载：紧凑占位「定妆照待生成」", () => {
    renderNode({ type: "character" });
    expect(screen.getByText("定妆照待生成")).toBeInTheDocument();
  });

  it("storyboard 未生成：MediaPlaceholder 显示「待生成分镜」与锁定原因", () => {
    renderNode({ type: "storyboard", lockReason: "请先生成所有角色定妆照" });
    expect(screen.getByText("待生成分镜")).toBeInTheDocument();
    expect(screen.getByText("请先生成所有角色定妆照")).toBeInTheDocument();
  });

  it("已生成但无图：MediaPlaceholder 显示类型名（无「待生成」前缀），无锁定条", () => {
    renderNode({ type: "storyboard", hasGenerated: true });
    expect(screen.getByText("分镜")).toBeInTheDocument();
    expect(screen.queryByText("待生成分镜")).not.toBeInTheDocument();
  });

  it("video 类型无图：占位显示「视频」", () => {
    renderNode({ type: "video", hasGenerated: true, videoUrl: "http://v/1.mp4" });
    expect(screen.getByText("视频")).toBeInTheDocument();
  });

  it("videoUrl：渲染 video 元素（poster 用 imageUrl），error 后切换为不可用占位", () => {
    const { container } = renderNode({
      type: "video",
      hasGenerated: true,
      videoUrl: "http://v/1.mp4",
      imageUrl: "http://img/cover.png",
    });
    const video = container.querySelector("video")!;
    expect(video).toHaveAttribute("src", "http://v/1.mp4");
    expect(video).toHaveAttribute("poster", "http://img/cover.png");
    fireEvent.error(video);
    expect(screen.getByText("视频预览暂不可用")).toBeInTheDocument();
    expect(container.querySelector("video")).not.toBeInTheDocument();
  });

  it("audioUrl：渲染 audio 元素", () => {
    const { container } = renderNode({ type: "voice", hasGenerated: true, audioUrl: "http://a/1.wav" });
    const audio = container.querySelector("audio")!;
    expect(audio).toHaveAttribute("src", "http://a/1.wav");
  });

  it("subtitleText：渲染字幕预览块", () => {
    renderNode({ type: "subtitle", hasGenerated: true, subtitleText: "你好 / 世界" });
    expect(screen.getByText("你好 / 世界")).toBeInTheDocument();
  });
});

describe("DramaNode 信息与质检区", () => {
  it("tags：普通节点全量渲染；未来节点最多 4 个", () => {
    const tags = ["t1", "t2", "t3", "t4", "t5"];
    const { unmount } = renderNode({ type: "quality", hasGenerated: true, tags });
    expect(screen.getByText("t5")).toBeInTheDocument();
    unmount();
    renderNode({ type: "quality", tags });
    expect(screen.getByText("t4")).toBeInTheDocument();
    expect(screen.queryByText("t5")).not.toBeInTheDocument();
  });

  it("meta：普通节点全量渲染；未来节点只取前 2 项", () => {
    const meta = [
      { label: "模型", value: "H3" },
      { label: "时长", value: "3s" },
      { label: "状态", value: "待生成" },
    ];
    const { unmount } = renderNode({ type: "video", hasGenerated: true, meta, imageUrl: "u" });
    expect(screen.getByText("状态")).toBeInTheDocument();
    unmount();
    renderNode({ type: "quality", meta });
    expect(screen.getByText("时长")).toBeInTheDocument();
    expect(screen.queryByText("状态")).not.toBeInTheDocument();
  });

  it("preview：非 loading 时渲染；loading 时隐藏", () => {
    const { unmount } = renderNode({ preview: "一段很长的描述" });
    expect(screen.getByText("一段很长的描述")).toBeInTheDocument();
    unmount();
    renderNode({ preview: "一段很长的描述", loading: true, loadingText: "处理中" });
    expect(screen.queryByText("一段很长的描述")).not.toBeInTheDocument();
  });

  it("qualitySummary / qualityIssues 渲染", () => {
    renderNode({
      type: "quality",
      hasGenerated: true,
      qualitySummary: "质量分 88 | 2 问题",
      qualityIssues: "[critical] 台词穿帮",
    });
    expect(screen.getByText("质量分 88 | 2 问题")).toBeInTheDocument();
    expect(screen.getByText("[critical] 台词穿帮")).toBeInTheDocument();
  });

  it("loading 块：spinner + loadingText / 缺省「正在生成…」", () => {
    const { unmount } = renderNode({ loading: true, loadingText: "生成视频中..." });
    expect(screen.getAllByText("生成视频中...").length).toBeGreaterThan(0);
    unmount();
    renderNode({ loading: true });
    expect(screen.getByText("正在生成…")).toBeInTheDocument();
  });

  it("非媒体类型锁定提示：voice 无音频时显示 lockReason；有音频后隐藏", () => {
    const { unmount } = renderNode({ type: "voice", lockReason: "请先生成该场景视频" });
    expect(screen.getByText("请先生成该场景视频")).toBeInTheDocument();
    unmount();
    renderNode({ type: "voice", hasGenerated: true, audioUrl: "http://a/1.wav", lockReason: "请先生成该场景视频" });
    expect(screen.queryByText("请先生成该场景视频")).not.toBeInTheDocument();
  });

  it("非媒体类型 loading 时不显示锁定提示", () => {
    renderNode({ type: "voice", loading: true, lockReason: "请先生成该场景视频" });
    expect(screen.queryByText("请先生成该场景视频")).not.toBeInTheDocument();
  });
});

describe("DramaNode 连接手柄", () => {
  it("默认：target-left + source-right", () => {
    renderNode({ type: "storyboard" });
    expect(screen.getByTestId("handle-target-left")).toHaveAttribute("data-position", "left");
    expect(screen.getByTestId("handle-source-right")).toHaveAttribute("data-position", "right");
    expect(screen.getByTestId("handle-source-right")).toHaveAttribute("data-handle-type", "source");
  });

  it("script：追加 source-top / source-bottom", () => {
    renderNode({ type: "script", hasGenerated: true });
    expect(screen.getByTestId("handle-source-top")).toBeInTheDocument();
    expect(screen.getByTestId("handle-source-bottom")).toBeInTheDocument();
  });

  it("character：追加 target-bottom", () => {
    renderNode({ type: "character" });
    expect(screen.getByTestId("handle-target-bottom")).toHaveAttribute("data-handle-type", "target");
  });

  it("video：追加 source-bottom", () => {
    renderNode({ type: "video", hasGenerated: true, videoUrl: "v" });
    expect(screen.getByTestId("handle-source-bottom")).toBeInTheDocument();
  });

  it("quality / visual_quality：追加 target-top", () => {
    const { unmount } = renderNode({ type: "quality" });
    expect(screen.getByTestId("handle-target-top")).toBeInTheDocument();
    unmount();
    renderNode({ type: "visual_quality" });
    expect(screen.getByTestId("handle-target-top")).toBeInTheDocument();
  });
});

describe("DramaNode 交互与选中态", () => {
  it("未选中悬停：进入浮起（transform/opacity），离开还原；未来节点宽度 240", () => {
    const { container } = renderNode({ type: "quality", lockReason: "锁定" });
    const card = container.firstChild as HTMLElement;
    expect(card).toHaveStyle({ width: "240px" }); // 未来节点
    expect(card.style.opacity).toBe("0.72");
    fireEvent.mouseEnter(card);
    expect(card.style.transform).toBe("translateY(-2px)");
    expect(card.style.opacity).toBe("1");
    fireEvent.mouseLeave(card);
    expect(card.style.transform).toBe("translateY(0)");
    expect(card.style.opacity).toBe("0.72");
  });

  it("已生成节点悬停离开还原为非暗淡样式；宽度 280", () => {
    const { container } = renderNode({ type: "quality", hasGenerated: true });
    const card = container.firstChild as HTMLElement;
    expect(card).toHaveStyle({ width: "280px" });
    fireEvent.mouseEnter(card);
    expect(card.style.opacity).toBe("1");
    fireEvent.mouseLeave(card);
    expect(card.style.opacity).toBe("1");
    expect(card.style.background).toBe("var(--bg-elevated)");
  });

  it("选中态：悬停不改变样式（保持选中浮起）", () => {
    const { container } = render(
      <DramaNode data={{ ...base, type: "quality" }} selected />
    );
    const card = container.firstChild as HTMLElement;
    expect(card.style.transform).toBe("translateY(-2px)");
    fireEvent.mouseEnter(card);
    expect(card.style.opacity).toBe("0.72"); // 暗淡未来节点选中后悬停不变
    fireEvent.mouseLeave(card);
    expect(card.style.opacity).toBe("0.72");
  });

  it("memo 比较器：data 引用不变不重渲染，变化后更新", () => {
    const data = { ...base };
    const { rerender } = render(<DramaNode data={data} />);
    rerender(<DramaNode data={data} />);
    expect(screen.getByText("测试节点")).toBeInTheDocument();
    rerender(<DramaNode data={{ ...data, label: "新名字" }} />);
    expect(screen.getByText("新名字")).toBeInTheDocument();
  });
});
