// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import ky from "ky";
import type { ReactNode } from "react";
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, opts?: Record<string, unknown>) =>
      opts ? `${key}:${JSON.stringify(opts)}` : key,
  }),
}));

vi.mock("@/lib/api", () => ({
  api: ky.create({ baseUrl: "http://localhost:3000/" }),
}));

import { ModelLibrarySection } from "@/components/settings/model-library-section";
import { useDownloadRequestStore } from "@/stores/downloadRequestStore";

const server = setupServer();

beforeAll(() => server.listen());
afterEach(() => {
  server.resetHandlers();
  useDownloadRequestStore.getState().clear();
});
afterAll(() => server.close());

const BASE = "http://localhost:3000/api/v1/model-library";

function wrapper({ children }: { children: ReactNode }) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
}

function mockNsfw(status: { nsfw_enabled: boolean }) {
  return http.get(`${BASE}/nsfw`, () => HttpResponse.json({ ok: true, data: status }));
}

function mockLibrary(items: unknown[] = [], types: string[] = []) {
  return http.get(`${BASE}/models`, () =>
    HttpResponse.json({
      ok: true,
      data: { items, total: items.length, types, scanned_at: 1, cache_hit: false },
    }),
  );
}

const ENTRY = {
  name: "majicMIX.safetensors",
  rel_path: "checkpoints/majicMIX.safetensors",
  root: "models",
  type: "checkpoints",
  size: 2 * 1024 * 1024 * 1024,
  mtime: 1700000000,
  nsfw: false,
};

describe("ModelLibrarySection — 页签与 NAS 列表", () => {
  it("默认展示 NAS 列表：名称/类型徽章/大小/日期", async () => {
    server.use(mockNsfw({ nsfw_enabled: false }), mockLibrary([ENTRY], ["checkpoints"]));
    render(<ModelLibrarySection />, { wrapper });
    const nameCell = await screen.findByText("majicMIX.safetensors");
    const row = nameCell.closest("li")!;
    expect(within(row).getByText("checkpoints")).toBeInTheDocument();
    expect(within(row).getByText("2.00 GB")).toBeInTheDocument();
    expect(
      within(row).getByText(`models · ${new Date(ENTRY.mtime * 1000).toLocaleDateString()}`),
    ).toBeInTheDocument();
  });

  it("空结果与错误态", async () => {
    server.use(mockNsfw({ nsfw_enabled: false }), mockLibrary([], []));
    const { unmount } = render(<ModelLibrarySection />, { wrapper });
    expect(await screen.findByText("settings.library.nas.empty")).toBeInTheDocument();
    unmount();

    server.use(
      mockNsfw({ nsfw_enabled: false }),
      http.get(`${BASE}/models`, () => HttpResponse.error()),
    );
    render(<ModelLibrarySection />, { wrapper });
    expect(await screen.findByText("settings.library.nas.loadFailed")).toBeInTheDocument();
  });

  it("类型过滤 chip 触发 type 参数请求", async () => {
    let lastUrl = "";
    server.use(
      mockNsfw({ nsfw_enabled: false }),
      http.get(`${BASE}/models`, ({ request }) => {
        lastUrl = request.url;
        return HttpResponse.json({
          ok: true,
          data: { items: [ENTRY], total: 1, types: ["checkpoints", "loras"], scanned_at: 1, cache_hit: false },
        });
      }),
    );
    const user = userEvent.setup();
    render(<ModelLibrarySection />, { wrapper });
    const lorasChip = await screen.findByRole("button", { name: "loras" });
    await user.click(lorasChip);
    await waitFor(() => expect(lastUrl).toContain("type=loras"));
  });

  it("刷新按钮触发 refresh=true", async () => {
    let refreshSeen = false;
    server.use(
      mockNsfw({ nsfw_enabled: false }),
      http.get(`${BASE}/models`, ({ request }) => {
        if (new URL(request.url).searchParams.get("refresh") === "true") refreshSeen = true;
        return HttpResponse.json({
          ok: true,
          data: { items: [], total: 0, types: [], scanned_at: 1, cache_hit: false },
        });
      }),
    );
    const user = userEvent.setup();
    render(<ModelLibrarySection />, { wrapper });
    await user.click(
      await screen.findByRole("button", { name: "settings.library.nas.refresh" }),
    );
    await waitFor(() => expect(refreshSeen).toBe(true));
  });

  it("NSFW 条目带标记徽章", async () => {
    server.use(
      mockNsfw({ nsfw_enabled: true }),
      mockLibrary([{ ...ENTRY, name: "urpm_x.pt", type: "loras", nsfw: true }], ["loras"]),
    );
    render(<ModelLibrarySection />, { wrapper });
    const nameCell = await screen.findByText("urpm_x.pt");
    const row = nameCell.closest("li")!;
    expect(within(row).getByText("NSFW")).toBeInTheDocument();
  });
});

