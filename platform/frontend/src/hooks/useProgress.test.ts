import { act, renderHook } from "@testing-library/react";
import { vi } from "vitest";
import { useProgress } from "./useProgress";
import type { ProgressEvent } from "../api/client";

/**
 * useProgress.ts 覆盖率补缺：jsdom 无 EventSource，以 MockEventSource 记录实例，
 * 逐一驱动 onopen/onmessage/onerror 与卸载/reset/streamUrl 切换路径。
 */

class MockEventSource {
  static instances: MockEventSource[] = [];
  url: string;
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;
  constructor(url: string) {
    this.url = url;
    MockEventSource.instances.push(this);
  }
  close() {
    this.closed = true;
  }
}

const evt = (patch: Partial<ProgressEvent> = {}): ProgressEvent => ({
  task_id: "t1",
  agent: "pipeline",
  status: "running",
  percent: 40,
  message: "分镜生成中",
  result: null,
  error: null,
  updated_at: 1,
  ...patch,
});

describe("useProgress（SSE 进度订阅）", () => {
  beforeEach(() => {
    MockEventSource.instances = [];
    vi.stubGlobal("EventSource", MockEventSource);
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("streamUrl 为 null 时不建立连接，状态保持初始", () => {
    const { result } = renderHook(() => useProgress(null));
    expect(MockEventSource.instances).toHaveLength(0);
    expect(result.current.connected).toBe(false);
    expect(result.current.status).toBeNull();
    expect(result.current.percent).toBe(0);
    expect(result.current.message).toBe("");
  });

  it("建立连接后 onopen 置 connected=true", () => {
    const { result } = renderHook(() => useProgress("/api/progress/t1/stream"));
    expect(MockEventSource.instances).toHaveLength(1);
    expect(MockEventSource.instances[0].url).toBe("/api/progress/t1/stream");
    act(() => MockEventSource.instances[0].onopen?.());
    expect(result.current.connected).toBe(true);
  });

  it("onmessage 解析 JSON 事件并全量更新状态", () => {
    const { result } = renderHook(() => useProgress("/s"));
    const es = MockEventSource.instances[0];
    act(() => es.onopen?.());
    act(() =>
      es.onmessage?.({
        data: JSON.stringify(evt({ percent: 65, message: "视频生成中", result: { url: "u" } })),
      })
    );
    expect(result.current.connected).toBe(true);
    expect(result.current.status).toBe("running");
    expect(result.current.percent).toBe(65);
    expect(result.current.message).toBe("视频生成中");
    expect(result.current.result).toEqual({ url: "u" });
    expect(result.current.error).toBeNull();
  });

  it("心跳注释行与空数据被忽略，状态不变", () => {
    const { result } = renderHook(() => useProgress("/s"));
    const es = MockEventSource.instances[0];
    act(() => es.onmessage?.({ data: JSON.stringify(evt()) }));
    act(() => es.onmessage?.({ data: ":heartbeat" }));
    act(() => es.onmessage?.({ data: "" }));
    expect(result.current.percent).toBe(40);
    expect(result.current.message).toBe("分镜生成中");
  });

  it("非 JSON 消息静默忽略（catch 分支）", () => {
    const { result } = renderHook(() => useProgress("/s"));
    const es = MockEventSource.instances[0];
    act(() => es.onmessage?.({ data: "not-json{{" }));
    expect(result.current.status).toBeNull();
    expect(result.current.percent).toBe(0);
  });

  it("completed 终态 500ms 后自动关闭连接", () => {
    vi.useFakeTimers();
    renderHook(() => useProgress("/s"));
    const es = MockEventSource.instances[0];
    act(() => es.onmessage?.({ data: JSON.stringify(evt({ status: "completed", percent: 100 })) }));
    expect(es.closed).toBe(false);
    act(() => {
      vi.advanceTimersByTime(600);
    });
    expect(es.closed).toBe(true);
  });

  it("failed 终态同样 500ms 后自动关闭连接", () => {
    vi.useFakeTimers();
    const { result } = renderHook(() => useProgress("/s"));
    const es = MockEventSource.instances[0];
    act(() =>
      es.onmessage?.({ data: JSON.stringify(evt({ status: "failed", error: "oom" })) })
    );
    expect(result.current.status).toBe("failed");
    expect(result.current.error).toBe("oom");
    act(() => {
      vi.advanceTimersByTime(600);
    });
    expect(es.closed).toBe(true);
  });

  it("onerror 将 connected 置 false（EventSource 自动重连，不关闭）", () => {
    const { result } = renderHook(() => useProgress("/s"));
    const es = MockEventSource.instances[0];
    act(() => es.onopen?.());
    expect(result.current.connected).toBe(true);
    act(() => es.onerror?.());
    expect(result.current.connected).toBe(false);
    expect(es.closed).toBe(false);
  });

  it("组件卸载时关闭连接", () => {
    const { unmount } = renderHook(() => useProgress("/s"));
    const es = MockEventSource.instances[0];
    unmount();
    expect(es.closed).toBe(true);
  });

  it("streamUrl 切换时关闭旧连接并建立新连接", () => {
    const { rerender } = renderHook(({ url }) => useProgress(url), {
      initialProps: { url: "/s/1" as string | null },
    });
    const first = MockEventSource.instances[0];
    rerender({ url: "/s/2" });
    expect(first.closed).toBe(true);
    expect(MockEventSource.instances).toHaveLength(2);
    expect(MockEventSource.instances[1].url).toBe("/s/2");
    expect(MockEventSource.instances[1].closed).toBe(false);
  });

  it("streamUrl 变为 null 时关闭连接且不再新建", () => {
    const { rerender } = renderHook(({ url }) => useProgress(url), {
      initialProps: { url: "/s/1" as string | null },
    });
    const first = MockEventSource.instances[0];
    rerender({ url: null });
    expect(first.closed).toBe(true);
    expect(MockEventSource.instances).toHaveLength(1);
  });

  it("reset 关闭当前连接并重置全部状态", () => {
    const { result } = renderHook(() => useProgress("/s"));
    const es = MockEventSource.instances[0];
    act(() => es.onopen?.());
    act(() => es.onmessage?.({ data: JSON.stringify(evt()) }));
    expect(result.current.percent).toBe(40);
    act(() => result.current.reset());
    expect(es.closed).toBe(true);
    expect(result.current.connected).toBe(false);
    expect(result.current.status).toBeNull();
    expect(result.current.percent).toBe(0);
    expect(result.current.message).toBe("");
    expect(result.current.result).toBeNull();
    expect(result.current.error).toBeNull();
  });
});
