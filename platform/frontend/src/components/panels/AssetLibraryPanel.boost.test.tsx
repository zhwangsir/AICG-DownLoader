import { render, screen, waitFor } from "@testing-library/react";
import { vi } from "vitest";
import AssetLibraryPanel from "./AssetLibraryPanel";
import { useDramaStore } from "../../store/useDramaStore";

// Mock API 层（保留 resolveStaticUrl 真实实现）
vi.mock("../../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api/client")>();
  return {
    ...actual,
    getCharacterLibrary: vi.fn(),
    getModelRegistry: vi.fn(),
  };
});

import { getModelRegistry } from "../../api/client";
const mockGetModels = vi.mocked(getModelRegistry);

describe("AssetLibraryPanel boost — 模型库空态补缺", () => {
  beforeEach(() => {
    useDramaStore.getState().reset();
    vi.clearAllMocks();
    vi.spyOn(console, "debug").mockImplementation(() => {});
    vi.spyOn(console, "info").mockImplementation(() => {});
    vi.spyOn(console, "warn").mockImplementation(() => {});
    vi.spyOn(console, "error").mockImplementation(() => {});
  });

  it("模型库：loras 为空数组时显示空态提示", async () => {
    mockGetModels.mockResolvedValue({
      loras: [],
      downloader_models: [],
      stats: {},
      sources: {},
    });
    useDramaStore.getState().setActivePanel("models");
    render(<AssetLibraryPanel />);
    await waitFor(() =>
      expect(screen.getByText("暂无 LoRA 注册记录。")).toBeInTheDocument()
    );
  });

  it("模型库：loras 字段缺失（?? [] 回退）同样显示空态提示", async () => {
    mockGetModels.mockResolvedValue({
      downloader_models: [],
      stats: {},
      sources: {},
    } as unknown as Awaited<ReturnType<typeof getModelRegistry>>);
    useDramaStore.getState().setActivePanel("models");
    render(<AssetLibraryPanel />);
    await waitFor(() =>
      expect(screen.getByText("暂无 LoRA 注册记录。")).toBeInTheDocument()
    );
  });
});