describe("ModelLibrarySection — NSFW（R18 确认）", () => {
  it("未开启：显示 R18 警示，确认后 POST enabled=true 并关闭", async () => {
    let body: Record<string, unknown> | null = null;
    server.use(
      mockNsfw({ nsfw_enabled: false }),
      mockLibrary([], []),
      http.post(`${BASE}/nsfw`, async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ ok: true, data: { nsfw_enabled: true } });
      }),
    );
    const user = userEvent.setup();
    render(<ModelLibrarySection />, { wrapper });
    await user.click(await screen.findByRole("button", { name: /settings.library.nsfw.button/ }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("settings.library.nsfw.r18Warning")).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: /settings.library.nsfw.confirmR18/ }));
    await waitFor(() => expect(body).toEqual({ enabled: true }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("已开启：显示已开启提示，可一键隐藏", async () => {
    let body: Record<string, unknown> | null = null;
    server.use(
      mockNsfw({ nsfw_enabled: true }),
      mockLibrary([], []),
      http.post(`${BASE}/nsfw`, async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ ok: true, data: { nsfw_enabled: false } });
      }),
    );
    const user = userEvent.setup();
    render(<ModelLibrarySection />, { wrapper });
    await user.click(await screen.findByRole("button", { name: /settings.library.nsfw.button/ }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("settings.library.nsfw.r18Enabled")).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: /settings.library.nsfw.hideR18/ }));
    await waitFor(() => expect(body).toEqual({ enabled: false }));
  });

  it("请求失败：内联展示后端错误且不关闭", async () => {
    server.use(
      mockNsfw({ nsfw_enabled: false }),
      mockLibrary([], []),
      http.post(`${BASE}/nsfw`, () =>
        HttpResponse.json({ detail: "boom" }, { status: 500 }),
      ),
    );
    const user = userEvent.setup();
    render(<ModelLibrarySection />, { wrapper });
    await user.click(await screen.findByRole("button", { name: /settings.library.nsfw.button/ }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: /settings.library.nsfw.confirmR18/ }));
    expect(await within(dialog).findByText("boom")).toBeInTheDocument();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });
});

