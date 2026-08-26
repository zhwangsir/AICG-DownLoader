import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { vi } from "vitest";
import ModelLibraryPanel from "./ModelLibraryPanel";
import { useDramaStore } from "../../store/useDramaStore";
import type { DownloadTask, NasModelEntry } from "../../api/client";

vi.mock("../../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api/client")>();
  return {
    ...actual,
    getModelRegistry: vi.fn(),
    getNasLibrary: vi.fn(),
    searchCivitaiModels: vi.fn(),
    startModelDownload: vi.fn(),
    getDownloadTasks: vi.fn(),
    cancelDownloadTask: vi.fn(),
  };
});

import {
  getModelRegistry,
  getNasLibrary,
  searchCivitaiModels,
  startModelDownload,
  getDownloadTasks,
  cancelDownloadTask,
} from "../../api/client";

const mockRegistry = vi.mocked(getModelRegistry);
const mockNas = vi.mocked(getNasLibrary);
const mockSearch = vi.mocked(searchCivitaiModels);
const mockStartDl = vi.mocked(startModelDownload);
const mockTasks = vi.mocked(getDownloadTasks);
const mockCancel = vi.mocked(cancelDownloadTask);

const nasItem: NasModelEntry = {
  name: "majicMIX_v7.safetensors",
  rel_path: "checkpoints/majicMIX_v7.safetensors",
  root: "models",
  type: "checkpoints",
  size: 4 * 1024 * 1024 * 1024,
  mtime: 1700000000,
  nsfw: false,
};

const nsfwItem: NasModelEntry = { ...nasItem, name: "lustifyNSFW_v8.safetensors", nsfw: true };

const runningTask: DownloadTask = {
  task_id: "t1",
  filename: "m.safetensors",
  subdir: "checkpoints",
  dest: "/nas/m.safetensors",
  source_url: "https://x",
  sha256: null,
  nsfw: false,
  status: "running",
  downloaded: 50,
  total: 100,
  speed_bps: 1024 * 1024,
  error: null,
  created_at: 1,
};

beforeEach(() => {
  useDramaStore.getState().reset();
  vi.clearAllMocks();
  mockRegistry.mockResolvedValue({
    loras: [
      {
        filename: "style.safetensors",
        name: "风格 LoRA",
        style_key: "写实",
        trigger_words: ["cinematic"],
        weight: 0.8,
        sha256: "x",
        size_kb: 1,
        downloaded: true,
      },
    ],
    stats: {},
  } as Awaited<ReturnType<typeof getModelRegistry>>);
  mockNas.mockResolvedValue({
    items: [nasItem],
    total: 1,
    types: ["checkpoints", "loras"],
    scanned_at: 1,
    cache_hit: false,
  });
  mockTasks.mockResolvedValue([]);
});

describe("ModelLibraryPanel — 页签与注册表", () => {
  it("默认注册表页签渲染 LoRA 卡片", async () => {
    render(<ModelLibraryPanel />);
    await waitFor(() => expect(screen.getByText("风格 LoRA")).toBeInTheDocument());
    expect(screen.getByText("已下载")).toBeInTheDocument();
  });

  it("三页签切换", async () => {
    render(<ModelLibraryPanel />);
    fireEvent.click(screen.getByRole("tab", { name: "NAS 模型" }));
    await waitFor(() => expect(mockNas).toHaveBeenCalled());
    fireEvent.click(screen.getByRole("tab", { name: "下载" }));
    expect(screen.getByPlaceholderText("搜索 Civitai 模型…")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "注册表" }));
    await waitFor(() => expect(screen.getByText("风格 LoRA")).toBeInTheDocument());
  });

  it("NSFW 锁按钮打开门禁模态（locked 态文案）", async () => {
    render(<ModelLibraryPanel />);
    const btn = screen.getByTitle(/NSFW 已锁定/);
    fireEvent.click(btn);
    expect(useDramaStore.getState().modals.nsfwGate).toBe(true);
  });

  it("NSFW 解锁态按钮样式与文案", () => {
    useDramaStore.getState().setNsfwState(true, true);
    render(<ModelLibraryPanel />);
    expect(screen.getByTitle(/NSFW 已解锁/)).toBeInTheDocument();
  });
});

