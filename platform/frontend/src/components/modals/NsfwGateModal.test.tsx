import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { vi } from "vitest";
import { NsfwGateModal } from "./NsfwGateModal";
import { useDramaStore } from "../../store/useDramaStore";

vi.mock("../../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api/client")>();
  return {
    ...actual,
    getNsfwStatus: vi.fn(),
    setNsfwEnabled: vi.fn(),
    changeNsfwPin: vi.fn(),
  };
});

import { getNsfwStatus, setNsfwEnabled, changeNsfwPin } from "../../api/client";

const mockStatus = vi.mocked(getNsfwStatus);
const mockSet = vi.mocked(setNsfwEnabled);
const mockChange = vi.mocked(changeNsfwPin);

beforeEach(() => {
  useDramaStore.getState().reset();
  vi.clearAllMocks();
  mockStatus.mockResolvedValue({ nsfw_enabled: false, has_pin: false });
});

describe("NsfwGateModal — 首次设置 PIN", () => {
  it("未设 PIN：显示设置表单与说明", () => {
    render(<NsfwGateModal onClose={vi.fn()} />);
    expect(screen.getByText(/首次开启需设置管理 PIN/)).toBeInTheDocument();
    expect(screen.getByPlaceholderText("4-8 位数字")).toBeInTheDocument();
    expect(screen.getByText("设置 PIN 并解锁")).toBeInTheDocument();
  });

  it("两次 PIN 不一致 → 显示错误且不调后端", async () => {
    render(<NsfwGateModal onClose={vi.fn()} />);
    fireEvent.change(screen.getByPlaceholderText("4-8 位数字"), { target: { value: "1234" } });
    fireEvent.change(screen.getByPlaceholderText("再次输入"), { target: { value: "5678" } });
    fireEvent.click(screen.getByText("设置 PIN 并解锁"));
    await waitFor(() => expect(screen.getByText("两次输入的新 PIN 不一致")).toBeInTheDocument());
    expect(mockSet).not.toHaveBeenCalled();
  });

  it("设置成功 → new_pin 透传 + 写回 store + 关闭", async () => {
    const onClose = vi.fn();
    mockSet.mockResolvedValue({ nsfw_enabled: true, has_pin: true });
    render(<NsfwGateModal onClose={onClose} />);
    fireEvent.change(screen.getByPlaceholderText("4-8 位数字"), { target: { value: "1234" } });
    fireEvent.change(screen.getByPlaceholderText("再次输入"), { target: { value: "1234" } });
    fireEvent.click(screen.getByText("设置 PIN 并解锁"));
    await waitFor(() => expect(mockSet).toHaveBeenCalledWith(true, "", "1234"));
    await waitFor(() => {
      const s = useDramaStore.getState();
      expect(s.nsfwEnabled).toBe(true);
      expect(s.nsfwHasPin).toBe(true);
      expect(s.statusInfo).toBe("NSFW 已解锁");
    });
    expect(onClose).toHaveBeenCalled();
  });
});

describe("NsfwGateModal — PIN 解锁/锁定", () => {
  it("已设 PIN 且关闭态：输入 PIN 解锁", async () => {
    useDramaStore.getState().setNsfwState(false, true);
    mockStatus.mockResolvedValue({ nsfw_enabled: false, has_pin: true });
    mockSet.mockResolvedValue({ nsfw_enabled: true, has_pin: true });
    const onClose = vi.fn();
    render(<NsfwGateModal onClose={onClose} />);
    expect(screen.getByText("解锁 NSFW")).toBeInTheDocument();
    fireEvent.change(screen.getByPlaceholderText("输入 PIN"), { target: { value: "1234" } });
    fireEvent.click(screen.getByText("解锁 NSFW"));
    await waitFor(() => expect(mockSet).toHaveBeenCalledWith(true, "1234", undefined));
    expect(onClose).toHaveBeenCalled();
  });

  it("PIN 错误 → 显示后端错误信息", async () => {
    useDramaStore.getState().setNsfwState(false, true);
    mockSet.mockRejectedValue(new Error("PIN 错误"));
    render(<NsfwGateModal onClose={vi.fn()} />);
    fireEvent.change(screen.getByPlaceholderText("输入 PIN"), { target: { value: "0000" } });
    fireEvent.click(screen.getByText("解锁 NSFW"));
    await waitFor(() => expect(screen.getByText("PIN 错误")).toBeInTheDocument());
  });

  it("解锁态：输入 PIN 可重新锁定", async () => {
    useDramaStore.getState().setNsfwState(true, true);
    mockStatus.mockResolvedValue({ nsfw_enabled: true, has_pin: true });
    mockSet.mockResolvedValue({ nsfw_enabled: false, has_pin: true });
    const onClose = vi.fn();
    render(<NsfwGateModal onClose={onClose} />);
    expect(screen.getByText(/NSFW 当前已解锁/)).toBeInTheDocument();
    fireEvent.change(screen.getByPlaceholderText("输入 PIN"), { target: { value: "1234" } });
    fireEvent.click(screen.getByText("锁定 NSFW"));
    await waitFor(() => expect(mockSet).toHaveBeenCalledWith(false, "1234", undefined));
    await waitFor(() => {
      expect(useDramaStore.getState().nsfwEnabled).toBe(false);
      expect(useDramaStore.getState().statusInfo).toBe("NSFW 已锁定");
    });
    expect(onClose).toHaveBeenCalled();
  });
});