describe("ModelLibrarySection — 生图测试台", () => {
  it("切到生图台：渲染底模选择/提示词/尺寸/生成按钮", async () => {
    server.use(mockNsfw({ nsfw_enabled: false }), mockLibrary([ENTRY], ["checkpoints"]));
    const user = userEvent.setup();
    render(<ModelLibrarySection />, { wrapper });
    await user.click(
      await screen.findByRole("tab", { name: /settings.library.tabs.studio/ }),
    );
    expect(screen.getByLabelText(/settings.library.studio.checkpoint/)).toBeInTheDocument();
    expect(screen.getByLabelText(/settings.library.studio.prompt/)).toBeInTheDocument();
    expect(screen.getByLabelText(/settings.library.studio.negative/)).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /settings.library.studio.generate/ }),
    ).toBeInTheDocument();
  });

  it("空提示词点生成：本地校验拦截，不发请求", async () => {
    server.use(mockNsfw({ nsfw_enabled: false }), mockLibrary([ENTRY], ["checkpoints"]));
    let posted = false;
    server.use(
      http.post(`${BASE}/generate-image`, () => {
        posted = true;
        return HttpResponse.json({ ok: true, data: { data: [] } });
      }),
    );
    const user = userEvent.setup();
    render(<ModelLibrarySection />, { wrapper });
    await user.click(
      await screen.findByRole("tab", { name: /settings.library.tabs.studio/ }),
    );
    await user.click(screen.getByRole("button", { name: /settings.library.studio.generate/ }));
    expect(await screen.findByText("settings.library.studio.emptyPrompt")).toBeInTheDocument();
    expect(posted).toBe(false);
  });

  it("填提示词生成：POST 带底模与尺寸，返回 b64 后展示图片与下载", async () => {
    server.use(mockNsfw({ nsfw_enabled: false }), mockLibrary([ENTRY], ["checkpoints"]));
    let captured: Record<string, unknown> | null = null;
    server.use(
      http.post(`${BASE}/generate-image`, async ({ request }) => {
        captured = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ ok: true, data: { data: [{ b64_json: "QUJD" }] } });
      }),
    );
    const user = userEvent.setup();
    render(<ModelLibrarySection />, { wrapper });
    await user.click(
      await screen.findByRole("tab", { name: /settings.library.tabs.studio/ }),
    );
    await user.type(screen.getByLabelText(/settings.library.studio.prompt/), "1girl, test");
    await user.click(screen.getByRole("button", { name: /settings.library.studio.generate/ }));
    const img = await screen.findByAltText("settings.library.studio.result");
    expect(img).toHaveAttribute("src", "data:image/png;base64,QUJD");
    expect(screen.getByText("settings.library.studio.download")).toBeInTheDocument();
    expect(captured).toMatchObject({
      prompt: "1girl, test",
      checkpoint: "majicMIX realistic 麦橘写实_v7.safetensors",
      size: "832x1216",
    });
  });

  it("后端 403（R18 拦截）：错误信息透传", async () => {
    server.use(mockNsfw({ nsfw_enabled: false }), mockLibrary([ENTRY], ["checkpoints"]));
    server.use(
      http.post(
        `${BASE}/generate-image`,
        () =>
          new HttpResponse(
            JSON.stringify({ detail: "所选底模为 NSFW 内容，请先在模型库开启 R18" }),
            { status: 403, headers: { "Content-Type": "application/json" } },
          ),
      ),
    );
    const user = userEvent.setup();
    render(<ModelLibrarySection />, { wrapper });
    await user.click(
      await screen.findByRole("tab", { name: /settings.library.tabs.studio/ }),
    );
    await user.type(screen.getByLabelText(/settings.library.studio.prompt/), "1girl");
    await user.click(screen.getByRole("button", { name: /settings.library.studio.generate/ }));
    expect(
      await screen.findByText("所选底模为 NSFW 内容，请先在模型库开启 R18"),
    ).toBeInTheDocument();
  });
});

