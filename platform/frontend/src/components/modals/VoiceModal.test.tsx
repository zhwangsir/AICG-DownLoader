import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { VoiceModal } from "./VoiceModal";
import { generateVoice } from "../../api/client";
import type { CharacterData, SceneData, VoiceData } from "../../api/client";
import { VOICE_OPTIONS } from "./shared";

// Mock 配音生成 API（组件本身不直接播放/录音，无媒体 API 依赖）
vi.mock("../../api/client", () => ({
  generateVoice: vi.fn(),
}));

const mockGenerateVoice = vi.mocked(generateVoice);

const characters: CharacterData[] = [
  { character_id: "c1", name: "Alice", role: "主角", age: 26, description: "", personality: "" },
  { character_id: "c2", name: "Bob", role: "配角", age: 30, description: "", personality: "" },
];

function makeScene(patch: Partial<SceneData> = {}): SceneData {
  return {
    scene_id: 1,
    episode: 1,
    shot_type: "中景",
    description: "开场戏",
    prompt: "",
    negative_prompt: "",
    character_actions: "",
    dialogue: "你终于来了。我等了你很久！",
    emotion: "neutral",
    duration_seconds: 5,
    camera_movement: "static",
    ...patch,
  };
}

const voiceData: VoiceData = {
  scene_id: 1,
  audio_urls: [{ filename: "a.wav", voice: "v", text: "t", audio_url: "http://x/a.wav" }],
  total_lines: 1,
};

function renderModal(patch: Partial<Parameters<typeof VoiceModal>[0]> = {}) {
  const onClose = vi.fn();
  const onSuccess = vi.fn();
  const utils = render(
    <VoiceModal
      scenes={[makeScene()]}
      characters={characters}
      onClose={onClose}
      onSuccess={onSuccess}
      {...patch}
    />
  );
  return { onClose, onSuccess, ...utils };
}

/** 对白区 combobox 顺序：0=场景选择，之后每行 [角色, 语速, 音色] */
const lineSelects = (lineIdx: number) => {
  const all = screen.getAllByRole("combobox") as HTMLSelectElement[];
  return {
    character: all[1 + lineIdx * 3],
    rate: all[2 + lineIdx * 3],
    voice: all[3 + lineIdx * 3],
  };
};

