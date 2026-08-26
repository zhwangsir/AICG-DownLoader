import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { vi } from "vitest";
import { VideoModal } from "./VideoModal";
import { useDramaStore } from "../../store/useDramaStore";
import { generateVideoAsync, rerunShot } from "../../api/client";
import { useProgress } from "../../hooks/useProgress";
import type {
  AgentResponse,
  AsyncTaskResponse,
  SceneData,
  StoryboardData,
  VideoData,
} from "../../api/client";

vi.mock("../../api/client", () => ({
  generateVideoAsync: vi.fn(),
  rerunShot: vi.fn(),
}));
vi.mock("../../hooks/useProgress", () => ({
  useProgress: vi.fn(),
}));

const mockGenerateVideoAsync = vi.mocked(generateVideoAsync);
const mockRerunShot = vi.mocked(rerunShot);
const mockUseProgress = vi.mocked(useProgress);

type ProgressMock = ReturnType<typeof useProgress>;
const idleProgress = (): ProgressMock => ({
  connected: false,
  status: null,
  percent: 0,
  message: "",
  result: null,
  error: null,
  reset: vi.fn(),
});
let progressState: ProgressMock = idleProgress();

const okResp = <T,>(data: T): AgentResponse<T> => ({
  success: true,
  data,
  error: null,
  elapsed_seconds: 1,
});
const errResp = (error: string | null): AgentResponse<VideoData> => ({
  success: false,
  data: null,
  error,
  elapsed_seconds: 1,
});

const taskResp: AsyncTaskResponse = {
  task_id: "t1",
  agent: "video",
  status: "pending",
  poll_url: "http://poll/1",
  stream_url: "http://stream/1",
};

const DEFAULT_NEG = "blurry, low quality, deformed, ugly, watermark, static";

const sb1: StoryboardData = {
  scene_id: 1,
  image_url: "http://img/1.png",
  prompt_used: "used-1",
};
const sb2: StoryboardData = {
  scene_id: 2,
  image_url: "http://img/2.png",
  prompt_used: "used-2",
};

const scene1: SceneData = {
  scene_id: 1,
  episode: 1,
  shot_type: "中景",
  description: "雨夜开场",
  prompt: "scene-prompt-1",
  negative_prompt: "scene-neg-1",
  character_actions: "",
  dialogue: "",
  emotion: "紧张",
  duration_seconds: 5,
  camera_movement: "推镜头",
};
const scene2: SceneData = {
  ...scene1,
  scene_id: 2,
  prompt: "",
  negative_prompt: "",
};

function renderModal(props: Partial<Parameters<typeof VideoModal>[0]> = {}) {
  const onClose = vi.fn();
  const onSuccess = vi.fn();
  // 每次调用生成新元素引用：复用同一 element 会触发 React bailout 跳过渲染
  const makeElement = () => (
    <VideoModal
      storyboards={[sb1, sb2]}
      scenes={[scene1, scene2]}
      onClose={onClose}
      onSuccess={onSuccess}
      {...props}
    />
  );
  const utils = render(makeElement());
  return { onClose, onSuccess, makeElement, ...utils };
}

function fieldControl(label: string, selector = "input") {
  const field = screen.getByText(label).closest(".modal-field");
  expect(field).not.toBeNull();
  const control = field!.querySelector(selector);
  expect(control).not.toBeNull();
  return control as HTMLElement;
}

/** 等待组件内 render 期 setTimeout(300ms) 落地 */
async function flushModalTimer() {
  await act(async () => {
    await new Promise((r) => setTimeout(r, 400));
  });
}

