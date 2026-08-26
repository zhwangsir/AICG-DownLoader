import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { vi } from "vitest";
import AssetLibraryPanel from "./AssetLibraryPanel";
import { useDramaStore } from "../../store/useDramaStore";
import type { CharacterAssetEntry, ModelLoraEntry } from "../../api/client";

// Mock API 层（保留 resolveStaticUrl 真实实现）
vi.mock("../../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api/client")>();
  return {
    ...actual,
    getCharacterLibrary: vi.fn(),
    getModelRegistry: vi.fn(),
  };
});

import { getCharacterLibrary, getModelRegistry } from "../../api/client";
const mockGetCharacters = vi.mocked(getCharacterLibrary);
const mockGetModels = vi.mocked(getModelRegistry);

const sampleCharacters: CharacterAssetEntry[] = [
  {
    character_id: "c1",
    name: "林远",
    role: "主角",
    age: 26,
    description: "",
    personality: "",
    reference_images: { front: "/static/char/front.png" },
    appearance_lock: "linyuan, yellow uniform, dark skin",
    locked: true,
    consistency_level: "L3",
    created_at: 1,
    updated_at: 2,
  },
  {
    character_id: "c2",
    name: "苏晚晴",
    role: "反派",
    age: null,
    description: "",
    personality: "",
    reference_images: {},
    appearance_lock: "",
    locked: false,
    consistency_level: "L3",
    created_at: 1,
    updated_at: 1,
  },
];

const sampleLoras: ModelLoraEntry[] = [
  {
    filename: "guofeng.safetensors",
    name: "国风工笔",
    style_key: "guofeng",
    trigger_words: ["gf_style", "ink", "gongbi", "meticulous", "chinese_art"],
    weight: 0.7,
    sha256: "abc",
    size_kb: 1024,
    downloaded: true,
    subdir: "loras",
    downloaded_at: 123,
  },
  {
    filename: "cyber.safetensors",
    name: "赛博朋克",
    style_key: "cyberpunk",
    trigger_words: [],
    weight: 0.6,
    sha256: "def",
    size_kb: 2048,
    downloaded: false,
    subdir: "loras",
    downloaded_at: null,
  },
];

describe("AssetLibraryPanel", () => {
  beforeEach(() => {
    useDramaStore.getState().reset();
    vi.clearAllMocks();
    // 静默调试日志，保持测试输出干净
    vi.spyOn(console, "debug").mockImplementation(() => {});
    vi.spyOn(console, "info").mockImplementation(() => {});
    vi.spyOn(console, "warn").mockImplementation(() => {});
    vi.spyOn(console, "error").mockImplementation(() => {});
  });

  it("activePanel=null 时不渲染抽屉", () => {
    const { container } = render(<AssetLibraryPanel />);
    expect(container.querySelector(".asset-drawer")).not.toBeInTheDocument();
  });

  it("角色库：加载成功后渲染角色卡片（名称/锁定徽标/外观锁定卡）", async () => {
    mockGetCharacters.mockResolvedValue(sampleCharacters);
    useDramaStore.getState().setActivePanel("characters");
    render(<AssetLibraryPanel />);
    // loading 态先出现
    expect(screen.getByText("加载角色资产…")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("林远")).toBeInTheDocument());
    expect(screen.getByText("主角")).toBeInTheDocument();
    expect(screen.getByText("锁定")).toBeInTheDocument();
    expect(screen.getByText("未锁")).toBeInTheDocument();
    expect(screen.getByText(/linyuan, yellow uniform/)).toBeInTheDocument();
    // 无定妆照角色显示占位
    expect(screen.getByText("无图")).toBeInTheDocument();
  });

  it("角色库：空列表显示空态提示", async () => {
    mockGetCharacters.mockResolvedValue([]);
    useDramaStore.getState().setActivePanel("characters");
    render(<AssetLibraryPanel />);
    await waitFor(() =>
      expect(screen.getByText(/暂无角色资产/)).toBeInTheDocument()
    );
  });

  it("角色库：加载失败显示错误信息", async () => {
    mockGetCharacters.mockRejectedValue(new Error("网络中断"));
    useDramaStore.getState().setActivePanel("characters");
    render(<AssetLibraryPanel />);
    await waitFor(() =>
      expect(screen.getByText(/加载失败/)).toBeInTheDocument()
    );
    expect(screen.getByText(/网络中断/)).toBeInTheDocument();
  });

  it("模型库：渲染 LoRA 卡片（触发词 chips 截断/下载徽标/权重）", async () => {
    mockGetModels.mockResolvedValue({
      loras: sampleLoras,
      downloader_models: [],
      stats: {},
      sources: {},
    });
    useDramaStore.getState().setActivePanel("models");
    render(<AssetLibraryPanel />);
    await waitFor(() => expect(screen.getByText("国风工笔")).toBeInTheDocument());
    // 5 个触发词 → 显示前 4 个 + 「+1」（第 5 个 chinese_art 折叠）
    expect(screen.getByText("gf_style")).toBeInTheDocument();
    expect(screen.getByText("meticulous")).toBeInTheDocument();
    expect(screen.getByText("+1")).toBeInTheDocument();
    expect(screen.queryByText("chinese_art")).not.toBeInTheDocument();
    // 下载状态徽标
    expect(screen.getByText("已下载")).toBeInTheDocument();
    expect(screen.getByText("未下载")).toBeInTheDocument();
    // 权重与 style_key
    expect(screen.getByText(/权重 0.7 · guofeng/)).toBeInTheDocument();
    // 无触发词模型不渲染 chips 容器
    expect(screen.getByText("赛博朋克")).toBeInTheDocument();
  });

  it("模型库：加载失败显示错误信息", async () => {
    mockGetModels.mockRejectedValue(new Error("注册表损坏"));
    useDramaStore.getState().setActivePanel("models");
    render(<AssetLibraryPanel />);
    await waitFor(() => expect(screen.getByText(/注册表损坏/)).toBeInTheDocument());
  });

  it("关闭按钮 → setActivePanel(null)，抽屉卸载", async () => {
    mockGetCharacters.mockResolvedValue(sampleCharacters);
    useDramaStore.getState().setActivePanel("characters");
    const { container } = render(<AssetLibraryPanel />);
    fireEvent.click(screen.getByTitle("收起"));
    expect(useDramaStore.getState().activePanel).toBeNull();
    await waitFor(() =>
      expect(container.querySelector(".asset-drawer")).not.toBeInTheDocument()
    );
  });

  it("面板切换时输出调试日志（panel → characters，info 级默认可见）", async () => {
    mockGetCharacters.mockResolvedValue([]);
    useDramaStore.getState().setActivePanel("characters");
    render(<AssetLibraryPanel />);
    await waitFor(() =>
      expect(console.info).toHaveBeenCalledWith(
        "[AssetLibrary] panel →",
        "characters"
      )
    );
  });
});

