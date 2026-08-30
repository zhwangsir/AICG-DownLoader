import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { vi } from "vitest";
import { ScriptModal } from "./ScriptModal";
import { useDramaStore } from "../../store/useDramaStore";
import { generateScript, getPipelineTemplates } from "../../api/client";
import type {
  AgentResponse,
  CharacterData,
  PipelineTemplateItem,
  SceneData,
  ScriptData,
} from "../../api/client";

vi.mock("../../api/client", () => ({
  generateScript: vi.fn(),
  getPipelineTemplates: vi.fn(),
}));

const mockGenerateScript = vi.mocked(generateScript);
const mockGetPipelineTemplates = vi.mocked(getPipelineTemplates);

const okResp = <T,>(data: T): AgentResponse<T> => ({
  success: true,
  data,
  error: null,
  elapsed_seconds: 1,
});
const errResp = (error: string | null): AgentResponse<ScriptData> => ({
  success: false,
  data: null,
  error,
  elapsed_seconds: 1,
});

const sampleCharacter: CharacterData = {
  character_id: "c1",
  name: "Alice",
  role: "主角",
  age: 26,
  description: "外卖员",
  personality: "冷静",
};

const sampleScene: SceneData = {
  scene_id: 1,
  episode: 1,
  shot_type: "中景",
  description: "雨夜开场",
  prompt: "rainy night",
  negative_prompt: "blur",
  character_actions: "",
  dialogue: "你点的餐到了",
  emotion: "紧张",
  duration_seconds: 5,
  camera_movement: "推镜头",
};

const sampleScript: ScriptData = {
  project_id: "p1",
  title: "夜雨外卖",
  genre: "都市悬疑",
  aspect_ratio: "9:16",
  total_episodes: 1,
  characters: [sampleCharacter],
  scenes: [sampleScene],
};

function renderNew(props: Partial<Parameters<typeof ScriptModal>[0]> = {}) {
  const onClose = vi.fn();
  const onSuccess = vi.fn();
  const onUpdate = vi.fn();
  const utils = render(
    <ScriptModal
      scriptData={null}
      onClose={onClose}
      onSuccess={onSuccess}
      onUpdate={onUpdate}
      {...props}
    />
  );
  return { onClose, onSuccess, onUpdate, ...utils };
}

function renderEdit(props: Partial<Parameters<typeof ScriptModal>[0]> = {}) {
  const onClose = vi.fn();
  const onSuccess = vi.fn();
  const onUpdate = vi.fn();
  const utils = render(
    <ScriptModal
      scriptData={sampleScript}
      onClose={onClose}
      onSuccess={onSuccess}
      onUpdate={onUpdate}
      {...props}
    />
  );
  return { onClose, onSuccess, onUpdate, ...utils };
}

/** 通过 label 文本定位同 .modal-field 内的输入控件 */
function fieldControl(label: string, selector = "input") {
  const field = screen.getByText(label).closest(".modal-field");
  expect(field).not.toBeNull();
  const control = field!.querySelector(selector);
  expect(control).not.toBeNull();
  return control as HTMLElement;
}

/** 场景行内控件：label 与控件同在父容器（非 .modal-field） */
function inlineControl(label: string, selector: string) {
  const labelEl = screen.getByText(label);
  const control = labelEl.parentElement!.querySelector(selector);
  expect(control).not.toBeNull();
  return control as HTMLElement;
}