describe("VideoModal 空数据与基础渲染", () => {
  beforeEach(() => {
    useDramaStore.getState().reset();
    vi.clearAllMocks();
    progressState = idleProgress();
    mockUseProgress.mockImplementation(() => progressState);
  });

  it("空分镜：引导文案 + 生成按钮禁用", () => {
    const { onClose } = renderModal({ storyboards: [] });
    expect(screen.getByText("请先生成分镜关键帧。")).toBeInTheDocument();
    expect(screen.getByText("生成视频")).toBeDisabled();
    fireEvent.click(screen.getByText("取消"));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("默认渲染：分镜选择/时长/提示词/反向提示词/关键帧预览", () => {
    renderModal();
    expect(
      screen.getByText("生成视频片段（H3 / LTX-2.5 双引擎）")
    ).toBeInTheDocument();
    expect((fieldControl("选择分镜", "select") as HTMLSelectElement).value).toBe("1");
    expect(
      (fieldControl("视频时长（秒，3-15）") as HTMLInputElement).value
    ).toBe("5");
    expect(screen.getByDisplayValue("scene-prompt-1")).toBeInTheDocument();
    expect(screen.getByDisplayValue("scene-neg-1")).toBeInTheDocument();
    expect((screen.getByAltText("scene-1") as HTMLImageElement).src).toBe(
      "http://img/1.png"
    );
    expect(screen.getByTestId("prompt-toolkit")).toBeInTheDocument();
    // 无 pipeline 快照/已有视频 → 无重拍按钮
    expect(screen.queryByText("锚点重拍")).not.toBeInTheDocument();
    expect(screen.queryByText("换 seed 重拍")).not.toBeInTheDocument();
    // 进度 idle → ProgressBar 不渲染
    expect(screen.queryByText(/连接中|已连接/)).not.toBeInTheDocument();
  });

  it("切换分镜：提示词兜底 prompt_used，反向词兜底默认串", () => {
    renderModal();
    fireEvent.change(fieldControl("选择分镜", "select"), { target: { value: "2" } });
    expect(screen.getByDisplayValue("used-2")).toBeInTheDocument();
    expect(screen.getByDisplayValue(DEFAULT_NEG)).toBeInTheDocument();
    expect((screen.getByAltText("scene-2") as HTMLImageElement).src).toBe(
      "http://img/2.png"
    );
  });

  it("遮罩点击关闭；模态内部点击不关闭", () => {
    const { onClose, container } = renderModal();
    fireEvent.click(screen.getByText("生成视频片段（H3 / LTX-2.5 双引擎）"));
    expect(onClose).not.toHaveBeenCalled();
    fireEvent.click(container.querySelector(".modal-overlay")!);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("边界：剧本场景缺失（scenes=[]）→ 提示词兜底空串/默认负向串，不崩溃", () => {
    renderModal({ scenes: [] });
    const textareas = screen.getAllByRole("textbox") as HTMLTextAreaElement[];
    expect(textareas[0].value).toBe("");
    expect(textareas[1].value).toBe(DEFAULT_NEG);
    // 分镜预览仍可用，生成按钮可用
    expect(screen.getByAltText("scene-1")).toBeInTheDocument();
    expect(screen.getByText("生成视频")).not.toBeDisabled();
  });

  it("边界：场景 prompt 与分镜 prompt_used 双空 → 提示词为空串", () => {
    renderModal({
      storyboards: [{ ...sb1, prompt_used: "" }],
      scenes: [{ ...scene1, prompt: "" }],
    });
    const textareas = screen.getAllByRole("textbox") as HTMLTextAreaElement[];
    expect(textareas[0].value).toBe("");
  });

  it("ProgressBar 透传：running 进度展示", () => {
    progressState = {
      ...idleProgress(),
      connected: true,
      status: "running",
      percent: 40,
      message: "渲染中",
    };
    renderModal();
    expect(screen.getByText(/渲染中/)).toBeInTheDocument();
    expect(screen.getByText("40%")).toBeInTheDocument();
  });
});

describe("VideoModal 视频生成流", () => {
  beforeEach(() => {
    useDramaStore.getState().reset();
    vi.clearAllMocks();
    progressState = idleProgress();
    mockUseProgress.mockImplementation(() => progressState);
  });

  it("生成成功：创建异步任务 → SSE 完成回写 onSuccess 并重置进度", async () => {
    let resolveTask!: (v: AsyncTaskResponse) => void;
    mockGenerateVideoAsync.mockReturnValue(
      new Promise((r) => {
        resolveTask = r;
      })
    );
    const { onSuccess, makeElement, rerender } = renderModal();
    fireEvent.click(screen.getByText("生成视频"));
    // loading 中：按钮禁用 + loading 动画
    const genBtn = document.querySelector(
      ".modal-actions .topbar-btn-primary"
    ) as HTMLButtonElement;
    expect(genBtn).toBeDisabled();
    expect(genBtn.querySelector(".loading")).toBeInTheDocument();

    resolveTask(taskResp);
    await waitFor(() =>
      expect(mockUseProgress).toHaveBeenCalledWith("http://stream/1")
    );

    const videoData: VideoData = {
      scene_id: 1,
      video_url: "http://v/1.mp4",
      duration_seconds: 5,
    };
    progressState = {
      ...idleProgress(),
      connected: true,
      status: "completed",
      percent: 100,
      message: "完成",
      result: videoData,
      reset: vi.fn(),
    };
    rerender(makeElement());
    await flushModalTimer();
    expect(onSuccess).toHaveBeenCalledWith(videoData);
    expect(progressState.reset).toHaveBeenCalled();
  });

  it("payload：编辑值透传（自定义提示词/负向词/时长）", async () => {
    mockGenerateVideoAsync.mockResolvedValue(taskResp);
    renderModal();
    fireEvent.change(fieldControl("视频时长（秒，3-15）"), {
      target: { value: "8" },
    });
    fireEvent.change(screen.getByDisplayValue("scene-prompt-1"), {
      target: { value: "自定义提示词" },
    });
    fireEvent.change(screen.getByDisplayValue("scene-neg-1"), {
      target: { value: "自定义负向" },
    });
    fireEvent.click(screen.getByText("生成视频"));
    await waitFor(() =>
      expect(mockGenerateVideoAsync).toHaveBeenCalledWith({
        scene_id: 1,
        image_url: "http://img/1.png",
        prompt: "自定义提示词",
        negative_prompt: "自定义负向",
        duration_seconds: 8,
      })
    );
  });

  it("payload：提示词/负向词清空后兜底 prompt_used 与默认负向串", async () => {
    mockGenerateVideoAsync.mockResolvedValue(taskResp);
    renderModal();
    fireEvent.change(screen.getByDisplayValue("scene-prompt-1"), {
      target: { value: "" },
    });
    fireEvent.change(screen.getByDisplayValue("scene-neg-1"), {
      target: { value: "" },
    });
    fireEvent.click(screen.getByText("生成视频"));
    await waitFor(() =>
      expect(mockGenerateVideoAsync).toHaveBeenCalledWith({
        scene_id: 1,
        image_url: "http://img/1.png",
        prompt: "used-1",
        negative_prompt: DEFAULT_NEG,
        duration_seconds: 5,
      })
    );
  });

  it("创建任务抛异常 → 错误展示且 loading 复位", async () => {
    mockGenerateVideoAsync.mockRejectedValue(new Error("创建异步任务失败: 503"));
    renderModal();
    fireEvent.click(screen.getByText("生成视频"));
    await waitFor(() =>
      expect(screen.getByText(/创建异步任务失败/)).toBeInTheDocument()
    );
    expect(screen.getByText("生成视频")).not.toBeDisabled();
  });

  it("SSE 失败（loading 中）→ 错误展示并重置进度", async () => {
    mockGenerateVideoAsync.mockResolvedValue(taskResp);
    const { makeElement, rerender } = renderModal();
    fireEvent.click(screen.getByText("生成视频"));
    await waitFor(() =>
      expect(mockUseProgress).toHaveBeenCalledWith("http://stream/1")
    );
    progressState = {
      ...idleProgress(),
      status: "failed",
      error: "引擎过载",
      reset: vi.fn(),
    };
    rerender(makeElement());
    await flushModalTimer();
    // ProgressBar 与模态级错误各展示一处
    expect(screen.getAllByText("引擎过载").length).toBe(2);
    expect(progressState.reset).toHaveBeenCalled();
    // loading 复位
    expect(screen.getByText("生成视频")).not.toBeDisabled();
  });

  it("SSE 失败且 error 为 null → 兜底「视频生成失败」", async () => {
    mockGenerateVideoAsync.mockResolvedValue(taskResp);
    const { makeElement, rerender } = renderModal();
    fireEvent.click(screen.getByText("生成视频"));
    await waitFor(() =>
      expect(mockUseProgress).toHaveBeenCalledWith("http://stream/1")
    );
    progressState = { ...idleProgress(), status: "failed", error: null };
    rerender(makeElement());
    await flushModalTimer();
    expect(screen.getByText("视频生成失败")).toBeInTheDocument();
  });

  it("SSE 失败但非 loading（陈旧事件）→ 仅 ProgressBar 展示，不写模态错误", async () => {
    progressState = { ...idleProgress(), status: "failed", error: "引擎过载" };
    renderModal();
    await flushModalTimer();
    // 仅 ProgressBar 的 progress-bar-error 展示；模态级错误未触发
    const errEl = screen.getByText("引擎过载");
    expect(errEl.className).toBe("progress-bar-error");
    expect(screen.getByText("生成视频")).not.toBeDisabled();
  });

  it("SSE 完成但 result 缺 video_url / 为 null → 不回写 onSuccess", async () => {
    const { onSuccess, makeElement, rerender } = renderModal();
    progressState = { ...idleProgress(), status: "completed", result: null };
    rerender(makeElement());
    progressState = { ...idleProgress(), status: "completed", result: { foo: 1 } };
    rerender(makeElement());
    await flushModalTimer();
    expect(onSuccess).not.toHaveBeenCalled();
  });
});

describe("VideoModal 锚点重拍（M25.1）", () => {
  beforeEach(() => {
    useDramaStore.getState().reset();
    vi.clearAllMocks();
    progressState = idleProgress();
    mockUseProgress.mockImplementation(() => progressState);
  });

  function seedRerunStore() {
    useDramaStore.getState().setPipelineProjectId("proj-1");
    useDramaStore
      .getState()
      .addVideo({ scene_id: 1, video_url: "http://v/old.mp4", duration_seconds: 5 });
  }

  it("有快照且有该场景视频 → 显示两个重拍按钮；锚点重拍不带 reseed", async () => {
    seedRerunStore();
    const videoData: VideoData = {
      scene_id: 1,
      video_url: "http://v/new.mp4",
      duration_seconds: 5,
    };
    mockRerunShot.mockResolvedValue(okResp(videoData));
    const { onSuccess } = renderModal();
    fireEvent.click(screen.getByText("锚点重拍"));
    await waitFor(() => expect(onSuccess).toHaveBeenCalledWith(videoData));
    expect(mockRerunShot).toHaveBeenCalledWith({ project_id: "proj-1", scene_id: 1 });
  });

  it("换 seed 重拍：显式 reseed=true", async () => {
    seedRerunStore();
    mockRerunShot.mockResolvedValue(
      okResp({ scene_id: 1, video_url: "http://v/reseed.mp4", duration_seconds: 5 })
    );
    renderModal();
    fireEvent.click(screen.getByText("换 seed 重拍"));
    await waitFor(() =>
      expect(mockRerunShot).toHaveBeenCalledWith({
        project_id: "proj-1",
        scene_id: 1,
        reseed: true,
      })
    );
  });

  it("重拍 pending：按钮文案「重拍中…」且全部动作禁用", async () => {
    seedRerunStore();
    let resolveRerun!: (v: AgentResponse<VideoData>) => void;
    mockRerunShot.mockReturnValue(
      new Promise((r) => {
        resolveRerun = r;
      })
    );
    renderModal();
    fireEvent.click(screen.getByText("锚点重拍"));
    expect(screen.getByText("重拍中…")).toBeInTheDocument();
    expect(screen.getByText("重拍中…").closest("button")).toBeDisabled();
    expect(screen.getByText("换 seed 重拍")).toBeDisabled();
    expect(screen.getByText("生成视频")).toBeDisabled();
    resolveRerun(
      okResp({ scene_id: 1, video_url: "http://v/new.mp4", duration_seconds: 5 })
    );
    await waitFor(() => expect(screen.getByText("锚点重拍")).toBeInTheDocument());
  });

  it("重拍失败：resp.error / 兜底「重拍失败」/ 抛异常", async () => {
    seedRerunStore();
    mockRerunShot.mockResolvedValue(errResp("快照缺失"));
    renderModal();
    fireEvent.click(screen.getByText("锚点重拍"));
    await waitFor(() => expect(screen.getByText("快照缺失")).toBeInTheDocument());

    mockRerunShot.mockResolvedValue(errResp(null));
    fireEvent.click(screen.getByText("锚点重拍"));
    await waitFor(() => expect(screen.getByText("重拍失败")).toBeInTheDocument());

    mockRerunShot.mockRejectedValue(new Error("锚点重拍失败: 500"));
    fireEvent.click(screen.getByText("锚点重拍"));
    await waitFor(() =>
      expect(screen.getByText(/锚点重拍失败: 500/)).toBeInTheDocument()
    );
  });

  it("有快照但该场景无视频产物 → 不显示重拍按钮", () => {
    useDramaStore.getState().setPipelineProjectId("proj-1");
    useDramaStore
      .getState()
      .addVideo({ scene_id: 99, video_url: "http://v/other.mp4", duration_seconds: 5 });
    renderModal();
    expect(screen.queryByText("锚点重拍")).not.toBeInTheDocument();
    expect(screen.queryByText("换 seed 重拍")).not.toBeInTheDocument();
  });
});
