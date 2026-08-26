import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { vi } from "vitest";
import CharacterPreviewPanel from "./CharacterPreviewPanel";
import { useDramaStore, type CharacterPreviewData } from "../store/useDramaStore";
import type { CharacterData, ScriptData } from "../api/client";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    previewCharacter: vi.fn(),
    generateCharacter: vi.fn(),
  };
});

import { previewCharacter, generateCharacter } from "../api/client";
const mockPreview = vi.mocked(previewCharacter);
const mockGenerate = vi.mocked(generateCharacter);

const character: CharacterData = {
  character_id: "c1",
  name: "林远",
  role: "主角",
  age: 26,
  description: "高大，眼神冷峻",
  personality: "冷静克制",
};

const character2: CharacterData = {
  character_id: "c2",
  name: "苏晚晴",
  role: "反派",
  age: null,
  description: "美艳",
  personality: "偏执",
};

function makeScript(chars: CharacterData[] = [character]): ScriptData {
  return {
    project_id: "p1",
    title: "测试短剧",
    genre: "都市情感",
    aspect_ratio: "9:16",
    total_episodes: 1,
    characters: chars,
    scenes: [],
  };
}

const previewPrompts = {
  front_view_prompt: "front view prompt text",
  side_view_prompt: "side view prompt text",
  closeup_prompt: "closeup prompt text",
  negative_prompt: "negative prompt text",
};

function makePreviewData(patch: Partial<CharacterPreviewData> = {}): CharacterPreviewData {
  return {
    character_id: character.character_id,
    character,
    style: "写实电影感",
    searchReference: "",
    generatedPrompts: { front_view_prompt: "", side_view_prompt: "", closeup_prompt: "", negative_prompt: "" },
    editedPrompts: { front_view_prompt: "", side_view_prompt: "", closeup_prompt: "", negative_prompt: "" },
    stage: "idle",
    ...patch,
  };
}