describe("ScriptModal 新建模式", () => {
  beforeEach(() => {
    useDramaStore.getState().reset();
    vi.clearAllMocks();
    // 默认无模板可选（模板库为空），避免影响既有断言
    mockGetPipelineTemplates.mockResolvedValue({ templates: [], total: 0, categories: [] });
  });

  it("默认渲染：创意/题材/集数/每集分镜数默认值，无「返回编辑模式」", () => {
    renderNew();
    expect(screen.getByText("生成剧本")).toBeInTheDocument();
    const premise = screen.getByPlaceholderText("输入你的创意...") as HTMLInputElement;
    expect(premise.value).toBe("都市悬疑，外卖员发现客户是凶手");
    expect((fieldControl("题材") as HTMLInputElement).value).toBe("都市悬疑");
    expect((fieldControl("集数") as HTMLInputElement).value).toBe("1");
    expect((fieldControl("每集分镜数") as HTMLInputElement).value).toBe("5");
    expect(screen.queryByText("返回编辑模式")).not.toBeInTheDocument();
  });

  it("AgentBar 创意草稿预填并「读后即清」", () => {
    useDramaStore.getState().setDraftPremise("科幻未来，AI 觉醒");
    renderNew();
    const premise = screen.getByPlaceholderText("输入你的创意...") as HTMLInputElement;
    expect(premise.value).toBe("科幻未来，AI 觉醒");
    // 挂载后 draftPremise 被清空
    expect(useDramaStore.getState().draftPremise).toBe("");
  });

  it("生成成功：自定义参数透传 payload 并回调 onSuccess（含长文本创意）", async () => {
    const longPremise = "悬疑".repeat(300);
    mockGenerateScript.mockResolvedValue(okResp(sampleScript));
    const { onSuccess } = renderNew();
    fireEvent.change(screen.getByPlaceholderText("输入你的创意..."), {
      target: { value: longPremise },
    });
    fireEvent.change(fieldControl("题材"), { target: { value: "科幻未来" } });
    fireEvent.change(fieldControl("集数"), { target: { value: "3" } });
    fireEvent.change(fieldControl("每集分镜数"), { target: { value: "7" } });
    fireEvent.click(screen.getByText("生成"));
    await waitFor(() => expect(onSuccess).toHaveBeenCalledWith(sampleScript));
    expect(mockGenerateScript).toHaveBeenCalledWith({
      premise: longPremise,
      genre: "科幻未来",
      episodes: 3,
      scenes_per_episode: 7,
    });
  });

  it("生成中：按钮禁用并显示「生成中…」，结束后恢复", async () => {
    let resolve!: (v: AgentResponse<ScriptData>) => void;
    mockGenerateScript.mockReturnValue(
      new Promise((r) => {
        resolve = r;
      })
    );
    renderNew();
    fireEvent.click(screen.getByText("生成"));
    const btn = screen.getByText("生成中…").closest("button")!;
    expect(btn).toBeDisabled();
    resolve(okResp(sampleScript));
    await waitFor(() => expect(screen.getByText("生成")).not.toBeDisabled());
  });

  it("生成失败（resp.error）→ 展示后端错误消息", async () => {
    mockGenerateScript.mockResolvedValue(errResp("LLM 离线"));
    renderNew();
    fireEvent.click(screen.getByText("生成"));
    await waitFor(() => expect(screen.getByText("LLM 离线")).toBeInTheDocument());
  });

  it("生成失败（无 error 字段）→ 兜底「生成失败」", async () => {
    mockGenerateScript.mockResolvedValue(errResp(null));
    renderNew();
    fireEvent.click(screen.getByText("生成"));
    await waitFor(() => expect(screen.getByText("生成失败")).toBeInTheDocument());
  });

  it("生成抛异常 → String(e) 展示", async () => {
    mockGenerateScript.mockRejectedValue(new Error("网络超时"));
    renderNew();
    fireEvent.click(screen.getByText("生成"));
    await waitFor(() => expect(screen.getByText(/网络超时/)).toBeInTheDocument());
  });

  it("遮罩点击关闭；模态内部点击不关闭；取消按钮关闭", () => {
    const { onClose, container } = renderNew();
    fireEvent.click(screen.getByText("生成剧本"));
    expect(onClose).not.toHaveBeenCalled();
    fireEvent.click(container.querySelector(".modal-overlay")!);
    expect(onClose).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByText("取消"));
    expect(onClose).toHaveBeenCalledTimes(2);
  });
});

