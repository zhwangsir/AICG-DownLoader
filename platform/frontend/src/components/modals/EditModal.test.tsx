import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { EditModal } from "./EditModal";
import { composeVideo } from "../../api/client";
import type { EditData, SubtitleData, VideoData, VoiceData } from "../../api/client";

// Mock 剪辑合成 API
vi.mock("../../api/client", () => ({
  composeVideo: vi.fn(),
}));

const mockComposeVideo = vi.mocked(composeVideo);

const videoOf = (scene_id: number): VideoData => ({
  scene_id,
  video_url: `http://x/v${scene_id}.mp4`,
  duration_seconds: 5,
});

const voiceOf = (scene_id: number): VoiceData => ({
  scene_id,
  audio_urls: [
    {
      filename: `a${scene_id}.wav`,
      voice: "v",
      text: "t",
      audio_url: `http://x/a${scene_id}.wav`,
    },
  ],
  total_lines: 1,
});

const subtitleOf = (scene_id: number): SubtitleData => ({
  scene_id,
  srt_content: "1\n00:00:00,000 --> 00:00:02,000\n你好\n",
  segments: [{ start: 0, end: 2, text: "你好" }],
  language: "zh",
  srt_url: `http://x/${scene_id}.srt`,
});

const editData: EditData = {
  project_id: "p1",
  title: "我的成片",
  final_video_url: "http://x/final.mp4",
  duration_seconds: 10,
  segments_count: 1,
};

function renderModal(patch: Partial<Parameters<typeof EditModal>[0]> = {}) {
  const onClose = vi.fn();
  const onSuccess = vi.fn();
  const utils = render(
    <EditModal
      videos={[videoOf(1)]}
      voices={[voiceOf(1)]}
      subtitles={[subtitleOf(1)]}
      onClose={onClose}
      onSuccess={onSuccess}
      {...patch}
    />
  );
  return { onClose, onSuccess, ...utils };
}

/** 表单区 combobox 顺序：0=转场, 1=分辨率, 2=帧率 */
const formSelects = () => {
  const all = screen.getAllByRole("combobox") as HTMLSelectElement[];
  return { transition: all[0], resolution: all[1], fps: all[2] };
};