describe("VoiceModal（生成配音）", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("空场景数组：提示先生成剧本，生成按钮禁用", () => {
    renderModal({ scenes: [] });
    expect(screen.getByText("请先生成剧本。")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "生成配音" })).toBeDisabled();
  });

  it("默认渲染：场景选择器、描述、台词引用与按标点拆分的对白列表", () => {
    renderModal();
    // 场景选择器
    const sceneSelect = screen.getAllByRole("combobox")[0] as HTMLSelectElement;
    expect(sceneSelect.value).toBe("1");
    expect(sceneSelect.querySelector("option")!.textContent).toBe("场景 1 — 中景");
    // 场景描述与台词引用
    expect(screen.getByText("开场戏")).toBeInTheDocument();
    expect(screen.getByText(/「你终于来了。我等了你很久！」/)).toBeInTheDocument();
    // 两句台词拆成 2 条，角色按序轮转、音色按 VOICE_OPTIONS 轮转
    expect(screen.getByText(/对白列表（2 条/)).toBeInTheDocument();
    const textareas = screen.getAllByPlaceholderText("对白文本") as HTMLTextAreaElement[];
    expect(textareas.map((t) => t.value)).toEqual(["你终于来了", "我等了你很久"]);
    expect(lineSelects(0).character.value).toBe("Alice");
    expect(lineSelects(0).rate.value).toBe("+0%");
    expect(lineSelects(0).voice.value).toBe(VOICE_OPTIONS[0].value);
    expect(lineSelects(1).character.value).toBe("Bob");
    expect(lineSelects(1).voice.value).toBe(VOICE_OPTIONS[1].value);
  });

  it("台词拆分边界：单字分段被过滤（trim 后长度 ≤1 不入列表）", () => {
    renderModal({ scenes: [makeScene({ dialogue: "你好。行。OK" })] });
    expect(screen.getByText(/对白列表（2 条/)).toBeInTheDocument();
    const textareas = screen.getAllByPlaceholderText("对白文本") as HTMLTextAreaElement[];
    expect(textareas.map((t) => t.value)).toEqual(["你好", "OK"]);
  });

  it("台词非空但拆分后全部被过滤（均 ≤1 字）：对白列表为 0 条", () => {
    renderModal({ scenes: [makeScene({ dialogue: "啊。哦！" })] });
    expect(screen.getByText(/对白列表（0 条/)).toBeInTheDocument();
    expect(screen.queryByPlaceholderText("对白文本")).not.toBeInTheDocument();
  });

  it("无台词场景：不渲染引用块，对白列表为 0 条", () => {
    renderModal({ scenes: [makeScene({ dialogue: "" })] });
    expect(screen.queryByText(/「/)).not.toBeInTheDocument();
    expect(screen.getByText(/对白列表（0 条/)).toBeInTheDocument();
  });

  it("切换场景：描述更新并按新场景台词重建对白列表", () => {
    renderModal({
      scenes: [
        makeScene(),
        makeScene({ scene_id: 2, shot_type: "特写", description: "第二场", dialogue: "新场景台词" }),
      ],
    });
    fireEvent.change(screen.getAllByRole("combobox")[0], { target: { value: "2" } });
    expect(screen.getByText("第二场")).toBeInTheDocument();
    expect(screen.getByText(/对白列表（1 条/)).toBeInTheDocument();
    expect(screen.getByPlaceholderText("对白文本")).toHaveValue("新场景台词");
  });

  it("角色下拉切换已登记角色：同步定位输入框", () => {
    renderModal();
    fireEvent.change(lineSelects(0).character, { target: { value: "Bob" } });
    const roles = screen.getAllByPlaceholderText("定位") as HTMLInputElement[];
    expect(roles[0].value).toBe("配角");
    expect(lineSelects(0).character.value).toBe("Bob");
  });

  it("无角色数据：角色名回退为「角色N」并渲染回退选项；选择未登记名字仅更新名字", () => {
    renderModal({ characters: [] });
    const sel = lineSelects(0).character;
    // 回退角色名 + 回退 option
    expect(sel.value).toBe("角色1");
    expect(sel.querySelectorAll("option").length).toBe(1);
    // 选择不在 characters 中的名字 → 走 find 失败分支，仅更新名字不崩溃
    fireEvent.change(sel, { target: { value: "角色1" } });
    expect(lineSelects(0).character.value).toBe("角色1");
    // 无角色时添加对白 → 名字回退为「角色」（场景两句台词 → 新行在第 3 位）
    fireEvent.click(screen.getByRole("button", { name: "+ 添加对白" }));
    expect(lineSelects(2).character.value).toBe("角色");
  });

  it("行内编辑：台词文本（含长文本）、定位、语速、音色均可修改", () => {
    renderModal();
    const longText = "很长的台词".repeat(200);
    fireEvent.change(screen.getAllByPlaceholderText("对白文本")[0], {
      target: { value: longText },
    });
    expect(screen.getAllByPlaceholderText("对白文本")[0]).toHaveValue(longText);
    fireEvent.change(screen.getAllByPlaceholderText("定位")[0], {
      target: { value: "反派" },
    });
    expect(screen.getAllByPlaceholderText("定位")[0]).toHaveValue("反派");
    fireEvent.change(lineSelects(0).rate, { target: { value: "+10%" } });
    expect(lineSelects(0).rate.value).toBe("+10%");
    fireEvent.change(lineSelects(0).voice, {
      target: { value: VOICE_OPTIONS[2].value },
    });
    expect(lineSelects(0).voice.value).toBe(VOICE_OPTIONS[2].value);
  });

  it("删除对白：点击 × 移除对应行", () => {
    renderModal();
    fireEvent.click(screen.getAllByTitle("删除该条")[0]);
    expect(screen.getByText(/对白列表（1 条/)).toBeInTheDocument();
    expect(screen.getByPlaceholderText("对白文本")).toHaveValue("我等了你很久");
  });

  it("排序：下移/上移交换行顺序；首行禁上移、末行禁下移", () => {
    renderModal();
    const ups = screen.getAllByTitle("上移") as HTMLButtonElement[];
    const downs = screen.getAllByTitle("下移") as HTMLButtonElement[];
    expect(ups[0]).toBeDisabled();
    expect(downs[1]).toBeDisabled();
    // 第一行下移 → 两行交换
    fireEvent.click(downs[0]);
    let values = (screen.getAllByPlaceholderText("对白文本") as HTMLTextAreaElement[]).map(
      (t) => t.value
    );
    expect(values).toEqual(["我等了你很久", "你终于来了"]);
    // 再上移回去
    fireEvent.click(screen.getAllByTitle("上移")[1]);
    values = (screen.getAllByPlaceholderText("对白文本") as HTMLTextAreaElement[]).map(
      (t) => t.value
    );
    expect(values).toEqual(["你终于来了", "我等了你很久"]);
  });

  it("单条对白时上移/下移均禁用，防御性点击不改变内容（越界 guard）", () => {
    renderModal({ scenes: [makeScene({ dialogue: "唯一一句台词" })] });
    const up = screen.getByTitle("上移") as HTMLButtonElement;
    const down = screen.getByTitle("下移") as HTMLButtonElement;
    expect(up).toBeDisabled();
    expect(down).toBeDisabled();
    fireEvent.click(up);
    fireEvent.click(down);
    expect(screen.getByPlaceholderText("对白文本")).toHaveValue("唯一一句台词");
  });

  it("添加对白：追加空行并默认绑定首个角色", () => {
    renderModal();
    fireEvent.click(screen.getByRole("button", { name: "+ 添加对白" }));
    expect(screen.getByText(/对白列表（3 条/)).toBeInTheDocument();
    const textareas = screen.getAllByPlaceholderText("对白文本") as HTMLTextAreaElement[];
    expect(textareas[2].value).toBe("");
    expect(lineSelects(2).character.value).toBe("Alice");
  });

  it("生成校验：清空全部台词后报「该场景没有台词」，不调用 API", () => {
    renderModal();
    // 清空两条已有台词文本（空白行会被 valid 过滤）
    screen.getAllByPlaceholderText("对白文本").forEach((ta) => {
      fireEvent.change(ta, { target: { value: "   " } });
    });
    fireEvent.click(screen.getByRole("button", { name: "生成配音" }));
    expect(screen.getByText("该场景没有台词，无法生成配音")).toBeInTheDocument();
    expect(mockGenerateVoice).not.toHaveBeenCalled();
  });

  it("生成成功：空白行被过滤，payload 正确并回调 onSuccess", async () => {
    mockGenerateVoice.mockResolvedValue({
      success: true,
      data: voiceData,
      error: null,
      elapsed_seconds: 0.5,
    });
    const { onSuccess } = renderModal();
    // 第二行置空 → 应被过滤
    fireEvent.change(screen.getAllByPlaceholderText("对白文本")[1], {
      target: { value: "  " },
    });
    fireEvent.click(screen.getByRole("button", { name: "生成配音" }));
    await waitFor(() => expect(onSuccess).toHaveBeenCalledWith(voiceData));
    expect(mockGenerateVoice).toHaveBeenCalledTimes(1);
    const payload = mockGenerateVoice.mock.calls[0][0];
    expect(payload.scene_id).toBe(1);
    expect(payload.dialogues).toHaveLength(1);
    expect(payload.dialogues[0]).toMatchObject({
      text: "你终于来了",
      character_name: "Alice",
      character_role: "主角",
      character_age: 26,
      rate: "+0%",
    });
  });

  it("生成失败：展示后端返回的 error", async () => {
    mockGenerateVoice.mockResolvedValue({
      success: false,
      data: null,
      error: "TTS 服务离线",
      elapsed_seconds: 0.1,
    });
    renderModal();
    fireEvent.click(screen.getByRole("button", { name: "生成配音" }));
    expect(await screen.findByText("TTS 服务离线")).toBeInTheDocument();
  });

  it("生成失败：无 error 字段回退默认文案「生成失败」", async () => {
    mockGenerateVoice.mockResolvedValue({
      success: false,
      data: null,
      error: null,
      elapsed_seconds: 0.1,
    });
    renderModal();
    fireEvent.click(screen.getByRole("button", { name: "生成配音" }));
    expect(await screen.findByText("生成失败")).toBeInTheDocument();
  });

  it("生成异常：catch 分支展示异常字符串", async () => {
    mockGenerateVoice.mockRejectedValue(new Error("network down"));
    renderModal();
    fireEvent.click(screen.getByRole("button", { name: "生成配音" }));
    expect(await screen.findByText("Error: network down")).toBeInTheDocument();
  });

  it("生成中 loading 态：按钮禁用并显示加载指示，完成后恢复", async () => {
    let resolveFn!: (v: Awaited<ReturnType<typeof generateVoice>>) => void;
    mockGenerateVoice.mockReturnValue(
      new Promise((r) => {
        resolveFn = r;
      })
    );
    const { onSuccess } = renderModal();
    const btn = screen.getByRole("button", { name: "生成配音" });
    fireEvent.click(btn);
    expect(btn).toBeDisabled();
    expect(document.querySelector(".loading")).toBeInTheDocument();
    resolveFn({ success: true, data: voiceData, error: null, elapsed_seconds: 1 });
    await waitFor(() => expect(onSuccess).toHaveBeenCalled());
    expect(btn).not.toBeDisabled();
  });

  it("无选中场景时生成按钮禁用，防御性点击直接返回（不调用 API）", () => {
    const onClose = vi.fn();
    const onSuccess = vi.fn();
    const { rerender } = render(
      <VoiceModal
        scenes={[makeScene()]}
        characters={characters}
        onClose={onClose}
        onSuccess={onSuccess}
      />
    );
    // 场景列表清空 → selectedScene 变 null，按钮禁用
    rerender(
      <VoiceModal scenes={[]} characters={characters} onClose={onClose} onSuccess={onSuccess} />
    );
    const btn = screen.getByRole("button", { name: "生成配音" });
    expect(btn).toBeDisabled();
    fireEvent.click(btn);
    expect(mockGenerateVoice).not.toHaveBeenCalled();
    expect(onSuccess).not.toHaveBeenCalled();
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
