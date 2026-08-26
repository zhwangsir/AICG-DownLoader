import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { vi } from "vitest";
import { SubtitleModal } from "./SubtitleModal";
import {
  generateSubtitle,
  type SubtitleData,
  type VoiceData,
} from "../../api/client";
import { useDramaStore } from "../../store/useDramaStore";

vi.mock("../../api/client", () => ({
  generateSubtitle: vi.fn(),
}));

const mockGenerateSubtitle = vi.mocked(generateSubtitle);

const voices: VoiceData[] = [
  {
    scene_id: 1,
    total_lines: 2,
    audio_urls: [
      { filename: "a1.wav", voice: "zh-f", text: "你好", audio_url: "http://x/a1.wav" },
      { filename: "a2.wav", voice: "zh-f", text: "世界", audio_url: "http://x/a2.wav" },
    ],
  },
  {
    scene_id: 2,
    total_lines: 1,
    audio_urls: [
      { filename: "b1.wav", voice: "zh-m", text: "再见", audio_url: "http://x/b1.wav" },
    ],
  },
];

const subtitleData: SubtitleData = {
  scene_id: 1,
  srt_content: "1\n00:00:00,000 --> 00:00:01,500\n你好\n",
  segments: [
    { start: 0, end: 1.5, text: "你好" },
    { start: 1.5, end: 3, text: "世界" },
  ],
  language: "zh",
  srt_url: "http://x/1.srt",
};

function renderModal(patch: Partial<Parameters<typeof SubtitleModal>[0]> = {}) {
  const props = {
    voices,
    onClose: vi.fn(),
    onSuccess: vi.fn(),
    ...patch,
  };
  return { ...render(<SubtitleModal {...props} />), props };
}

describe("SubtitleModal（faster-whisper ASR 字幕生成）", () => {
  beforeEach(() => {
    useDramaStore.getState().reset();
    vi.clearAllMocks();
  });

  it("无配音时提示先生成配音，生成按钮禁用", () => {
    renderModal({ voices: [] });
    expect(screen.getByText("请先生成配音。")).toBeInTheDocument();
    expect(screen.getByText("生成字幕").closest("button")).toBeDisabled();
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
  });

  it("默认选中首个场景：场景/语言下拉、音频条数与首条音频预览", () => {
    renderModal();
    expect(screen.getByText("生成字幕（faster-whisper ASR）")).toBeInTheDocument();
    const [sceneSelect, langSelect] = screen.getAllByRole(
      "combobox"
    ) as HTMLSelectElement[];
    expect(sceneSelect.value).toBe("1");
    expect(sceneSelect.querySelectorAll("option").length).toBe(2);
    expect(screen.getByText(/场景 1（2 条语音）/)).toBeInTheDocument();
    expect(langSelect.value).toBe("zh");
    expect(screen.getByText("配音音频: 2 条")).toBeInTheDocument();
    const audio = document.querySelector("audio") as HTMLAudioElement;
    expect(audio.src).toBe("http://x/a1.wav");
  });

  it("切换场景与语言后按新值生成", async () => {
    mockGenerateSubtitle.mockResolvedValue({ success: true, data: subtitleData });
    const { props } = renderModal();
    const [sceneSelect, langSelect] = screen.getAllByRole("combobox");
    fireEvent.change(sceneSelect, { target: { value: "2" } });
    fireEvent.change(langSelect, { target: { value: "en" } });
    expect(screen.getByText("配音音频: 1 条")).toBeInTheDocument();
    fireEvent.click(screen.getByText("生成字幕"));
    await waitFor(() =>
      expect(mockGenerateSubtitle).toHaveBeenCalledWith({
        scene_id: 2,
        audio_url: "http://x/b1.wav",
        language: "en",
      })
    );
    await waitFor(() =>
      expect(props.onSuccess).toHaveBeenCalledWith(subtitleData)
    );
  });

  it("支持自动检测语言选项", async () => {
    mockGenerateSubtitle.mockResolvedValue({ success: true, data: subtitleData });
    renderModal();
    const [, langSelect] = screen.getAllByRole("combobox");
    fireEvent.change(langSelect, { target: { value: "auto" } });
    fireEvent.click(screen.getByText("生成字幕"));
    await waitFor(() =>
      expect(mockGenerateSubtitle).toHaveBeenCalledWith(
        expect.objectContaining({ language: "auto" })
      )
    );
  });

  it("后端返回失败：显示 error 字段", async () => {
    mockGenerateSubtitle.mockResolvedValue({
      success: false,
      error: "ASR 服务离线",
    });
    const { props } = renderModal();
    fireEvent.click(screen.getByText("生成字幕"));
    await screen.findByText("ASR 服务离线");
    expect(props.onSuccess).not.toHaveBeenCalled();
  });

  it("后端返回失败且无 error 字段：显示默认「生成失败」", async () => {
    mockGenerateSubtitle.mockResolvedValue({ success: false });
    renderModal();
    fireEvent.click(screen.getByText("生成字幕"));
    await screen.findByText("生成失败");
  });

  it("请求异常：错误写入错误区", async () => {
    mockGenerateSubtitle.mockRejectedValue(new Error("网络中断"));
    renderModal();
    fireEvent.click(screen.getByText("生成字幕"));
    await screen.findByText(/Error: 网络中断/);
  });

  it("生成中按钮禁用并显示加载态", async () => {
    let resolveGen: (v: { success: boolean; data: SubtitleData }) => void = () => {};
    mockGenerateSubtitle.mockReturnValue(
      new Promise((r) => {
        resolveGen = r;
      })
    );
    const { container } = renderModal();
    fireEvent.click(screen.getByText("生成字幕"));
    await waitFor(() =>
      expect(container.querySelector("span.loading")).toBeInTheDocument()
    );
    expect(container.querySelector("span.loading")!.closest("button")).toBeDisabled();
    resolveGen({ success: true, data: subtitleData });
    await waitFor(() =>
      expect(container.querySelector("span.loading")).not.toBeInTheDocument()
    );
  });

  it("已有字幕的场景：渲染可编辑分段，编辑文本写回 store", () => {
    useDramaStore.getState().addSubtitle(subtitleData);
    renderModal();
    expect(
      screen.getByText(/字幕编辑（2 段，文本可编辑）/)
    ).toBeInTheDocument();
    expect(screen.getByText("0.0s → 1.5s")).toBeInTheDocument();
    expect(screen.getByText("1.5s → 3.0s")).toBeInTheDocument();
    fireEvent.change(screen.getByDisplayValue("你好"), {
      target: { value: "你好呀" },
    });
    const sub = useDramaStore.getState().subtitles.find((s) => s.scene_id === 1);
    expect(sub?.segments[0].text).toBe("你好呀");
    expect(sub?.srt_content).toContain("你好呀");
  });

  it("切换到无字幕场景：编辑区隐藏", () => {
    useDramaStore.getState().addSubtitle(subtitleData);
    renderModal();
    const [sceneSelect] = screen.getAllByRole("combobox");
    fireEvent.change(sceneSelect, { target: { value: "2" } });
    expect(screen.queryByText(/字幕编辑/)).not.toBeInTheDocument();
  });

  it("场景无音频：不渲染 audio，生成按钮禁用", () => {
    renderModal({
      voices: [{ scene_id: 3, total_lines: 0, audio_urls: [] }],
    });
    expect(screen.getByText("配音音频: 0 条")).toBeInTheDocument();
    expect(document.querySelector("audio")).not.toBeInTheDocument();
    expect(screen.getByText("生成字幕").closest("button")).toBeDisabled();
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