describe("AssetLibraryPanel — 2026-08-15 UI 打磨回归", () => {
  beforeEach(() => {
    useDramaStore.getState().reset();
    vi.clearAllMocks();
    vi.spyOn(console, "debug").mockImplementation(() => {});
    vi.spyOn(console, "info").mockImplementation(() => {});
    vi.spyOn(console, "warn").mockImplementation(() => {});
    vi.spyOn(console, "error").mockImplementation(() => {});
  });

  it("角色库：缩略图加载失败 → 降级为「图失效」占位（不再裂图）", async () => {
    mockGetCharacters.mockResolvedValue(sampleCharacters);
    useDramaStore.getState().setActivePanel("characters");
    render(<AssetLibraryPanel />);
    const img = await screen.findByAltText("林远");
    // 触发裂图 → 应切换为占位块
    fireEvent.error(img);
    await waitFor(() => expect(screen.getByText("图失效")).toBeInTheDocument());
    // 原 img 被移除，占位块替代
    expect(screen.queryByAltText("林远")).not.toBeInTheDocument();
    // 无定妆照的苏晚晴仍显示「无图」（两种占位语义区分）
    expect(screen.getByText("无图")).toBeInTheDocument();
  });

  it("角色库：缩略图失败记录 warn 日志（含角色名与 URL）", async () => {
    mockGetCharacters.mockResolvedValue(sampleCharacters);
    useDramaStore.getState().setActivePanel("characters");
    render(<AssetLibraryPanel />);
    const img = await screen.findByAltText("林远");
    fireEvent.error(img);
    await waitFor(() =>
      expect(console.warn).toHaveBeenCalledWith(
        expect.stringContaining("[AssetLibrary] 角色缩略图加载失败: 林远")
      )
    );
  });

  it("角色库：正常缩略图不受影响（降级状态按 character_id 隔离）", async () => {
    const twoWithImages: CharacterAssetEntry[] = [
      sampleCharacters[0],
      { ...sampleCharacters[0], character_id: "c3", name: "白芷", reference_images: { front: "/static/char/baizhi.png" } },
    ];
    mockGetCharacters.mockResolvedValue(twoWithImages);
    useDramaStore.getState().setActivePanel("characters");
    render(<AssetLibraryPanel />);
    const broken = await screen.findByAltText("林远");
    fireEvent.error(broken);
    await waitFor(() => expect(screen.getByText("图失效")).toBeInTheDocument());
    // 另一角色的 img 仍正常渲染
    expect(screen.getByAltText("白芷")).toBeInTheDocument();
  });

  it("角色库：名称包裹在 .asset-card-name-text 中（flex 省略结构）", async () => {
    mockGetCharacters.mockResolvedValue(sampleCharacters);
    useDramaStore.getState().setActivePanel("characters");
    const { container } = render(<AssetLibraryPanel />);
    await screen.findByAltText("林远");
    const nameSpan = container.querySelector(".asset-card-name > .asset-card-name-text");
    expect(nameSpan).toBeInTheDocument();
    expect(nameSpan!.textContent).toBe("林远");
    // 徽标与名称文本为兄弟节点（不被挤出）
    const badge = container.querySelector(".asset-card-name > .asset-badge");
    expect(badge).toBeInTheDocument();
  });

  it("模型库：长名称包裹在 .asset-card-name-text，徽标常驻不被挤出", async () => {
    const longNameLora: ModelLoraEntry = {
      ...sampleLoras[1],
      filename: "realistic-photographic-film-stock-footage-style-xl-f1d.safetensors",
      name: "Realistic Photographic Film Stock Footage Style XL + F1D Long Name",
    };
    mockGetModels.mockResolvedValue({
      loras: [longNameLora],
      downloader_models: [],
      stats: {},
      sources: {},
    });
    useDramaStore.getState().setActivePanel("models");
    const { container } = render(<AssetLibraryPanel />);
    await waitFor(() =>
      expect(container.querySelector(".asset-card-name-text")).toBeInTheDocument()
    );
    const nameSpan = container.querySelector(".asset-card-name > .asset-card-name-text");
    expect(nameSpan!.textContent).toContain("Realistic Photographic");
    expect(container.querySelector(".asset-card-name > .asset-badge")).toBeInTheDocument();
    expect(screen.getByText("未下载")).toBeInTheDocument();
  });
});
