import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { vi } from "vitest";
import { CharacterModal } from "./CharacterModal";
import {
  generateCharacter,
  type CharacterData,
  type ScriptData,
} from "../../api/client";
import { useDramaStore } from "../../store/useDramaStore";

vi.mock("../../api/client", () => ({
  generateCharacter: vi.fn(),
}));

const mockGenerate = vi.mocked(generateCharacter);

const characters: CharacterData[] = [
  {
    character_id: "c1",
    name: "林雪",
    role: "女主",
    age: 25,
    description: "冷静敏锐",
    personality: "内敛",
  },
  {
    character_id: "c2",
    name: "赵雷",
    role: "反派",
    age: null,
    description: "狡猾",
    personality: "暴躁",
  },
];

const scriptData: ScriptData = {
  project_id: "p-1",
  title: "测试剧",
  genre: "都市",
  aspect_ratio: "9:16",
  total_episodes: 1,
  characters,
  scenes: [],
};

function renderModal(patch: Partial<Parameters<typeof CharacterModal>[0]> = {}) {
  const props = {
    characters,
    onClose: vi.fn(),
    onSuccess: vi.fn(),
    ...patch,
  };
  return { ...render(<CharacterModal {...props} />), props };
}

describe("CharacterModal（生成角色定妆照）", () => {
  beforeEach(() => {
    useDramaStore.getState().reset();
    vi.clearAllMocks();
  });

  it("无角色时提示先生成剧本，无保存按钮，生成按钮禁用", () => {
    renderModal({ characters: [] });
    expect(
      screen.getByText("请先生成剧本，角色将从剧本中提取。")
    ).toBeInTheDocument();
    expect(screen.queryByText("保存角色修改")).not.toBeInTheDocument();
    expect(screen.getByText("生成定妆照").closest("button")).toBeDisabled();
  });

  it("默认选中首个角色：下拉/画风/一致性层级/可编辑信息区", () => {
    renderModal();
    expect(screen.getByText("生成角色定妆照")).toBeInTheDocument();
    const [charSelect, styleInput, levelSelect] = screen.getAllByRole(
      "combobox"
    ) as (HTMLSelectElement | HTMLInputElement)[];
    expect(charSelect.value).toBe("c1");
    expect(screen.getByText(/林雪 \(女主\)/)).toBeInTheDocument();
    expect((styleInput as HTMLInputElement).value).toBe("写实电影感");
    expect(levelSelect.value).toBe("L3");
    expect((screen.getByPlaceholderText("姓名") as HTMLInputElement).value).toBe(
      "林雪"
    );
    expect((screen.getByPlaceholderText("定位") as HTMLInputElement).value).toBe(
      "女主"
    );
    expect((screen.getByPlaceholderText("年龄") as HTMLInputElement).value).toBe(
      "25"
    );
    expect(screen.getByDisplayValue("冷静敏锐")).toBeInTheDocument();
    expect(screen.getByDisplayValue("内敛")).toBeInTheDocument();
  });

  it("切换角色：编辑区同步为目标角色（年龄 null 显示为空）", () => {
    renderModal();
    const [charSelect] = screen.getAllByRole("combobox");
    fireEvent.change(charSelect, { target: { value: "c2" } });
    expect((screen.getByPlaceholderText("姓名") as HTMLInputElement).value).toBe(
      "赵雷"
    );
    expect((screen.getByPlaceholderText("年龄") as HTMLInputElement).value).toBe(
      ""
    );
    expect(screen.getByDisplayValue("狡猾")).toBeInTheDocument();
  });

  it("编辑字段并生成：参数携带修改后的角色/画风/一致性层级", async () => {
    mockGenerate.mockResolvedValue({ success: true, data: {} as never });
    const { props } = renderModal();
    fireEvent.change(screen.getByPlaceholderText("姓名"), {
      target: { value: "林小雪" },
    });
    fireEvent.change(screen.getByPlaceholderText("定位"), {
      target: { value: "大女主" },
    });
    fireEvent.change(screen.getByPlaceholderText("年龄"), {
      target: { value: "30" },
    });
    fireEvent.change(screen.getByPlaceholderText("描述"), {
      target: { value: "新描述" },
    });
    fireEvent.change(screen.getByPlaceholderText("性格"), {
      target: { value: "新性格" },
    });
    const [, styleInput, levelSelect] = screen.getAllByRole("combobox");
    fireEvent.change(styleInput, { target: { value: "日系动漫" } });
    fireEvent.change(levelSelect, { target: { value: "L2" } });
    fireEvent.click(screen.getByText("生成定妆照"));
    await waitFor(() =>
      expect(mockGenerate).toHaveBeenCalledWith({
        character: expect.objectContaining({
          character_id: "c1",
          name: "林小雪",
          role: "大女主",
          age: 30,
          description: "新描述",
          personality: "新性格",
        }),
        style: "日系动漫",
        consistency_level: "L2",
      })
    );
    await waitFor(() => expect(props.onSuccess).toHaveBeenCalledWith("林小雪"));
  });

  it("年龄清空 → null", async () => {
    mockGenerate.mockResolvedValue({ success: true, data: {} as never });
    renderModal();
    fireEvent.change(screen.getByPlaceholderText("年龄"), {
      target: { value: "" },
    });
    fireEvent.click(screen.getByText("生成定妆照"));
    await waitFor(() =>
      expect(mockGenerate).toHaveBeenCalledWith(
        expect.objectContaining({
          character: expect.objectContaining({ age: null }),
        })
      )
    );
  });

  it("保存角色修改：写回 store 并短暂显示「已保存」", () => {
    vi.useFakeTimers();
    try {
      useDramaStore.getState().setScriptData(scriptData);
      renderModal();
      fireEvent.change(screen.getByPlaceholderText("姓名"), {
        target: { value: "林雪儿" },
      });
      fireEvent.change(screen.getByPlaceholderText("年龄"), {
        target: { value: "26" },
      });
      fireEvent.click(screen.getByText("保存角色修改"));
      const saved = useDramaStore
        .getState()
        .scriptData?.characters.find((c) => c.character_id === "c1");
      expect(saved).toMatchObject({ name: "林雪儿", age: 26 });
      expect(screen.getByText("已保存")).toBeInTheDocument();
      act(() => {
        vi.advanceTimersByTime(1600);
      });
      expect(screen.queryByText("已保存")).not.toBeInTheDocument();
      expect(screen.getByText("保存角色修改")).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it("后端返回失败：显示 error；无 error 显示默认「生成失败」", async () => {
    mockGenerate.mockResolvedValue({ success: false, error: "GPU 离线" });
    const { props, unmount } = renderModal();
    fireEvent.click(screen.getByText("生成定妆照"));
    await screen.findByText("GPU 离线");
    expect(props.onSuccess).not.toHaveBeenCalled();
    unmount();

    mockGenerate.mockResolvedValue({ success: false });
    renderModal();
    fireEvent.click(screen.getByText("生成定妆照"));
    await screen.findByText("生成失败");
  });

  it("请求异常：错误写入错误区", async () => {
    mockGenerate.mockRejectedValue(new Error("超时"));
    renderModal();
    fireEvent.click(screen.getByText("生成定妆照"));
    await screen.findByText(/Error: 超时/);
  });

  it("生成中按钮禁用并显示加载态", async () => {
    let resolveGen: (v: { success: boolean; data: never }) => void = () => {};
    mockGenerate.mockReturnValue(
      new Promise((r) => {
        resolveGen = r;
      })
    );
    const { container } = renderModal();
    fireEvent.click(screen.getByText("生成定妆照"));
    await waitFor(() =>
      expect(container.querySelector("span.loading")).toBeInTheDocument()
    );
    expect(
      container.querySelector("span.loading")!.closest("button")
    ).toBeDisabled();
    resolveGen({ success: true, data: {} as never });
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
