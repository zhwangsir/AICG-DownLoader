import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QualityModal } from "./QualityModal";
import { checkQuality, applySubtitleFix } from "../../api/client";
import type {
  QualityCheckData,
  ScriptData,
  SubtitleData,
  SubtitleFixResult,
} from "../../api/client";
import { useDramaStore } from "../../store/useDramaStore";

// Mock 质检与字幕修正 API；store 使用真实 zustand（beforeEach reset）
vi.mock("../../api/client", () => ({
  checkQuality: vi.fn(),
  applySubtitleFix: vi.fn(),
}));

const mockCheckQuality = vi.mocked(checkQuality);
const mockApplySubtitleFix = vi.mocked(applySubtitleFix);

function makeScript(patch: Partial<ScriptData> = {}): ScriptData {
  return {
    project_id: "p1",
    title: "测试短剧",
    genre: "都市悬疑",
    aspect_ratio: "9:16",
    total_episodes: 1,
    characters: [
      {
        character_id: "c1",
        name: "Alice",
        role: "主角",
        age: 26,
        description: "",
        personality: "",
      },
    ],
    scenes: [
      {
        scene_id: 1,
        episode: 1,
        shot_type: "中景",
        description: "开场",
        prompt: "",
        negative_prompt: "",
        character_actions: "",
        dialogue: "你好",
        emotion: "neutral",
        duration_seconds: 5,
        camera_movement: "static",
      },
      {
        scene_id: 2,
        episode: 1,
        shot_type: "特写",
        description: "收尾",
        prompt: "",
        negative_prompt: "",
        character_actions: "",
        dialogue: "再见",
        emotion: "neutral",
        duration_seconds: 5,
        camera_movement: "static",
      },
    ],
    ...patch,
  };
}

const subtitle1: SubtitleData = {
  scene_id: 1,
  srt_content: "1\n00:00:00,000 --> 00:00:02,000\n你好\n",
  segments: [{ start: 0, end: 2, text: "你好" }],
  language: "zh",
  srt_url: "http://x/1.srt",
};

/** 含三类严重级别 + 一条 subtitle 类 issue 的质检结果 */
function makeQualityData(patch: Partial<QualityCheckData> = {}): QualityCheckData {
  return {
    project_id: "p1",
    title: "测试短剧",
    score: 82,
    summary: "整体良好",
    issues: [
      {
        category: "logic",
        severity: "critical",
        scene_id: 1,
        message: "第1场逻辑矛盾",
        suggestion: "调整台词",
      },
      {
        category: "consistency",
        severity: "warning",
        scene_id: 2,
        message: "人设不一致",
        suggestion: "",
      },
      {
        category: "subtitle",
        severity: "info",
        scene_id: null,
        message: "字幕存在错别字",
        suggestion: "回写修正",
      },
    ],
    checked_at: 1700000000,
    ...patch,
  };
}

function renderModal(patch: Partial<Parameters<typeof QualityModal>[0]> = {}) {
  const onClose = vi.fn();
  const onSuccess = vi.fn();
  const utils = render(
    <QualityModal
      scriptData={makeScript()}
      subtitles={[subtitle1]}
      onClose={onClose}
      onSuccess={onSuccess}
      {...patch}
    />
  );
  return { onClose, onSuccess, ...utils };
}