describe("NsfwGateModal — 修改 PIN", () => {
  beforeEach(() => {
    // 门禁打开时会从后端同步状态覆盖 store，需让 mock 与测试前置态一致
    mockStatus.mockResolvedValue({ nsfw_enabled: true, has_pin: true });
  });

  it("修改流程：旧 PIN + 新 PIN 提交", async () => {
    useDramaStore.getState().setNsfwState(true, true);
    mockChange.mockResolvedValue({ nsfw_enabled: true, has_pin: true });
    render(<NsfwGateModal onClose={vi.fn()} />);
    fireEvent.click(screen.getByText("修改 PIN"));
    expect(screen.getByText("确认修改")).toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText("输入 PIN"), { target: { value: "1234" } });
    fireEvent.change(screen.getByPlaceholderText("4-8 位数字"), { target: { value: "5678" } });
    fireEvent.change(screen.getByPlaceholderText("再次输入"), { target: { value: "5678" } });
    fireEvent.click(screen.getByText("确认修改"));
    await waitFor(() => expect(mockChange).toHaveBeenCalledWith("1234", "5678"));
    await waitFor(() =>
      expect(useDramaStore.getState().statusInfo).toBe("NSFW 管理 PIN 已修改")
    );
    // 修改完成后返回主界面
    await waitFor(() => expect(screen.getByText("锁定 NSFW")).toBeInTheDocument());
  });

  it("修改 PIN 两次不一致 → 显示错误", async () => {
    useDramaStore.getState().setNsfwState(true, true);
    render(<NsfwGateModal onClose={vi.fn()} />);
    fireEvent.click(screen.getByText("修改 PIN"));
    fireEvent.change(screen.getByPlaceholderText("输入 PIN"), { target: { value: "1234" } });
    fireEvent.change(screen.getByPlaceholderText("4-8 位数字"), { target: { value: "5678" } });
    fireEvent.change(screen.getByPlaceholderText("再次输入"), { target: { value: "9999" } });
    fireEvent.click(screen.getByText("确认修改"));
    await waitFor(() => expect(screen.getByText("两次输入的新 PIN 不一致")).toBeInTheDocument());
    expect(mockChange).not.toHaveBeenCalled();
  });

  it("返回按钮回到主界面且不清 PIN", async () => {
    useDramaStore.getState().setNsfwState(true, true);
    render(<NsfwGateModal onClose={vi.fn()} />);
    fireEvent.click(screen.getByText("修改 PIN"));
    fireEvent.click(screen.getByText("返回"));
    expect(screen.getByText("锁定 NSFW")).toBeInTheDocument();
  });
});

describe("NsfwGateModal — 通用行为", () => {
  it("遮罩点击关闭；模态内部点击不冒泡", () => {
    const onClose = vi.fn();
    const { container } = render(<NsfwGateModal onClose={onClose} />);
    fireEvent.click(container.querySelector(".modal")!);
    expect(onClose).not.toHaveBeenCalled();
    fireEvent.click(container.querySelector(".modal-overlay")!);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("取消按钮关闭", () => {
    const onClose = vi.fn();
    render(<NsfwGateModal onClose={onClose} />);
    fireEvent.click(screen.getByText("取消"));
    expect(onClose).toHaveBeenCalled();
  });

  it("打开时从后端同步状态到 store", async () => {
    mockStatus.mockResolvedValue({ nsfw_enabled: true, has_pin: true });
    render(<NsfwGateModal onClose={vi.fn()} />);
    await waitFor(() => {
      const s = useDramaStore.getState();
      expect(s.nsfwEnabled).toBe(true);
      expect(s.nsfwHasPin).toBe(true);
    });
  });

  it("后端状态同步失败静默忽略", async () => {
    mockStatus.mockRejectedValue(new Error("网络不可达"));
    render(<NsfwGateModal onClose={vi.fn()} />);
    await waitFor(() => expect(mockStatus).toHaveBeenCalled());
    expect(useDramaStore.getState().nsfwEnabled).toBe(false);
  });
});
