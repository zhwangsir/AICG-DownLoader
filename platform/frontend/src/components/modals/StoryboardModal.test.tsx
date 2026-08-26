import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { vi } from "vitest";
import { StoryboardModal } from "./StoryboardModal";
import { useDramaStore } from "../../store/useDramaStore";
import { generateStoryboard } from "../../api/client";
import type {
  AgentResponse,
  CharacterData,
  SceneData,
  StoryboardData,
} from "../../api/client";

vi.mock("../../api/client", () => ({
  generateStoryboard: vi.fn(),
}));

const mockGenerateStoryboard = vi.mocked(generateStoryboard);

const okResp = <T,>(data: T): AgentResponse<T> => ({
  success: true,
  data,
  error: null,
  elapsed_seconds: 1,
});
const errResp = (error: string | null): AgentResponse<StoryboardData> => ({
  success: false,
  data: null,
  error,
  elapsed_seconds: 1,
});

const characters: CharacterData[] = [
  {
    character_id: "c1",
    name: "Alice",
    role: "主角",
    age: 26,
    description: "外卖员",
    personality: "冷静",
  },
];

const scene1: SceneData = {
  scene_id: 1,
  episode: 1,
  shot_type: "中景",
  description: "雨夜开场",
  prompt: "rainy night",
  negative_prompt: "blur",
  character_actions: "",
  dialogue: "别回头",
  emotion: "紧张",
  duration_seconds: 5,
  camera_movement: "推镜头",
};

const scene2: SceneData = {
  ...scene1,
  scene_id: 2,
  description: "天台对峙",
  prompt: "rooftop",
  dialogue: "",
  duration_seconds: 8,
};

function renderModal(
  props: Partial<Parameters<typeof StoryboardModal>[0]> = {}
) {
  const onClose = vi.fn();
  const onSuccess = vi.fn();
  const utils = render(
    <StoryboardModal
      scenes={[scene1, scene2]}
      characters={characters}
      onClose={onClose}
      onSuccess={onSuccess}
      {...props}
    />
  );
  return { onClose, onSuccess, ...utils };
}

function fieldControl(label: string, selector = "input") {
  const field = screen.getByText(label).closest(".modal-field");
  expect(field).not.toBeNull();
  const control = field!.querySelector(selector);
  expect(control).not.toBeNull();
  return control as HTMLElement;
}

const sketchResult: StoryboardData = {
  scene_id: 1,
  image_url: "http://img/sketch.png",
  prompt_used: "used-prompt",
  is_sketch: true,
  sketch_seed: 123,
};

const finalResult: StoryboardData = {
  scene_id: 1,
  image_url: "http://img/final.png",
  prompt_used: "used-prompt",
};