describe("ScriptModal 编辑模式", () => {
  beforeEach(() => {
    useDramaStore.getState().reset();
    useDramaStore.getState().setScriptData(sampleScript);
    vi.clearAllMocks();
    mockGetPipelineTemplates.mockResolvedValue({ templates: [], total: 0, categories: [] });
  });

  it("默认渲染：标题/题材/角色/场景初始值", () => {
    renderEdit();
    expect(screen.getByText("编辑剧本")).toBeInTheDocument();
    expect((fieldControl("标题") as HTMLInputElement).value).toBe("夜雨外卖");
    expect((fieldControl("题材") as HTMLInputElement).value).toBe("都市悬疑");
    expect(screen.getByText("角色列表（1）")).toBeInTheDocument();
    expect(screen.getByText("场景列表（1）")).toBeInTheDocument();
    expect((screen.getByPlaceholderText("姓名") as HTMLInputElement).value).toBe("Alice");
    expect((screen.getByPlaceholderText("年龄") as HTMLInputElement).value).toBe("26");
    expect((screen.getByPlaceholderText("场景描述") as HTMLTextAreaElement).value).toBe("雨夜开场");
    expect((screen.getByPlaceholderText("对白") as HTMLTextAreaElement).value).toBe("你点的餐到了");
  });

  it("全字段编辑 + 保存修改：写回 store、回调 onUpdate、已保存徽标 1.5s 后消失（多角色/多场景仅改首个）", async () => {
    vi.useFakeTimers();
    try {
      const richScript: ScriptData = {
        ...sampleScript,
        characters: [
          sampleCharacter,
          { ...sampleCharacter, character_id: "c2", name: "Carol", age: null },
        ],
        scenes: [sampleScene, { ...sampleScene, scene_id: 2, description: "第二场" }],
      };
      useDramaStore.getState().setScriptData(richScript);
      const { onUpdate } = renderEdit({ scriptData: richScript });
      // 角色字段（仅编辑第一个角色）
      fireEvent.change(screen.getAllByPlaceholderText("姓名")[0], { target: { value: "Bob" } });
      fireEvent.change(screen.getAllByPlaceholderText("定位")[0], { target: { value: "反派" } });
      fireEvent.change(screen.getAllByPlaceholderText("年龄")[0], { target: { value: "30" } });
      // 年龄清空 → null 分支
      fireEvent.change(screen.getAllByPlaceholderText("年龄")[0], { target: { value: "" } });
      fireEvent.change(screen.getAllByPlaceholderText("描述")[0], { target: { value: "新描述" } });
      fireEvent.change(screen.getAllByPlaceholderText("性格")[0], { target: { value: "暴躁" } });
      // 标题/题材
      fireEvent.change(fieldControl("标题"), { target: { value: "午夜订单" } });
      fireEvent.change(fieldControl("题材"), { target: { value: "犯罪推理" } });
      // 场景字段（仅编辑第一个场景）
      fireEvent.change(screen.getAllByPlaceholderText("场景描述")[0], { target: { value: "新场景" } });
      const inlineAt = (label: string, selector: string) =>
        screen.getAllByText(label)[0].parentElement!.querySelector(selector) as HTMLElement;
      fireEvent.change(inlineAt("景别", "select"), { target: { value: "特写" } });
      fireEvent.change(inlineAt("情绪", "select"), { target: { value: "愤怒" } });
      fireEvent.change(inlineAt("运镜", "select"), { target: { value: "拉镜头" } });
      fireEvent.change(inlineAt("时长(s)", "input"), { target: { value: "8" } });
      fireEvent.change(screen.getAllByPlaceholderText("正向提示词")[0], { target: { value: "new prompt" } });
      fireEvent.change(screen.getAllByPlaceholderText("反向提示词")[0], { target: { value: "new negative" } });
      fireEvent.change(screen.getAllByPlaceholderText("对白")[0], { target: { value: "新对白" } });

      fireEvent.click(screen.getByText("保存修改"));
      expect(screen.getByText("已保存")).toBeInTheDocument();
      expect(onUpdate).toHaveBeenCalledTimes(1);

      const saved = useDramaStore.getState().scriptData!;
      expect(saved.title).toBe("午夜订单");
      expect(saved.genre).toBe("犯罪推理");
      expect(saved.characters[0]).toMatchObject({
        name: "Bob",
        role: "反派",
        age: null,
        description: "新描述",
        personality: "暴躁",
      });
      expect(saved.scenes[0]).toMatchObject({
        description: "新场景",
        shot_type: "特写",
        emotion: "愤怒",
        camera_movement: "拉镜头",
        duration_seconds: 8,
        prompt: "new prompt",
        negative_prompt: "new negative",
        dialogue: "新对白",
      });
      // 第二个角色/场景未被波及
      expect(saved.characters[1].name).toBe("Carol");
      expect(saved.scenes[1].description).toBe("第二场");

      await act(async () => {
        await vi.advanceTimersByTimeAsync(1600);
      });
      expect(screen.queryByText("已保存")).not.toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it("时长输入非法值 → 归一为 0", () => {
    renderEdit();
    fireEvent.change(inlineControl("时长(s)", "input"), { target: { value: "abc" } });
    fireEvent.click(screen.getByText("保存修改"));
    expect(useDramaStore.getState().scriptData!.scenes[0].duration_seconds).toBe(0);
  });

  it("「重新生成」切换到新建模式并拼接创意；题材为空时兜底「都市悬疑」", () => {
    renderEdit();
    fireEvent.change(fieldControl("题材"), { target: { value: "" } });
    fireEvent.click(screen.getByText("重新生成"));
    expect(screen.getByText("生成剧本")).toBeInTheDocument();
    const premise = screen.getByPlaceholderText("输入你的创意...") as HTMLInputElement;
    expect(premise.value).toBe("，夜雨外卖");
    expect((fieldControl("题材") as HTMLInputElement).value).toBe("都市悬疑");
    // scriptData 存在 → 可返回编辑模式
    fireEvent.click(screen.getByText("返回编辑模式"));
    expect(screen.getByText("编辑剧本")).toBeInTheDocument();
  });

  it("编辑模式错误展示：新建模式生成失败后返回编辑模式仍可见错误", async () => {
    mockGenerateScript.mockResolvedValue(errResp("配额耗尽"));
    renderEdit();
    fireEvent.click(screen.getByText("重新生成"));
    fireEvent.click(screen.getByText("生成"));
    await waitFor(() => expect(screen.getByText("配额耗尽")).toBeInTheDocument());
    fireEvent.click(screen.getByText("返回编辑模式"));
    expect(screen.getByText("编辑剧本")).toBeInTheDocument();
    expect(screen.getByText("配额耗尽")).toBeInTheDocument();
  });

  it("编辑模式「关闭」按钮触发 onClose", () => {
    const { onClose } = renderEdit();
    fireEvent.click(screen.getByText("关闭"));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});

describe("ScriptModal M25.3 模板起手", () => {
  const tplA: PipelineTemplateItem = {
    id: "trope_boss_romance_confrontation",
    title: "霸总对峙/壁咚",
    category: "genre_trope",
    tags: ["霸总", "对峙"],
    summary: "CEO romance confrontation...",
    content: "CEO romance confrontation: tall male lead in tailored suit",
  };
  const tplB: PipelineTemplateItem = {
    id: "trope_sweet_cafe_date",
    title: "甜宠咖啡馆约会",
    category: "genre_trope",
    tags: ["甜宠", "约会"],
    summary: "sweet romance cafe date",
    content: "sweet romance cafe date: young couple sitting across a small table",
  };

  beforeEach(() => {
    useDramaStore.getState().reset();
    vi.clearAllMocks();
    mockGetPipelineTemplates.mockResolvedValue({
      templates: [tplA, tplB],
      total: 2,
      categories: ["genre_trope"],
    });
  });

  it("模板加载成功后渲染下拉选项（含默认「不使用模板」项）", async () => {
    renderNew();
    await waitFor(() =>
      expect(screen.getByText("模板起手（可选）")).toBeInTheDocument()
    );
    const select = fieldControl("模板起手（可选）", "select") as HTMLSelectElement;
    expect(select.options).toHaveLength(3); // 1 默认 + 2 模板
    expect(select.options[0].textContent).toBe("不使用模板，直接输入创意");
    expect(select.options[1].textContent).toBe("霸总对峙/壁咚（霸总/对峙）");
    expect(select.options[2].textContent).toBe("甜宠咖啡馆约会（甜宠/约会）");
  });

  it("选中模板后将「标题：内容」预填到创意输入框，可继续修改", async () => {
    renderNew();
    await waitFor(() => screen.getByText("模板起手（可选）"));
    fireEvent.change(fieldControl("模板起手（可选）", "select"), {
      target: { value: "trope_boss_romance_confrontation" },
    });
    const premise = screen.getByPlaceholderText("输入你的创意...") as HTMLInputElement;
    expect(premise.value).toBe(
      "霸总对峙/壁咚：CEO romance confrontation: tall male lead in tailored suit"
    );
    // 用户可修改预填内容后再生成
    fireEvent.change(premise, { target: { value: "修改后的创意" } });
    expect(premise.value).toBe("修改后的创意");
  });

  it("模板库为空时不渲染模板选择器", async () => {
    mockGetPipelineTemplates.mockResolvedValue({ templates: [], total: 0, categories: [] });
    renderNew();
    await waitFor(() =>
      expect(mockGetPipelineTemplates).toHaveBeenCalledTimes(1)
    );
    expect(screen.queryByText("模板起手（可选）")).not.toBeInTheDocument();
  });

  it("模板库加载失败时静默降级（不渲染选择器，不阻塞生成流程）", async () => {
    mockGetPipelineTemplates.mockRejectedValue(new Error("后端离线"));
    const { onSuccess } = renderNew();
    await waitFor(() =>
      expect(mockGetPipelineTemplates).toHaveBeenCalledTimes(1)
    );
    expect(screen.queryByText("模板起手（可选）")).not.toBeInTheDocument();
    // 生成主流程不受影响：仍可提交生成并成功回调
    mockGenerateScript.mockResolvedValue(okResp(sampleScript));
    fireEvent.click(screen.getByText("生成"));
    await waitFor(() => expect(onSuccess).toHaveBeenCalledWith(sampleScript));
  });
});
