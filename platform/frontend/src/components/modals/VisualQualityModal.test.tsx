import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { vi } from "vitest";
import { VisualQualityModal } from "./VisualQualityModal";
import {
  checkVisualQuality,
  type QualityVisualData,
  type VideoData,
} from "../../api/client";

vi.mock("../../api/client", () => ({
  checkVisualQuality: vi.fn(),
}));

const mockCheck = vi.mocked(checkVisualQuality);

const videos: VideoData[] = [
  { scene_id: 1, video_url: "http://x/v1.mp4", duration_seconds: 5 },
  { scene_id: 2, video_url: "http://x/v2.mp4", duration_seconds: 8 },
];

const qualityData: QualityVisualData = {
  project_id: "p-1",
  title: "测试剧",
  scene_id: 1,
  score: 8.5,
  summary: "画面连贯",
} as QualityVisualData;

function renderModal(patch: Partial<Parameters<typeof VisualQualityModal>[0]> = {}) {
  const props = {
    videos,
    title: "测试剧",
    onClose: vi.fn(),
    onSuccess: vi.fn(),
    ...patch,
  };
  return { ...render(<VisualQualityModal {...props} />), props };
}

describe("VisualQualityModal（视觉漂移对照质检）", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("无视频时提示先生成视频片段，质检按钮禁用", () => {
    renderModal({ videos: [] });
    expect(screen.getByText("请先生成视频片段。")).toBeInTheDocument();
    expect(screen.getByText("开始质检").closest("button")).toBeDisabled();
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
  });

  it("默认选中首个视频：下拉选项、抽帧数默认 6、场景说明", () => {
    renderModal();
    expect(screen.getByText("视觉质检")).toBeInTheDocument();
    const select = screen.getByRole("combobox") as HTMLSelectElement;
    expect(select.value).toBe("1");
    expect(select.querySelectorAll("option").length).toBe(2);
    const range = screen.getByRole("slider") as HTMLInputElement;
    expect(range.value).toBe("6");
    const number = screen.getByRole("spinbutton") as HTMLInputElement;
    expect(number.value).toBe("6");
    expect(
      screen.getByText(/将对场景 1 的视频进行角色一致性/)
    ).toBeInTheDocument();
  });

  it("切换视频后按新场景质检，project_id 自动生成", async () => {
    mockCheck.mockResolvedValue({ success: true, data: qualityData });
    const { props } = renderModal();
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "2" } });
    expect(
      screen.getByText(/将对场景 2 的视频进行角色一致性/)
    ).toBeInTheDocument();
    fireEvent.click(screen.getByText("开始质检"));
    await waitFor(() =>
      expect(mockCheck).toHaveBeenCalledWith({
        project_id: expect.stringMatching(/^project-\d+$/),
        title: "测试剧",
        scene_id: 2,
        video_url: "http://x/v2.mp4",
        max_frames: 6,
      })
    );
    await waitFor(() => expect(props.onSuccess).toHaveBeenCalledWith(qualityData));
  });

  it("抽帧数：滑杆与数字输入联动，数字输入钳制 1-12", () => {
    renderModal();
    const range = screen.getByRole("slider") as HTMLInputElement;
    const number = screen.getByRole("spinbutton") as HTMLInputElement;
    fireEvent.change(range, { target: { value: "10" } });
    expect(number.value).toBe("10");
    fireEvent.change(number, { target: { value: "99" } });
    expect(number.value).toBe("12");
    fireEvent.change(number, { target: { value: "0" } });
    expect(number.value).toBe("1");
    fireEvent.change(number, { target: { value: "abc" } });
    expect(number.value).toBe("1");
  });

  it("抽帧数修改体现在提交参数；空标题回退「未命名视频」", async () => {
    mockCheck.mockResolvedValue({ success: true, data: qualityData });
    renderModal({ title: "" });
    fireEvent.change(screen.getByRole("slider"), { target: { value: "3" } });
    fireEvent.click(screen.getByText("开始质检"));
    await waitFor(() =>
      expect(mockCheck).toHaveBeenCalledWith(
        expect.objectContaining({ title: "未命名视频", max_frames: 3 })
      )
    );
  });

  it("后端返回失败：显示 error 字段；无 error 显示默认文案", async () => {
    mockCheck.mockResolvedValue({ success: false, error: "VLM 超时" });
    const { props, unmount } = renderModal();
    fireEvent.click(screen.getByText("开始质检"));
    await screen.findByText("VLM 超时");
    expect(props.onSuccess).not.toHaveBeenCalled();
    unmount();

    mockCheck.mockResolvedValue({ success: false });
    renderModal();
    fireEvent.click(screen.getByText("开始质检"));
    await screen.findByText("视觉质检失败");
  });

  it("请求异常：错误写入错误区", async () => {
    mockCheck.mockRejectedValue(new Error("连接拒绝"));
    renderModal();
    fireEvent.click(screen.getByText("开始质检"));
    await screen.findByText(/Error: 连接拒绝/);
  });

  it("质检中按钮禁用并显示加载态", async () => {
    let resolveCheck: (v: { success: boolean; data: QualityVisualData }) => void =
      () => {};
    mockCheck.mockReturnValue(
      new Promise((r) => {
        resolveCheck = r;
      })
    );
    const { container } = renderModal();
    fireEvent.click(screen.getByText("开始质检"));
    await waitFor(() =>
      expect(container.querySelector("span.loading")).toBeInTheDocument()
    );
    expect(
      container.querySelector("span.loading")!.closest("button")
    ).toBeDisabled();
    resolveCheck({ success: true, data: qualityData });
    await waitFor(() =>
      expect(container.querySelector("span.loading")).not.toBeInTheDocument()
    );
  });

  it("遮罩点击关闭，模态内部点击不冒泡；取消按钮关闭", () => {
    const { container, props } = renderModal();
    fireEvent.click(container.querySelector(".modal")!);
    expect(props.onClose).not.toHaveBeenCalled();
    fireEvent.click(container.querySelector(".modal-overlay")!);
    expect(props.onClose).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByText("取消"));
    expect(props.onClose).toHaveBeenCalledTimes(2);
  });
});