describe("StoryboardModal 空数据与基础渲染", () => {
  beforeEach(() => {
    useDramaStore.getState().reset();
    vi.clearAllMocks();
  });

  it("空场景列表：引导文案 + 主按钮禁用 + 无保存按钮", () => {
    const { onClose } = renderModal({ scenes: [] });
    expect(screen.getByText(/请先生成剧本/)).toBeInTheDocument();
    expect(screen.getByText("生成线稿")).toBeDisabled();
    expect(screen.queryByText("保存场景修改")).not.toBeInTheDocument();
    fireEvent.click(screen.getByText("取消"));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("默认渲染：首个场景信息/对白/画风默认值/线稿先行开启", () => {
    renderModal();
    expect(screen.getByText("生成分镜关键帧")).toBeInTheDocument();
    const select = fieldControl("选择场景", "select") as HTMLSelectElement;
    expect(select.value).toBe("1");
    expect(screen.getByText("场景 1 — 中景（5s）")).toBeInTheDocument();
    // 场景信息（可编辑）区块
    const textareas = screen.getAllByRole("textbox") as HTMLTextAreaElement[];
    expect(textareas[0].value).toBe("雨夜开场");
    expect(textareas[1].value).toBe("rainy night");
    expect(screen.getByText("「别回头」")).toBeInTheDocument();
    expect((fieldControl("画风") as HTMLInputElement).value).toBe("写实电影感");
    expect(screen.getByRole("checkbox")).toBeChecked();
    expect(screen.getByTestId("prompt-toolkit")).toBeInTheDocument();
    expect(screen.getByText("生成线稿")).not.toBeDisabled();
  });

  it("切换场景：描述/提示词联动刷新，无对白场景不展示对白块", () => {
    renderModal();
    fireEvent.change(fieldControl("选择场景", "select"), { target: { value: "2" } });
    const textareas = screen.getAllByRole("textbox") as HTMLTextAreaElement[];
    expect(textareas[0].value).toBe("天台对峙");
    expect(textareas[1].value).toBe("rooftop");
    expect(screen.queryByText("「别回头」")).not.toBeInTheDocument();
    expect(screen.getByText(/场景 2 · 中景 · 紧张/)).toBeInTheDocument();
  });

  it("遮罩点击关闭；模态内部点击不关闭", () => {
    const { onClose, container } = renderModal();
    fireEvent.click(screen.getByText("生成分镜关键帧"));
    expect(onClose).not.toHaveBeenCalled();
    fireEvent.click(container.querySelector(".modal-overlay")!);
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});

describe("StoryboardModal 场景编辑与保存", () => {
  beforeEach(() => {
    useDramaStore.getState().reset();
    useDramaStore.getState().setScriptData({
      project_id: "p1",
      title: "t",
      genre: "g",
      aspect_ratio: "9:16",
      total_episodes: 1,
      characters,
      scenes: [scene1, scene2],
    });
    vi.clearAllMocks();
  });

  it("编辑描述/提示词 + 保存场景修改：写回 store，已保存徽标 1.5s 后消失", async () => {
    vi.useFakeTimers();
    try {
      renderModal();
      const textareas = screen.getAllByRole("textbox") as HTMLTextAreaElement[];
      fireEvent.change(textareas[0], { target: { value: "改后描述" } });
      fireEvent.change(textareas[1], { target: { value: "改后提示词" } });
      fireEvent.click(screen.getByText("保存场景修改"));
      expect(screen.getByText("已保存")).toBeInTheDocument();
      const saved = useDramaStore.getState().scriptData!.scenes[0];
      expect(saved.description).toBe("改后描述");
      expect(saved.prompt).toBe("改后提示词");
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1600);
      });
      expect(screen.queryByText("已保存")).not.toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("StoryboardModal 两段式线稿流程（M25.9 C1）", () => {
  beforeEach(() => {
    useDramaStore.getState().reset();
    vi.clearAllMocks();
  });

  it("生成线稿：sketch_mode=true + refine_seed=null，成功后仅预览不回写", async () => {
    mockGenerateStoryboard.mockResolvedValue(okResp(sketchResult));
    const { onSuccess } = renderModal();
    // 编辑描述/提示词/画风后生成，验证 payload 合并
    const textareas = screen.getAllByRole("textbox") as HTMLTextAreaElement[];
    fireEvent.change(textareas[0], { target: { value: "改后描述" } });
    fireEvent.change(textareas[1], { target: { value: "改后提示词" } });
    fireEvent.change(fieldControl("画风"), { target: { value: "赛博朋克" } });
    fireEvent.click(screen.getByText("生成线稿"));
    await waitFor(() =>
      expect(screen.getByAltText("分镜线稿预览")).toBeInTheDocument()
    );
    expect(mockGenerateStoryboard).toHaveBeenCalledWith({
      scene: { ...scene1, description: "改后描述", prompt: "改后提示词" },
      characters,
      style: "赛博朋克",
      sketch_mode: true,
      refine_seed: null,
    });
    // 线稿阶段不回写 onSuccess
    expect(onSuccess).not.toHaveBeenCalled();
    expect(screen.getByText(/线稿预览 · seed 123/)).toBeInTheDocument();
    expect((screen.getByAltText("分镜线稿预览") as HTMLImageElement).src).toBe(
      "http://img/sketch.png"
    );
  });

  it("采用构图并精绘：同 seed refine，成功后回写 onSuccess 并清空预览", async () => {
    mockGenerateStoryboard
      .mockResolvedValueOnce(okResp(sketchResult))
      .mockResolvedValueOnce(okResp(finalResult));
    const { onSuccess } = renderModal();
    fireEvent.click(screen.getByText("生成线稿"));
    await waitFor(() =>
      expect(screen.getByAltText("分镜线稿预览")).toBeInTheDocument()
    );
    fireEvent.click(screen.getByText("采用构图并精绘"));
    await waitFor(() => expect(onSuccess).toHaveBeenCalledWith(finalResult));
    expect(mockGenerateStoryboard).toHaveBeenLastCalledWith(
      expect.objectContaining({ sketch_mode: false, refine_seed: 123 })
    );
    // 预览被清空
    expect(screen.queryByAltText("分镜线稿预览")).not.toBeInTheDocument();
  });

  it("重出线稿 pending：精绘按钮转 loading、复选框禁用；sketch_seed 缺失兜底 0", async () => {
    let resolveSecond!: (v: AgentResponse<StoryboardData>) => void;
    mockGenerateStoryboard
      .mockResolvedValueOnce(okResp(sketchResult))
      .mockReturnValueOnce(
        new Promise((r) => {
          resolveSecond = r;
        })
      );
    renderModal();
    fireEvent.click(screen.getByText("生成线稿"));
    await waitFor(() =>
      expect(screen.getByAltText("分镜线稿预览")).toBeInTheDocument()
    );
    fireEvent.click(screen.getByText("重出线稿"));
    // pending 中：预览保留，按钮 loading/禁用
    expect(screen.getByAltText("分镜线稿预览")).toBeInTheDocument();
    expect(screen.getByRole("checkbox")).toBeDisabled();
    expect(screen.getByText("重出线稿")).toBeDisabled();
    expect(document.querySelectorAll(".modal .loading").length).toBeGreaterThan(0);
    resolveSecond(
      okResp({ ...sketchResult, image_url: "http://img/sketch2.png", sketch_seed: undefined })
    );
    await waitFor(() =>
      expect(screen.getByText(/线稿预览 · seed 0/)).toBeInTheDocument()
    );
  });

  it("弃用：清空线稿预览", async () => {
    mockGenerateStoryboard.mockResolvedValue(okResp(sketchResult));
    renderModal();
    fireEvent.click(screen.getByText("生成线稿"));
    await waitFor(() =>
      expect(screen.getByAltText("分镜线稿预览")).toBeInTheDocument()
    );
    fireEvent.click(screen.getByText("弃用"));
    expect(screen.queryByAltText("分镜线稿预览")).not.toBeInTheDocument();
  });

  it("关闭线稿先行：清空已有预览并切换为一段式直出", async () => {
    mockGenerateStoryboard.mockResolvedValue(okResp(sketchResult));
    const { onSuccess } = renderModal();
    fireEvent.click(screen.getByText("生成线稿"));
    await waitFor(() =>
      expect(screen.getByAltText("分镜线稿预览")).toBeInTheDocument()
    );
    fireEvent.click(screen.getByRole("checkbox"));
    // 预览被清空，主按钮切换为「生成分镜」
    expect(screen.queryByAltText("分镜线稿预览")).not.toBeInTheDocument();
    mockGenerateStoryboard.mockResolvedValue(okResp(finalResult));
    fireEvent.click(screen.getByText("生成分镜"));
    await waitFor(() => expect(onSuccess).toHaveBeenCalledWith(finalResult));
    expect(mockGenerateStoryboard).toHaveBeenLastCalledWith(
      expect.objectContaining({ sketch_mode: false, refine_seed: null })
    );
  });
});

describe("StoryboardModal 错误态", () => {
  beforeEach(() => {
    useDramaStore.getState().reset();
    vi.clearAllMocks();
  });

  it("resp.error 展示后端错误消息", async () => {
    mockGenerateStoryboard.mockResolvedValue(errResp("ComfyUI 离线"));
    renderModal();
    fireEvent.click(screen.getByText("生成线稿"));
    await waitFor(() =>
      expect(screen.getByText("ComfyUI 离线")).toBeInTheDocument()
    );
  });

  it("无 error 字段兜底「生成失败」", async () => {
    mockGenerateStoryboard.mockResolvedValue(errResp(null));
    renderModal();
    fireEvent.click(screen.getByText("生成线稿"));
    await waitFor(() => expect(screen.getByText("生成失败")).toBeInTheDocument());
  });

  it("抛异常 → String(e) 展示", async () => {
    mockGenerateStoryboard.mockRejectedValue(new Error("连接拒绝"));
    renderModal();
    fireEvent.click(screen.getByText("生成线稿"));
    await waitFor(() => expect(screen.getByText(/连接拒绝/)).toBeInTheDocument());
  });

  it("切换场景清除上一场景的错误", async () => {
    mockGenerateStoryboard.mockResolvedValue(errResp("ComfyUI 离线"));
    renderModal();
    fireEvent.click(screen.getByText("生成线稿"));
    await waitFor(() =>
      expect(screen.getByText("ComfyUI 离线")).toBeInTheDocument()
    );
    fireEvent.change(fieldControl("选择场景", "select"), { target: { value: "2" } });
    expect(screen.queryByText("ComfyUI 离线")).not.toBeInTheDocument();
  });
});