describe("QualityModal（剧本质检）", () => {
  beforeEach(() => {
    useDramaStore.getState().reset();
    vi.clearAllMocks();
  });

  it("无剧本：提示先生成剧本，开始质检禁用；防御性点击不调用 API", () => {
    renderModal({ scriptData: null });
    expect(screen.getByText("请先生成剧本。")).toBeInTheDocument();
    const btn = screen.getByRole("button", { name: "开始质检" });
    expect(btn).toBeDisabled();
    fireEvent.click(btn);
    expect(mockCheckQuality).not.toHaveBeenCalled();
  });

  it("默认渲染：标题、场景数与字幕数摘要；无质检结果区块", () => {
    renderModal();
    expect(
      screen.getByText(/将对《测试短剧》进行台词一致性、剧情逻辑、敏感词检查。/)
    ).toBeInTheDocument();
    expect(screen.getByText(/检查场景数: 2 · 字幕数: 1/)).toBeInTheDocument();
    expect(screen.queryByText("已有质检结果")).not.toBeInTheDocument();
  });

  it("已有质检结果：分数/摘要/issues 渲染，严重级别着色，空建议不渲染建议行", () => {
    useDramaStore.getState().setQualityData(makeQualityData());
    renderModal();
    expect(screen.getByText("已有质检结果")).toBeInTheDocument();
    expect(screen.getByText("82")).toBeInTheDocument();
    expect(screen.getByText("整体良好")).toBeInTheDocument();
    // 严重级别着色
    expect((screen.getByText("[critical]") as HTMLElement).style.color).toBe(
      "rgb(170, 85, 85)"
    );
    expect((screen.getByText("[warning]") as HTMLElement).style.color).toBe(
      "rgb(170, 170, 85)"
    );
    expect(screen.getByText("[info]")).toBeInTheDocument();
    // suggestion 非空才渲染建议行（warning 条为空字符串 → 不渲染）
    expect(screen.getAllByText(/^建议: /).length).toBe(2);
  });

  it("issues 为空：渲染分数但不渲染问题列表", () => {
    useDramaStore.getState().setQualityData(makeQualityData({ issues: [] }));
    renderModal();
    expect(screen.getByText("已有质检结果")).toBeInTheDocument();
    expect(screen.queryByText(/^\[(critical|warning|info)\]$/)).not.toBeInTheDocument();
  });

  it("开始质检成功：payload 正确并回调 onSuccess", async () => {
    const data = makeQualityData({ score: 90 });
    mockCheckQuality.mockResolvedValue({
      success: true,
      data,
      error: null,
      elapsed_seconds: 1,
    });
    const { onSuccess } = renderModal();
    fireEvent.click(screen.getByRole("button", { name: "开始质检" }));
    await waitFor(() => expect(onSuccess).toHaveBeenCalledWith(data));
    expect(mockCheckQuality).toHaveBeenCalledWith({
      project_id: "p1",
      title: "测试短剧",
      characters: expect.any(Array),
      scenes: expect.any(Array),
      subtitles: [subtitle1],
    });
  });

  it("project_id 为空时回退生成 project-<时间戳>", async () => {
    mockCheckQuality.mockResolvedValue({
      success: true,
      data: makeQualityData(),
      error: null,
      elapsed_seconds: 1,
    });
    renderModal({ scriptData: makeScript({ project_id: "" }) });
    fireEvent.click(screen.getByRole("button", { name: "开始质检" }));
    await waitFor(() => expect(mockCheckQuality).toHaveBeenCalled());
    expect(mockCheckQuality.mock.calls[0][0].project_id).toMatch(/^project-\d+$/);
  });

  it("质检失败：展示后端 error", async () => {
    mockCheckQuality.mockResolvedValue({
      success: false,
      data: null,
      error: "LLM 超时",
      elapsed_seconds: 1,
    });
    renderModal();
    fireEvent.click(screen.getByRole("button", { name: "开始质检" }));
    expect(await screen.findByText("LLM 超时")).toBeInTheDocument();
  });

  it("质检失败：无 error 回退默认文案「质检失败」", async () => {
    mockCheckQuality.mockResolvedValue({
      success: false,
      data: null,
      error: null,
      elapsed_seconds: 1,
    });
    renderModal();
    fireEvent.click(screen.getByRole("button", { name: "开始质检" }));
    expect(await screen.findByText("质检失败")).toBeInTheDocument();
  });

  it("质检异常：catch 分支展示异常字符串", async () => {
    mockCheckQuality.mockRejectedValue(new Error("boom"));
    renderModal();
    fireEvent.click(screen.getByRole("button", { name: "开始质检" }));
    expect(await screen.findByText("Error: boom")).toBeInTheDocument();
  });

  it("质检 loading 态：按钮禁用并显示加载指示", async () => {
    let resolveFn!: (v: Awaited<ReturnType<typeof checkQuality>>) => void;
    mockCheckQuality.mockReturnValue(
      new Promise((r) => {
        resolveFn = r;
      })
    );
    const { onSuccess } = renderModal();
    const btn = screen.getByRole("button", { name: "开始质检" });
    fireEvent.click(btn);
    expect(btn).toBeDisabled();
    expect(document.querySelector(".loading")).toBeInTheDocument();
    resolveFn({ success: true, data: makeQualityData(), error: null, elapsed_seconds: 1 });
    await waitFor(() => expect(onSuccess).toHaveBeenCalled());
    expect(btn).not.toBeDisabled();
  });

  it("无字幕类 issue：不渲染字幕修正区", () => {
    useDramaStore
      .getState()
      .setQualityData(
        makeQualityData({
          issues: [
            {
              category: "logic",
              severity: "warning",
              scene_id: 1,
              message: "逻辑问题",
              suggestion: "",
            },
          ],
        })
      );
    renderModal();
    expect(screen.queryByText(/一键修正字幕错别字/)).not.toBeInTheDocument();
  });

  it("有字幕类 issue 但无字幕数据：不渲染字幕修正区", () => {
    useDramaStore.getState().setQualityData(makeQualityData());
    renderModal({ subtitles: [] });
    expect(screen.queryByText(/一键修正字幕错别字/)).not.toBeInTheDocument();
  });

  it("字幕修正成功：回写 store 字幕、状态栏提示、结果与修正对展示", async () => {
    useDramaStore.getState().setQualityData(makeQualityData());
    const fixed: SubtitleData = { ...subtitle1, srt_content: "修正后的SRT" };
    const fixResult: SubtitleFixResult = {
      fixed_subtitles: [fixed],
      corrections: [{ wrong: "的地", right: "的" }],
      fixed_count: 1,
      details: [],
      persisted_files: ["/data/1.srt"],
    };
    mockApplySubtitleFix.mockResolvedValue({
      success: true,
      data: fixResult,
      error: null,
      elapsed_seconds: 0.5,
    });
    renderModal();
    fireEvent.click(screen.getByText("一键修正字幕错别字"));
    await waitFor(() =>
      expect(useDramaStore.getState().subtitles[0]?.srt_content).toBe("修正后的SRT")
    );
    // API 参数
    expect(mockApplySubtitleFix).toHaveBeenCalledWith({
      subtitles: [subtitle1],
      issues: makeQualityData().issues,
      persist: true,
    });
    // 状态栏
    expect(useDramaStore.getState().statusInfo).toContain(
      "字幕修正完成: 1 段已修正，1 个错别字已回写 SRT"
    );
    // 结果展示
    expect(screen.getByText(/已修正 1 段字幕，提取 1 个错别字/)).toBeInTheDocument();
    expect(screen.getByText("的地 → 的")).toBeInTheDocument();
    expect(screen.getByText("已回写 1 个 SRT 文件")).toBeInTheDocument();
  });

  it("修正结果为零修正对且无回写文件：不渲染修正对与回写区块", async () => {
    useDramaStore.getState().setQualityData(makeQualityData());
    mockApplySubtitleFix.mockResolvedValue({
      success: true,
      data: {
        fixed_subtitles: [],
        corrections: [],
        fixed_count: 0,
        details: [],
        persisted_files: [],
      } satisfies SubtitleFixResult,
      error: null,
      elapsed_seconds: 0.5,
    });
    renderModal();
    fireEvent.click(screen.getByText("一键修正字幕错别字"));
    expect(
      await screen.findByText(/已修正 0 段字幕，提取 0 个错别字/)
    ).toBeInTheDocument();
    expect(screen.queryByText(/→/)).not.toBeInTheDocument();
    expect(screen.queryByText(/已回写 \d+ 个 SRT 文件/)).not.toBeInTheDocument();
  });

  it("字幕修正失败：展示后端 error", async () => {
    useDramaStore.getState().setQualityData(makeQualityData());
    mockApplySubtitleFix.mockResolvedValue({
      success: false,
      data: null,
      error: "SRT 回写失败",
      elapsed_seconds: 0.5,
    });
    renderModal();
    fireEvent.click(screen.getByText("一键修正字幕错别字"));
    expect(await screen.findByText("SRT 回写失败")).toBeInTheDocument();
  });

  it("字幕修正失败：无 error 回退默认文案；异常分支展示异常字符串", async () => {
    useDramaStore.getState().setQualityData(makeQualityData());
    mockApplySubtitleFix.mockResolvedValue({
      success: false,
      data: null,
      error: null,
      elapsed_seconds: 0.5,
    });
    const { unmount } = renderModal();
    fireEvent.click(screen.getByText("一键修正字幕错别字"));
    expect(await screen.findByText("字幕修正失败")).toBeInTheDocument();
    unmount();

    useDramaStore.getState().setQualityData(makeQualityData());
    mockApplySubtitleFix.mockRejectedValue(new Error("disk full"));
    renderModal();
    fireEvent.click(screen.getByText("一键修正字幕错别字"));
    expect(await screen.findByText("Error: disk full")).toBeInTheDocument();
  });

  it("修正 loading 态：按钮禁用并显示加载指示", async () => {
    useDramaStore.getState().setQualityData(makeQualityData());
    let resolveFn!: (v: Awaited<ReturnType<typeof applySubtitleFix>>) => void;
    mockApplySubtitleFix.mockReturnValue(
      new Promise((r) => {
        resolveFn = r;
      })
    );
    renderModal();
    const btn = screen.getByText("一键修正字幕错别字").closest("button")!;
    fireEvent.click(btn);
    expect(btn).toBeDisabled();
    expect(document.querySelector(".loading")).toBeInTheDocument();
    resolveFn({
      success: true,
      data: {
        fixed_subtitles: [],
        corrections: [],
        fixed_count: 0,
        details: [],
        persisted_files: [],
      },
      error: null,
      elapsed_seconds: 0.5,
    });
    await waitFor(() => expect(btn).not.toBeDisabled());
  });

  it("再次开始质检时清空上次修正结果", async () => {
    useDramaStore.getState().setQualityData(makeQualityData());
    mockApplySubtitleFix.mockResolvedValue({
      success: true,
      data: {
        fixed_subtitles: [],
        corrections: [{ wrong: "的地", right: "的" }],
        fixed_count: 2,
        details: [],
        persisted_files: ["/a.srt"],
      },
      error: null,
      elapsed_seconds: 0.5,
    });
    const { onSuccess } = renderModal();
    fireEvent.click(screen.getByText("一键修正字幕错别字"));
    expect(await screen.findByText(/已修正 2 段字幕/)).toBeInTheDocument();
    // 再次质检 → handleCheck 起始即清空 fixResult
    mockCheckQuality.mockResolvedValue({
      success: true,
      data: makeQualityData(),
      error: null,
      elapsed_seconds: 1,
    });
    fireEvent.click(screen.getByRole("button", { name: "开始质检" }));
    await waitFor(() => expect(onSuccess).toHaveBeenCalled());
    expect(screen.queryByText(/已修正 \d+ 段字幕/)).not.toBeInTheDocument();
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