describe("EditModal（剪辑合成成片）", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("无完整素材：提示并禁用合成按钮", () => {
    renderModal({ videos: [], voices: [], subtitles: [] });
    expect(
      screen.getByText("请先生成至少一个场景的完整素材（视频、配音、字幕）。")
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "合成成片" })).toBeDisabled();
  });

  it("素材完备性过滤：缺配音/空音频/缺字幕的场景被剔除", () => {
    renderModal({
      videos: [videoOf(1), videoOf(2), videoOf(3), videoOf(4)],
      voices: [
        voiceOf(1),
        { ...voiceOf(2), audio_urls: [] }, // 空音频 → 剔除
        // scene 3 无配音 → 剔除
        voiceOf(4),
      ],
      subtitles: [subtitleOf(1), subtitleOf(3), subtitleOf(4)], // scene 2 无字幕
    });
    const boxes = screen.getAllByRole("checkbox") as HTMLInputElement[];
    expect(boxes.map((b) => b.checked)).toEqual([true, true]);
    expect(screen.getByRole("checkbox", { name: "场景 1" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "场景 4" })).toBeChecked();
    expect(screen.queryByRole("checkbox", { name: "场景 2" })).not.toBeInTheDocument();
    expect(screen.queryByRole("checkbox", { name: "场景 3" })).not.toBeInTheDocument();
    expect(screen.getByText("将合成 2 个场景。")).toBeInTheDocument();
  });

  it("表单默认值：标题/转场/分辨率/帧率/BGM", () => {
    renderModal();
    expect(screen.getByPlaceholderText("输入成片标题...")).toHaveValue("短剧成片");
    expect(formSelects().transition.value).toBe("none");
    expect(formSelects().resolution.value).toBe("768x1344");
    expect(formSelects().fps.value).toBe("24");
    expect(screen.getByPlaceholderText("http://...")).toHaveValue("");
  });

  it("分辨率选项钉 768P 竖屏/横屏，不含 1080x1920", () => {
    renderModal();
    const opts = Array.from(formSelects().resolution.options).map((o) => o.value);
    expect(opts).toContain("768x1344");
    expect(opts).toContain("1344x768");
    expect(opts).not.toContain("1080x1920");
    expect(opts).not.toContain("1920x1080");
  });

  it("表单编辑：标题（含长文本）/转场/分辨率/帧率/BGM", () => {
    renderModal();
    const longTitle = "超长成片标题".repeat(100);
    fireEvent.change(screen.getByPlaceholderText("输入成片标题..."), {
      target: { value: longTitle },
    });
    expect(screen.getByPlaceholderText("输入成片标题...")).toHaveValue(longTitle);
    fireEvent.change(formSelects().transition, { target: { value: "fade" } });
    expect(formSelects().transition.value).toBe("fade");
    fireEvent.change(formSelects().resolution, { target: { value: "720x1280" } });
    expect(formSelects().resolution.value).toBe("720x1280");
    fireEvent.change(formSelects().fps, { target: { value: "60" } });
    expect(formSelects().fps.value).toBe("60");
    fireEvent.change(screen.getByPlaceholderText("http://..."), {
      target: { value: "http://x/bgm.mp3" },
    });
    expect(screen.getByPlaceholderText("http://...")).toHaveValue("http://x/bgm.mp3");
  });

  it("勾选切换与参与计数联动", () => {
    renderModal({
      videos: [videoOf(1), videoOf(2)],
      voices: [voiceOf(1), voiceOf(2)],
      subtitles: [subtitleOf(1), subtitleOf(2)],
    });
    expect(screen.getByText("将合成 2 个场景。")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("checkbox", { name: "场景 1" }));
    expect(screen.getByText("将合成 1 个场景。")).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "场景 1" })).not.toBeChecked();
    fireEvent.click(screen.getByRole("checkbox", { name: "场景 1" }));
    expect(screen.getByText("将合成 2 个场景。")).toBeInTheDocument();
  });

  it("全部取消勾选后点合成：报错且不调用 API", () => {
    renderModal();
    fireEvent.click(screen.getByRole("checkbox", { name: "场景 1" }));
    expect(screen.getByText("将合成 0 个场景。")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "合成成片" }));
    expect(screen.getByText("请至少勾选一个场景参与合成")).toBeInTheDocument();
    expect(mockComposeVideo).not.toHaveBeenCalled();
  });

  it("合成成功：payload 正确并回调 onSuccess", async () => {
    mockComposeVideo.mockResolvedValue({
      success: true,
      data: editData,
      error: null,
      elapsed_seconds: 2,
    });
    const { onSuccess } = renderModal();
    fireEvent.change(screen.getByPlaceholderText("输入成片标题..."), {
      target: { value: "我的成片" },
    });
    fireEvent.change(formSelects().transition, { target: { value: "fade" } });
    fireEvent.change(formSelects().resolution, { target: { value: "720x1280" } });
    fireEvent.change(formSelects().fps, { target: { value: "60" } });
    fireEvent.change(screen.getByPlaceholderText("http://..."), {
      target: { value: "http://x/bgm.mp3" },
    });
    fireEvent.click(screen.getByRole("button", { name: "合成成片" }));
    await waitFor(() => expect(onSuccess).toHaveBeenCalledWith(editData));
    expect(mockComposeVideo).toHaveBeenCalledWith({
      project_id: expect.stringMatching(/^project-\d+$/),
      title: "我的成片",
      segments: [
        {
          scene_id: 1,
          video_url: "http://x/v1.mp4",
          audio_url: "http://x/a1.wav",
          subtitle_url: "http://x/1.srt",
          duration_seconds: 5,
        },
      ],
      transition: "fade",
      bgm_url: "http://x/bgm.mp3",
      output_resolution: "720x1280",
      output_fps: 60,
    });
  });

  it("BGM 留空时 payload 传 null", async () => {
    mockComposeVideo.mockResolvedValue({
      success: true,
      data: editData,
      error: null,
      elapsed_seconds: 2,
    });
    renderModal();
    fireEvent.click(screen.getByRole("button", { name: "合成成片" }));
    await waitFor(() => expect(mockComposeVideo).toHaveBeenCalled());
    expect(mockComposeVideo.mock.calls[0][0].bgm_url).toBeNull();
    expect(mockComposeVideo.mock.calls[0][0].output_fps).toBe(24);
    expect(mockComposeVideo.mock.calls[0][0].output_resolution).toBe("768x1344");
  });

  it("合成失败：展示后端 error；无 error 回退默认文案；异常分支展示异常字符串", async () => {
    mockComposeVideo.mockResolvedValue({
      success: false,
      data: null,
      error: "ffmpeg 合成失败",
      elapsed_seconds: 1,
    });
    const { unmount } = renderModal();
    fireEvent.click(screen.getByRole("button", { name: "合成成片" }));
    expect(await screen.findByText("ffmpeg 合成失败")).toBeInTheDocument();
    unmount();

    mockComposeVideo.mockResolvedValue({
      success: false,
      data: null,
      error: null,
      elapsed_seconds: 1,
    });
    const { unmount: unmount2 } = renderModal();
    fireEvent.click(screen.getByRole("button", { name: "合成成片" }));
    expect(await screen.findByText("合成失败")).toBeInTheDocument();
    unmount2();

    mockComposeVideo.mockRejectedValue(new Error("disk full"));
    renderModal();
    fireEvent.click(screen.getByRole("button", { name: "合成成片" }));
    expect(await screen.findByText("Error: disk full")).toBeInTheDocument();
  });

  it("合成 loading 态：按钮禁用并显示加载指示，完成后恢复", async () => {
    let resolveFn!: (v: Awaited<ReturnType<typeof composeVideo>>) => void;
    mockComposeVideo.mockReturnValue(
      new Promise((r) => {
        resolveFn = r;
      })
    );
    const { onSuccess } = renderModal();
    const btn = screen.getByRole("button", { name: "合成成片" });
    fireEvent.click(btn);
    expect(btn).toBeDisabled();
    expect(document.querySelector(".loading")).toBeInTheDocument();
    resolveFn({ success: true, data: editData, error: null, elapsed_seconds: 2 });
    await waitFor(() => expect(onSuccess).toHaveBeenCalled());
    expect(btn).not.toBeDisabled();
  });

  it("覆盖层点击关闭；内容区点击不关闭；取消按钮关闭", () => {
    const { onClose, container } = renderModal();
    fireEvent.click(container.querySelector(".modal-overlay")!);
    expect(onClose).toHaveBeenCalledTimes(1);
    fireEvent.click(container.querySelector(".modal")!);
    expect(onClose).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it("卸载清理：unmount 无异常", () => {
    const { unmount } = renderModal();
    expect(() => unmount()).not.toThrow();
  });
});