describe("ModelLibraryPanel — NAS 模型页签", () => {
  it("渲染条目：名称/大小/类型/日期", async () => {
    const { container } = render(<ModelLibraryPanel />);
    fireEvent.click(screen.getByRole("tab", { name: "NAS 模型" }));
    await waitFor(() => expect(screen.getByText("majicMIX_v7.safetensors")).toBeInTheDocument());
    expect(screen.getByText(/4.00 GB/)).toBeInTheDocument();
    // 类型徽标（与过滤器 option 同文本，用选择器限定）
    expect(container.querySelector(".asset-badge")!.textContent).toBe("checkpoints");
    // 日期按本地时区动态计算（CI/本机时区无关）
    const d = new Date(1700000000 * 1000);
    const pad = (n: number) => String(n).padStart(2, "0");
    const expectDate = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
    expect(screen.getByText(new RegExp(expectDate))).toBeInTheDocument();
  });

  it("搜索输入触发防抖重查（q 透传）", async () => {
    render(<ModelLibraryPanel />);
    fireEvent.click(screen.getByRole("tab", { name: "NAS 模型" }));
    await waitFor(() => expect(mockNas).toHaveBeenCalledTimes(1));
    fireEvent.change(screen.getByPlaceholderText("搜索模型名/路径…"), {
      target: { value: "animagine" },
    });
    await waitFor(
      () => {
        expect(mockNas).toHaveBeenCalledWith(
          expect.objectContaining({ q: "animagine" })
        );
      },
      { timeout: 2000 }
    );
  });

  it("类型过滤透传", async () => {
    render(<ModelLibraryPanel />);
    fireEvent.click(screen.getByRole("tab", { name: "NAS 模型" }));
    await waitFor(() => expect(mockNas).toHaveBeenCalled());
    fireEvent.change(screen.getByTitle("类型过滤"), { target: { value: "loras" } });
    await waitFor(() =>
      expect(mockNas).toHaveBeenCalledWith(expect.objectContaining({ type: "loras" }))
    );
  });

  it("强制重扫按钮带 refresh=true", async () => {
    render(<ModelLibraryPanel />);
    fireEvent.click(screen.getByRole("tab", { name: "NAS 模型" }));
    await waitFor(() => expect(mockNas).toHaveBeenCalled());
    fireEvent.click(screen.getByTitle("强制重扫 NAS"));
    await waitFor(() =>
      expect(mockNas).toHaveBeenCalledWith(expect.objectContaining({ refresh: true }))
    );
  });

  it("NSFW 未解锁：复选框禁用 + 解锁按钮开门禁", async () => {
    render(<ModelLibraryPanel />);
    fireEvent.click(screen.getByRole("tab", { name: "NAS 模型" }));
    await waitFor(() => expect(mockNas).toHaveBeenCalled());
    const checkbox = screen.getByRole("checkbox");
    expect(checkbox).toBeDisabled();
    fireEvent.click(screen.getByText("解锁"));
    expect(useDramaStore.getState().modals.nsfwGate).toBe(true);
  });

  it("NSFW 已解锁：勾选后 include_nsfw=true 透传", async () => {
    useDramaStore.getState().setNsfwState(true, true);
    mockNas.mockResolvedValue({
      items: [nasItem, nsfwItem],
      total: 2,
      types: ["checkpoints"],
      scanned_at: 1,
      cache_hit: false,
    });
    render(<ModelLibraryPanel />);
    fireEvent.click(screen.getByRole("tab", { name: "NAS 模型" }));
    await waitFor(() => expect(mockNas).toHaveBeenCalled());
    fireEvent.click(screen.getByRole("checkbox"));
    await waitFor(() =>
      expect(mockNas).toHaveBeenCalledWith(expect.objectContaining({ include_nsfw: true }))
    );
    await waitFor(() => expect(screen.getByText("lustifyNSFW_v8.safetensors")).toBeInTheDocument());
    expect(screen.getByText("18+")).toBeInTheDocument();
  });

  it("加载失败显示错误", async () => {
    mockNas.mockRejectedValue(new Error("NAS 挂载失效"));
    render(<ModelLibraryPanel />);
    fireEvent.click(screen.getByRole("tab", { name: "NAS 模型" }));
    await waitFor(() => expect(screen.getByText(/NAS 挂载失效/)).toBeInTheDocument());
  });

  it("空结果显示占位", async () => {
    mockNas.mockResolvedValue({ items: [], total: 0, types: [], scanned_at: 1, cache_hit: false });
    render(<ModelLibraryPanel />);
    fireEvent.click(screen.getByRole("tab", { name: "NAS 模型" }));
    await waitFor(() => expect(screen.getByText("无匹配模型。")).toBeInTheDocument());
  });
});