function deferred<T>() {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

const successResp = {
  success: true,
  data: {
    character_id: "c1",
    character,
    style: "写实电影感",
    search_reference: "联网调研参考资料",
    prompts: previewPrompts,
  },
};

function getPromptTextarea(label: string): HTMLTextAreaElement {
  const labelEl = screen.getByText(label);
  return labelEl.nextElementSibling as HTMLTextAreaElement;
}

describe("CharacterPreviewPanel", () => {
  beforeEach(() => {
    useDramaStore.getState().reset();
    vi.clearAllMocks();
  });

  it("角色不存在：显示「未找到角色信息」，点击关闭触发 onClose", () => {
    useDramaStore.getState().setScriptData(makeScript());
    const onClose = vi.fn();
    render(<CharacterPreviewPanel characterId="nope" onClose={onClose} />);
    expect(screen.getByText("未找到角色信息")).toBeInTheDocument();
    fireEvent.click(screen.getByText("关闭"));
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(mockPreview).not.toHaveBeenCalled();
  });

  it("挂载后自动开始 AI 调研：searching 态 spinner，完成后进入可编辑态并回填提示词", async () => {
    const d = deferred<typeof successResp>();
    mockPreview.mockReturnValue(d.promise);
    useDramaStore.getState().setScriptData(makeScript());
    render(<CharacterPreviewPanel characterId="c1" onClose={vi.fn()} />);

    // searching 中间态
    expect(screen.getByText("AI 联网调研中")).toBeInTheDocument();
    expect(screen.getByText(/正在联网搜索相关参考资料/)).toBeInTheDocument();
    expect(mockPreview).toHaveBeenCalledWith({ character, style: "写实电影感" });
    // 提示词编辑区在搜索中禁用
    expect(getPromptTextarea("正面提示词（三视图共用）")).toBeDisabled();
    // 搜索中不可点击生成
    expect(screen.getByText("确认生成定妆照")).toBeDisabled();

    await act(async () => d.resolve(successResp));
    await waitFor(() => expect(screen.getByText("可编辑确认")).toBeInTheDocument());
    expect(screen.getByText("联网调研参考资料")).toBeInTheDocument();
    expect(getPromptTextarea("正面提示词（三视图共用）").value).toBe("front view prompt text");
    expect(getPromptTextarea("反向提示词").value).toBe("negative prompt text");
    expect(useDramaStore.getState().statusInfo).toContain("林远");
    expect(useDramaStore.getState().statusInfo).toContain("AI 调研完成");
  });

  it("调研部分失败（success 但带 error）：状态提示包含错误信息", async () => {
    mockPreview.mockResolvedValue({ ...successResp, error: "联网受限，已本地生成" });
    useDramaStore.getState().setScriptData(makeScript());
    render(<CharacterPreviewPanel characterId="c1" onClose={vi.fn()} />);
    await waitFor(() => expect(screen.getByText("可编辑确认")).toBeInTheDocument());
    expect(useDramaStore.getState().statusInfo).toContain("联网受限");
  });

  it("调研失败（success:false）：进入编辑态并展示错误", async () => {
    mockPreview.mockResolvedValue({ success: false, error: "LLM 超时" });
    useDramaStore.getState().setScriptData(makeScript());
    render(<CharacterPreviewPanel characterId="c1" onClose={vi.fn()} />);
    await waitFor(() => expect(screen.getByText("LLM 超时")).toBeInTheDocument());
    expect(screen.getByText("可编辑确认")).toBeInTheDocument();
  });

  it("调研失败且无 error 字段：使用默认「搜索失败」", async () => {
    mockPreview.mockResolvedValue({ success: false });
    useDramaStore.getState().setScriptData(makeScript());
    render(<CharacterPreviewPanel characterId="c1" onClose={vi.fn()} />);
    await waitFor(() => expect(screen.getByText("搜索失败")).toBeInTheDocument());
  });

  it("调研抛异常：错误消息展示在面板", async () => {
    mockPreview.mockRejectedValue(new Error("网络中断"));
    useDramaStore.getState().setScriptData(makeScript());
    render(<CharacterPreviewPanel characterId="c1" onClose={vi.fn()} />);
    await waitFor(() => expect(screen.getByText("网络中断")).toBeInTheDocument());
  });

  it("调研成功但 search_reference 为空：显示暂无搜索资料提示", async () => {
    mockPreview.mockResolvedValue({
      ...successResp,
      data: { ...successResp.data, search_reference: "" },
    });
    useDramaStore.getState().setScriptData(makeScript());
    render(<CharacterPreviewPanel characterId="c1" onClose={vi.fn()} />);
    await waitFor(() =>
      expect(screen.getByText(/暂无搜索资料/)).toBeInTheDocument()
    );
  });

  it("已有 completed 预览：不重复调研，显示生成完成与「重新生成」", () => {
    useDramaStore.getState().setScriptData(makeScript());
    useDramaStore.getState().setCharacterPreview("c1", makePreviewData({ stage: "completed", searchReference: "旧资料" }));
    render(<CharacterPreviewPanel characterId="c1" onClose={vi.fn()} />);
    expect(mockPreview).not.toHaveBeenCalled();
    expect(screen.getByText("生成完成")).toBeInTheDocument();
    expect(screen.getByText("重新生成")).toBeInTheDocument();
    expect(screen.getByText("旧资料")).toBeInTheDocument();
  });

  it("已有 editing+error 预览：自动重新调研", async () => {
    mockPreview.mockResolvedValue(successResp);
    useDramaStore.getState().setScriptData(makeScript());
    useDramaStore.getState().setCharacterPreview("c1", makePreviewData({ stage: "editing", error: "旧错误" }));
    render(<CharacterPreviewPanel characterId="c1" onClose={vi.fn()} />);
    await waitFor(() => expect(mockPreview).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.getByText("可编辑确认")).toBeInTheDocument());
    expect(screen.queryByText("旧错误")).not.toBeInTheDocument();
  });

  it("搜索结果返回前预览已被外部修改：过期结果被丢弃", async () => {
    const d = deferred<typeof successResp>();
    mockPreview.mockReturnValue(d.promise);
    useDramaStore.getState().setScriptData(makeScript());
    render(<CharacterPreviewPanel characterId="c1" onClose={vi.fn()} />);
    await waitFor(() => expect(mockPreview).toHaveBeenCalled());
    // 外部把阶段改回 editing（如用户取消），搜索结果应被判定过期
    act(() => {
      useDramaStore.getState().updateCharacterPreview("c1", { stage: "editing" });
    });
    await act(async () => d.resolve(successResp));
    const p = useDramaStore.getState().characterPreviews["c1"];
    expect(p.searchReference).toBe("");
    expect(p.stage).toBe("editing");
  });

  it("搜索抛异常前预览已过期：异常被静默丢弃", async () => {
    const d = deferred<typeof successResp>();
    mockPreview.mockReturnValue(d.promise);
    useDramaStore.getState().setScriptData(makeScript());
    render(<CharacterPreviewPanel characterId="c1" onClose={vi.fn()} />);
    await waitFor(() => expect(mockPreview).toHaveBeenCalled());
    act(() => {
      useDramaStore.getState().updateCharacterPreview("c1", { stage: "editing" });
    });
    await act(async () => d.reject(new Error("超时")));
    const p = useDramaStore.getState().characterPreviews["c1"];
    expect(p.error).toBeUndefined();
  });

  it("角色信息本地编辑：名称/身份/年龄/外貌/性格", async () => {
    mockPreview.mockResolvedValue(successResp);
    useDramaStore.getState().setScriptData(makeScript());
    render(<CharacterPreviewPanel characterId="c1" onClose={vi.fn()} />);
    await waitFor(() => expect(screen.getByText("可编辑确认")).toBeInTheDocument());

    const nameInput = screen.getByDisplayValue("林远");
    fireEvent.change(nameInput, { target: { value: "林远山" } });
    expect((nameInput as HTMLInputElement).value).toBe("林远山");

    const roleInput = screen.getByDisplayValue("主角");
    fireEvent.change(roleInput, { target: { value: "反派" } });
    expect((roleInput as HTMLInputElement).value).toBe("反派");

    const ageInput = screen.getByDisplayValue("26");
    fireEvent.change(ageInput, { target: { value: "" } });
    expect((ageInput as HTMLInputElement).value).toBe("");
    fireEvent.change(ageInput, { target: { value: "40" } });
    expect((ageInput as HTMLInputElement).value).toBe("40");

    fireEvent.change(screen.getByDisplayValue("高大，眼神冷峻"), { target: { value: "新外貌" } });
    fireEvent.change(screen.getByDisplayValue("冷静克制"), { target: { value: "新性格" } });
    expect(screen.getByDisplayValue("新外貌")).toBeInTheDocument();
    expect(screen.getByDisplayValue("新性格")).toBeInTheDocument();
  });

  it("提示词编辑写回 store.editedPrompts", async () => {
    mockPreview.mockResolvedValue(successResp);
    useDramaStore.getState().setScriptData(makeScript());
    render(<CharacterPreviewPanel characterId="c1" onClose={vi.fn()} />);
    await waitFor(() => expect(screen.getByText("可编辑确认")).toBeInTheDocument());
    fireEvent.change(getPromptTextarea("正面提示词（三视图共用）"), { target: { value: "改后正面" } });
    fireEvent.change(getPromptTextarea("反向提示词"), { target: { value: "改后反向" } });
    const p = useDramaStore.getState().characterPreviews["c1"];
    expect(p.editedPrompts.front_view_prompt).toBe("改后正面");
    expect(p.editedPrompts.negative_prompt).toBe("改后反向");
  });

  it("确认生成成功：调用 generateCharacter（携带编辑后提示词）、入角色卡、进入完成态", async () => {
    mockPreview.mockResolvedValue(successResp);
    const card = {
      character_id: "c1",
      name: "林远",
      reference_images: { front: "/static/f.png" },
      consistency_level: "L3",
    };
    mockGenerate.mockResolvedValue({ success: true, data: card });
    useDramaStore.getState().setScriptData(makeScript());
    render(<CharacterPreviewPanel characterId="c1" onClose={vi.fn()} />);
    await waitFor(() => expect(screen.getByText("可编辑确认")).toBeInTheDocument());

    // 本地改名 + 编辑提示词后生成
    fireEvent.change(screen.getByDisplayValue("林远"), { target: { value: "林远山" } });
    fireEvent.change(getPromptTextarea("正面提示词（三视图共用）"), { target: { value: "最终正面" } });
    fireEvent.click(screen.getByText("确认生成定妆照"));

    await waitFor(() => expect(screen.getByText("生成完成")).toBeInTheDocument());
    expect(mockGenerate).toHaveBeenCalledWith(
      expect.objectContaining({
        style: "写实电影感",
        consistency_level: "L3",
        preview_positive_prompt: "最终正面",
        preview_negative_prompt: "negative prompt text",
      })
    );
    // 本地编辑的角色名已写回 store
    expect(useDramaStore.getState().scriptData?.characters[0].name).toBe("林远山");
    expect(useDramaStore.getState().characterCards).toHaveLength(1);
    expect(useDramaStore.getState().statusInfo).toContain("定妆照生成完成");
    expect(useDramaStore.getState().globalLoading).toBe(false);
    expect(screen.getByText("重新生成")).toBeInTheDocument();
  });

  it("生成失败（success:false）：回到编辑态并展示错误", async () => {
    mockPreview.mockResolvedValue(successResp);
    mockGenerate.mockResolvedValue({ success: false, error: "ComfyUI 离线" });
    useDramaStore.getState().setScriptData(makeScript());
    render(<CharacterPreviewPanel characterId="c1" onClose={vi.fn()} />);
    await waitFor(() => expect(screen.getByText("可编辑确认")).toBeInTheDocument());
    fireEvent.click(screen.getByText("确认生成定妆照"));
    await waitFor(() => expect(screen.getByText("ComfyUI 离线")).toBeInTheDocument());
    expect(screen.getByText("可编辑确认")).toBeInTheDocument();
    expect(useDramaStore.getState().globalLoading).toBe(false);
  });

  it("生成失败且无 error 字段：默认「生成失败」", async () => {
    mockPreview.mockResolvedValue(successResp);
    mockGenerate.mockResolvedValue({ success: false });
    useDramaStore.getState().setScriptData(makeScript());
    render(<CharacterPreviewPanel characterId="c1" onClose={vi.fn()} />);
    await waitFor(() => expect(screen.getByText("可编辑确认")).toBeInTheDocument());
    fireEvent.click(screen.getByText("确认生成定妆照"));
    await waitFor(() => expect(screen.getByText("生成失败")).toBeInTheDocument());
  });

  it("生成抛异常：错误展示且全局 loading 复位", async () => {
    mockPreview.mockResolvedValue(successResp);
    mockGenerate.mockRejectedValue(new Error("连接重置"));
    useDramaStore.getState().setScriptData(makeScript());
    render(<CharacterPreviewPanel characterId="c1" onClose={vi.fn()} />);
    await waitFor(() => expect(screen.getByText("可编辑确认")).toBeInTheDocument());
    fireEvent.click(screen.getByText("确认生成定妆照"));
    await waitFor(() => expect(screen.getByText("连接重置")).toBeInTheDocument());
    expect(useDramaStore.getState().globalLoading).toBe(false);
  });

  it("generating 阶段：收起/取消按钮禁用", () => {
    useDramaStore.getState().setScriptData(makeScript());
    useDramaStore.getState().setCharacterPreview("c1", makePreviewData({ stage: "generating" }));
    render(<CharacterPreviewPanel characterId="c1" onClose={vi.fn()} />);
    expect(screen.getByText("正在生成定妆照")).toBeInTheDocument();
    expect(screen.getByText("收起")).toBeDisabled();
    expect(screen.getByText("取消")).toBeDisabled();
    expect(screen.getByText("确认生成定妆照")).toBeDisabled();
  });

  it("全局 loading 时生成按钮禁用", () => {
    useDramaStore.getState().setScriptData(makeScript());
    useDramaStore.getState().setCharacterPreview("c1", makePreviewData({ stage: "editing" }));
    useDramaStore.getState().startGlobalLoading("其他任务中");
    render(<CharacterPreviewPanel characterId="c1" onClose={vi.fn()} />);
    expect(screen.getByText("确认生成定妆照")).toBeDisabled();
  });

  it("idle 阶段（无预览时的兜底态）：显示等待开始与剧本元信息", () => {
    // 预置 searching 预览阻止自动调研，再切回 idle
    useDramaStore.getState().setScriptData(makeScript());
    useDramaStore.getState().setCharacterPreview("c1", makePreviewData({ stage: "searching" }));
    const { rerender } = render(<CharacterPreviewPanel characterId="c1" onClose={vi.fn()} />);
    expect(mockPreview).not.toHaveBeenCalled();
    act(() => {
      useDramaStore.getState().updateCharacterPreview("c1", { stage: "idle" });
    });
    rerender(<CharacterPreviewPanel characterId="c1" onClose={vi.fn()} />);
    expect(screen.getByText("等待开始")).toBeInTheDocument();
    expect(screen.getByText("测试短剧")).toBeInTheDocument();
    expect(screen.getByText(/题材：都市情感/)).toBeInTheDocument();
    // idle 态可点击生成（canGenerate 包含 idle）
    expect(screen.getByText("确认生成定妆照")).not.toBeDisabled();
  });

  it("点击收起/取消触发 onClose", () => {
    useDramaStore.getState().setScriptData(makeScript());
    useDramaStore.getState().setCharacterPreview("c1", makePreviewData({ stage: "editing" }));
    const onClose = vi.fn();
    render(<CharacterPreviewPanel characterId="c1" onClose={onClose} />);
    fireEvent.click(screen.getByText("收起"));
    fireEvent.click(screen.getByText("取消"));
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it("同一组件实例切换角色后不重新调研（hasStartedSearchRef 不重置）", async () => {
    mockPreview.mockResolvedValue(successResp);
    useDramaStore.getState().setScriptData(makeScript([character, character2]));
    const { rerender } = render(<CharacterPreviewPanel characterId="c1" onClose={vi.fn()} />);
    await waitFor(() => expect(mockPreview).toHaveBeenCalledTimes(1));
    rerender(<CharacterPreviewPanel characterId="c2" onClose={vi.fn()} />);
    // c2 信息正常展示，但不会触发第二次调研
    await waitFor(() => expect(screen.getByDisplayValue("苏晚晴")).toBeInTheDocument());
    expect(mockPreview).toHaveBeenCalledTimes(1);
  });
});