describe("ModelLibrarySection — 下载页签", () => {
  const SEARCH_RESULT = {
    items: [
      {
        id: 1,
        name: "majicMIX realistic",
        type: "Checkpoint",
        nsfw: false,
        versions: [
          {
            id: 11,
            name: "v7",
            files: [
              {
                name: "majic_v7.safetensors",
                size_kb: 2000000,
                download_url: "http://dl/1",
                sha256: "ab",
                primary: true,
              },
            ],
          },
        ],
      },
    ],
    total: 1,
  };

  it("搜索 → 结果卡片 → 发起下载 POST", async () => {
    let postBody: Record<string, unknown> | null = null;
    server.use(
      mockNsfw({ nsfw_enabled: false }),
      mockLibrary([], []),
      http.get(`${BASE}/search`, () => HttpResponse.json({ ok: true, data: SEARCH_RESULT })),
      http.get(`${BASE}/downloads`, () => HttpResponse.json({ ok: true, data: { items: [] } })),
      http.post(`${BASE}/downloads`, async ({ request }) => {
        postBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(
          {
            ok: true,
            data: {
              task_id: "t1",
              filename: "majic_v7.safetensors",
              subdir: "checkpoints",
              status: "pending",
            },
          },
          { status: 201 },
        );
      }),
    );
    const user = userEvent.setup();
    render(<ModelLibrarySection />, { wrapper });
    await user.click(await screen.findByRole("tab", { name: /settings.library.tabs.download/ }));
    await user.type(
      screen.getByLabelText("settings.library.download.searchPlaceholder"),
      "majic",
    );
    await user.click(screen.getByRole("button", { name: "settings.library.download.search" }));
    expect(await screen.findByText("majicMIX realistic")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /settings.library.download.start/ }));
    await waitFor(() =>
      expect(postBody).toMatchObject({
        download_url: "http://dl/1",
        filename: "majic_v7.safetensors",
        subdir: "checkpoints",
        sha256: "ab",
      }),
    );
  });

  it("任务列表渲染进度与取消", async () => {
    let canceled = "";
    server.use(
      mockNsfw({ nsfw_enabled: false }),
      mockLibrary([], []),
      http.get(`${BASE}/downloads`, () =>
        HttpResponse.json({
          ok: true,
          data: {
            items: [
              {
                task_id: "t1",
                filename: "m.safetensors",
                subdir: "checkpoints",
                dest: "/x/checkpoints/m.safetensors",
                source_url: "http://dl/1",
                sha256: null,
                nsfw: false,
                status: "running",
                downloaded: 500,
                total: 1000,
                speed_bps: 1024 * 1024,
                error: null,
                created_at: 1,
              },
            ],
          },
        }),
      ),
      http.delete(`${BASE}/downloads/:id`, ({ params }) => {
        canceled = String(params.id);
        return HttpResponse.json({ ok: true, data: { task_id: canceled } });
      }),
    );
    const user = userEvent.setup();
    render(<ModelLibrarySection />, { wrapper });
    await user.click(await screen.findByRole("tab", { name: /settings.library.tabs.download/ }));
    expect(await screen.findByText("m.safetensors")).toBeInTheDocument();
    expect(screen.getByText(/50%/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "settings.library.download.cancel" }));
    await waitFor(() => expect(canceled).toBe("t1"));
  });

  it("无任务时空态展示", async () => {
    server.use(
      mockNsfw({ nsfw_enabled: false }),
      mockLibrary([], []),
      http.get(`${BASE}/downloads`, () => HttpResponse.json({ ok: true, data: { items: [] } })),
    );
    const user = userEvent.setup();
    render(<ModelLibrarySection />, { wrapper });
    await user.click(await screen.findByRole("tab", { name: /settings.library.tabs.download/ }));
    expect(await screen.findByText("settings.library.download.noTasks")).toBeInTheDocument();
  });

  it("缺失一键补齐：消费跨页请求 → 切下载页签 + 预填搜索词 + 结果卡片预选子目录", async () => {
    let searchQ = "";
    server.use(
      mockNsfw({ nsfw_enabled: false }),
      mockLibrary([], []),
      http.get(`${BASE}/search`, ({ request }) => {
        searchQ = new URL(request.url).searchParams.get("q") ?? "";
        return HttpResponse.json({ ok: true, data: SEARCH_RESULT });
      }),
      http.get(`${BASE}/downloads`, () => HttpResponse.json({ ok: true, data: { items: [] } })),
    );
    // 模拟 workflow 引用面板点「去下载」写入的跨页请求
    useDownloadRequestStore.getState().requestDownload("majic", "loras");
    render(<ModelLibrarySection />, { wrapper });
    // 自动切到下载页签并预填搜索词、立即触发搜索
    expect(await screen.findByText("majicMIX realistic")).toBeInTheDocument();
    expect(
      screen.getByRole("tab", { name: /settings.library.tabs.download/ }),
    ).toHaveAttribute("aria-selected", "true");
    expect(
      screen.getByLabelText("settings.library.download.searchPlaceholder"),
    ).toHaveValue("majic");
    await waitFor(() => expect(searchQ).toBe("majic"));
    // 结果卡片目标子目录预选为 loras（优先于按 Civitai 类型猜测的 checkpoints）
    expect(
      screen.getByLabelText("settings.library.download.targetDir"),
    ).toHaveValue("loras");
    // 请求已被消费清除
    expect(useDownloadRequestStore.getState().pending).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// NSFW 手动标记
// ---------------------------------------------------------------------------

function mockMarks(marks: Record<string, boolean>) {
  return http.get(`${BASE}/nsfw/marks`, () =>
    HttpResponse.json({ ok: true, data: { marks, count: Object.keys(marks).length } }),
  );
}

describe("ModelLibrarySection — NSFW 手动标记", () => {
  const NSFW_ENTRY = { ...ENTRY, name: "urpm_x.pt", rel_path: "loras/urpm_x.pt", type: "loras", nsfw: true };

  it("NSFW 关闭时不渲染标记按钮", async () => {
    server.use(mockNsfw({ nsfw_enabled: false }), mockLibrary([ENTRY], ["checkpoints"]));
    render(<ModelLibrarySection />, { wrapper });
    expect(await screen.findByText("majicMIX.safetensors")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "settings.library.nas.toggleMark" }),
    ).not.toBeInTheDocument();
  });

  it("SFW 条目点击标记 → POST nsfw:true", async () => {
    let posted: { rel_path?: string; nsfw?: boolean | null } = {};
    server.use(
      mockNsfw({ nsfw_enabled: true }),
      mockLibrary([ENTRY], ["checkpoints"]),
      mockMarks({}),
      http.post(`${BASE}/nsfw/marks`, async ({ request }) => {
        posted = (await request.json()) as typeof posted;
        return HttpResponse.json({
          ok: true,
          data: { marks: { [posted.rel_path as string]: posted.nsfw }, count: 1 },
        });
      }),
    );
    const user = userEvent.setup();
    render(<ModelLibrarySection />, { wrapper });
    await user.click(
      await screen.findByRole("button", { name: "settings.library.nas.toggleMark" }),
    );
    await waitFor(() =>
      expect(posted).toEqual({ rel_path: "checkpoints/majicMIX.safetensors", nsfw: true }),
    );
  });

  it("NSFW 条目（关键词判定、无覆盖）点击 → POST nsfw:false", async () => {
    let posted: { rel_path?: string; nsfw?: boolean | null } = {};
    server.use(
      mockNsfw({ nsfw_enabled: true }),
      mockLibrary([NSFW_ENTRY], ["loras"]),
      mockMarks({}),
      http.post(`${BASE}/nsfw/marks`, async ({ request }) => {
        posted = (await request.json()) as typeof posted;
        return HttpResponse.json({ ok: true, data: { marks: {}, count: 0 } });
      }),
    );
    const user = userEvent.setup();
    render(<ModelLibrarySection />, { wrapper });
    await user.click(
      await screen.findByRole("button", { name: "settings.library.nas.toggleMark" }),
    );
    await waitFor(() =>
      expect(posted).toEqual({ rel_path: "loras/urpm_x.pt", nsfw: false }),
    );
  });

  it("已有覆盖且与现状一致 → 点击发送 nsfw:null 清除覆盖", async () => {
    let posted: { rel_path?: string; nsfw?: boolean | null } = {};
    server.use(
      mockNsfw({ nsfw_enabled: true }),
      mockLibrary([NSFW_ENTRY], ["loras"]),
      mockMarks({ "loras/urpm_x.pt": true }),
      http.post(`${BASE}/nsfw/marks`, async ({ request }) => {
        posted = (await request.json()) as typeof posted;
        return HttpResponse.json({ ok: true, data: { marks: {}, count: 0 } });
      }),
    );
    const user = userEvent.setup();
    render(<ModelLibrarySection />, { wrapper });
    await user.click(
      await screen.findByRole("button", { name: "settings.library.nas.toggleMark" }),
    );
    await waitFor(() =>
      expect(posted).toEqual({ rel_path: "loras/urpm_x.pt", nsfw: null }),
    );
  });
});