describe("ModelLibraryPanel — 下载页签", () => {
  const civitaiModel = {
    id: 1,
    name: "TestCheckpoint",
    type: "Checkpoint",
    nsfw: false,
    versions: [
      {
        id: 11,
        name: "v1",
        files: [
          {
            name: "model.safetensors",
            size_kb: 2048,
            download_url: "https://civitai.red/dl/1",
            sha256: "abc",
            primary: true,
          },
        ],
      },
    ],
  };

  it("搜索渲染结果并可发起下载（subdir 映射 + sha256 透传）", async () => {
    mockSearch.mockResolvedValue({ items: [civitaiModel], total: 1 });
    mockStartDl.mockResolvedValue(runningTask);
    render(<ModelLibraryPanel />);
    fireEvent.click(screen.getByRole("tab", { name: "下载" }));

    fireEvent.change(screen.getByPlaceholderText("搜索 Civitai 模型…"), {
      target: { value: "test" },
    });
    fireEvent.click(screen.getByText("搜索"));
    await waitFor(() => expect(screen.getByText("TestCheckpoint")).toBeInTheDocument());

    fireEvent.click(screen.getByText("下载到 NAS"));
    await waitFor(() =>
      expect(mockStartDl).toHaveBeenCalledWith(
        expect.objectContaining({
          download_url: "https://civitai.red/dl/1",
          filename: "model.safetensors",
          subdir: "checkpoints",
          sha256: "abc",
          nsfw: false,
        })
      )
    );
    await waitFor(() => expect(screen.getByText(/已开始下载/)).toBeInTheDocument());
  });

  it("LORA 类型映射到 loras 子目录", async () => {
    mockSearch.mockResolvedValue({
      items: [{ ...civitaiModel, type: "LORA" }],
      total: 1,
    });
    mockStartDl.mockResolvedValue(runningTask);
    render(<ModelLibraryPanel />);
    fireEvent.click(screen.getByRole("tab", { name: "下载" }));
    fireEvent.click(screen.getByText("搜索"));
    await waitFor(() => expect(screen.getByText("TestCheckpoint")).toBeInTheDocument());
    fireEvent.click(screen.getByText("下载到 NAS"));
    await waitFor(() =>
      expect(mockStartDl).toHaveBeenCalledWith(expect.objectContaining({ subdir: "loras" }))
    );
  });

  it("NSFW 已解锁时搜索带 include_nsfw=true", async () => {
    useDramaStore.getState().setNsfwState(true, true);
    mockSearch.mockResolvedValue({ items: [], total: 0 });
    render(<ModelLibraryPanel />);
    fireEvent.click(screen.getByRole("tab", { name: "下载" }));
    fireEvent.click(screen.getByText("搜索"));
    await waitFor(() =>
      expect(mockSearch).toHaveBeenCalledWith(expect.objectContaining({ include_nsfw: true }))
    );
  });

  it("搜索失败显示错误", async () => {
    mockSearch.mockRejectedValue(new Error("Civitai 搜索失败: timeout"));
    render(<ModelLibraryPanel />);
    fireEvent.click(screen.getByRole("tab", { name: "下载" }));
    fireEvent.click(screen.getByText("搜索"));
    await waitFor(() => expect(screen.getByText(/Civitai 搜索失败/)).toBeInTheDocument());
  });

  it("下载失败显示通知", async () => {
    mockSearch.mockResolvedValue({ items: [civitaiModel], total: 1 });
    mockStartDl.mockRejectedValue(new Error("NSFW 内容未开启"));
    render(<ModelLibraryPanel />);
    fireEvent.click(screen.getByRole("tab", { name: "下载" }));
    fireEvent.click(screen.getByText("搜索"));
    await waitFor(() => expect(screen.getByText("TestCheckpoint")).toBeInTheDocument());
    fireEvent.click(screen.getByText("下载到 NAS"));
    await waitFor(() => expect(screen.getByText(/NSFW 内容未开启/)).toBeInTheDocument());
  });

  it("运行中任务渲染进度条与取消按钮", async () => {
    mockTasks.mockResolvedValue([runningTask]);
    render(<ModelLibraryPanel />);
    fireEvent.click(screen.getByRole("tab", { name: "下载" }));
    await waitFor(() => expect(screen.getByText("m.safetensors")).toBeInTheDocument());
    expect(screen.getByText(/50%/)).toBeInTheDocument();

    mockCancel.mockResolvedValue(undefined);
    fireEvent.click(screen.getByTitle("取消"));
    await waitFor(() => expect(mockCancel).toHaveBeenCalledWith("t1"));
  });

  it("失败任务显示错误信息", async () => {
    mockTasks.mockResolvedValue([
      { ...runningTask, status: "error", error: "SHA256 校验失败" },
    ]);
    render(<ModelLibraryPanel />);
    fireEvent.click(screen.getByRole("tab", { name: "下载" }));
    await waitFor(() => expect(screen.getByText(/SHA256 校验失败/)).toBeInTheDocument());
  });

  it("取消任务失败显示通知", async () => {
    mockTasks.mockResolvedValue([runningTask]);
    mockCancel.mockRejectedValue(new Error("无法取消"));
    render(<ModelLibraryPanel />);
    fireEvent.click(screen.getByRole("tab", { name: "下载" }));
    await waitFor(() => expect(screen.getByText("m.safetensors")).toBeInTheDocument());
    fireEvent.click(screen.getByTitle("取消"));
    await waitFor(() => expect(screen.getByText(/无法取消/)).toBeInTheDocument());
  });
});
