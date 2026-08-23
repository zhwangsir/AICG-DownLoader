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

import { applyModelRef, extractModelRefs } from "@/lib/comfyui-loaders";
import { ModelNamePicker } from "@/components/settings/model-name-picker";
import { WorkflowRefsPanel } from "@/components/settings/workflow-refs-panel";
import {
  filenameToQuery,
  useDownloadRequestStore,
} from "@/stores/downloadRequestStore";

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

const LIB_ITEMS = [
  {
    name: "majicMIX.safetensors",
    rel_path: "checkpoints/majicMIX.safetensors",
    root: "models",
    type: "checkpoints",
    size: 2 * 1024 * 1024 * 1024,
    mtime: 1700000000,
    nsfw: false,
  },
  {
    name: "animagineXL40.safetensors",
    rel_path: "checkpoints/animagineXL40.safetensors",
    root: "models",
    type: "checkpoints",
    size: 7 * 1024 * 1024 * 1024,
    mtime: 1700000001,
    nsfw: false,
  },
  {
    name: "anime-lora.pt",
    rel_path: "loras/anime-lora.pt",
    root: "models",
    type: "loras",
    size: 40 * 1024 * 1024,
    mtime: 1700000002,
    nsfw: false,
  },
];

function mockLibrary(items = LIB_ITEMS) {
  return http.get(`${BASE}/models`, () =>
    HttpResponse.json({
      ok: true,
      data: {
        items,
        total: items.length,
        types: [...new Set(items.map((i) => i.type))],
        scanned_at: 1,
        cache_hit: false,
      },
    }),
  );
}

const WORKFLOW = {
  "1": {
    class_type: "CheckpointLoaderSimple",
    inputs: { ckpt_name: "majicMIX.safetensors" },
  },
  "2": { class_type: "LoraLoader", inputs: { lora_name: "missing-lora.pt" } },
  "3": { class_type: "KSampler", inputs: { seed: 1 } },
};

describe("comfyui-loaders（纯函数）", () => {
  it("extractModelRefs 提取已知 loader 引用", () => {
    const refs = extractModelRefs(WORKFLOW);
    expect(refs).toHaveLength(2);
    expect(refs[0]).toMatchObject({
      nodeId: "1",
      field: "ckpt_name",
      filename: "majicMIX.safetensors",
      expectedTypes: ["checkpoints"],
    });
    expect(refs[1]).toMatchObject({ nodeId: "2", field: "lora_name" });
  });

  it("extractModelRefs 容忍非法节点", () => {
    expect(
      extractModelRefs({
        "1": "x" as unknown as Record<string, never>,
        "2": { class_type: "CheckpointLoaderSimple" },
        "3": { class_type: "Nope", inputs: { ckpt_name: "a.safetensors" } },
      }),
    ).toEqual([]);
  });

  it("applyModelRef 写回指定字段且不改动其他节点", () => {
    const refs = extractModelRefs(WORKFLOW);
    const next = applyModelRef(WORKFLOW, refs[0], "animagineXL40.safetensors");
    const node1 = next["1"] as { inputs: { ckpt_name: string } };
    expect(node1.inputs.ckpt_name).toBe("animagineXL40.safetensors");
    // 原对象不被修改
    const orig1 = WORKFLOW["1"] as { inputs: { ckpt_name: string } };
    expect(orig1.inputs.ckpt_name).toBe("majicMIX.safetensors");
    // 其他节点保持引用相等
    expect(next["2"]).toBe(WORKFLOW["2"]);
  });
});

describe("ModelNamePicker", () => {
  it("下拉按类型过滤 + 搜索 + 选择回调", async () => {
    server.use(mockLibrary());
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <ModelNamePicker
        value="majicMIX.safetensors"
        expectedTypes={["checkpoints"]}
        onChange={onChange}
      />,
      { wrapper },
    );
    await user.click(screen.getByRole("button", { name: "settings.library.picker.button" }));
    // 仅 checkpoints 两条，loras 不出现
    expect(await screen.findByText("animagineXL40.safetensors")).toBeInTheDocument();
    expect(screen.queryByText("anime-lora.pt")).not.toBeInTheDocument();
    // 搜索过滤（断言范围限定在 listbox；触发按钮仍显示当前值）
    await user.type(
      screen.getByLabelText("settings.library.picker.searchPlaceholder"),
      "animagine",
    );
    const listbox = screen.getByRole("listbox");
    expect(within(listbox).queryByText("majicMIX.safetensors")).not.toBeInTheDocument();
    await user.click(within(listbox).getByText("animagineXL40.safetensors"));
    expect(onChange).toHaveBeenCalledWith("animagineXL40.safetensors");
  });

  it("当前值不在库中显示警示", async () => {
    server.use(mockLibrary());
    const user = userEvent.setup();
    render(
      <ModelNamePicker
        value="ghost.safetensors"
        expectedTypes={["checkpoints"]}
        onChange={() => {}}
      />,
      { wrapper },
    );
    expect(
      screen.getByLabelText("settings.library.picker.notInLibrary"),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "settings.library.picker.button" }));
    expect(await screen.findByText("animagineXL40.safetensors")).toBeInTheDocument();
  });

  it("无候选类型时空态", async () => {
    server.use(mockLibrary());
    const user = userEvent.setup();
    render(
      <ModelNamePicker value="" expectedTypes={["controlnet"]} onChange={() => {}} />,
      { wrapper },
    );
    await user.click(screen.getByRole("button", { name: "settings.library.picker.button" }));
    expect(
      await screen.findByText("settings.library.picker.noCandidates"),
    ).toBeInTheDocument();
  });
});

describe("WorkflowRefsPanel", () => {
  it("渲染引用行并通过 picker 回写 JSON", async () => {
    server.use(mockLibrary());
    const user = userEvent.setup();
    const onRewrite = vi.fn();
    render(
      <WorkflowRefsPanel draftText={JSON.stringify(WORKFLOW, null, 2)} onRewrite={onRewrite} />,
      { wrapper },
    );
    // 两行引用（ckpt + lora），标题带计数
    expect(
      screen.getByText(/settings.library.refs.title/),
    ).toBeInTheDocument();
    const pickers = screen.getAllByRole("button", { name: /CheckpointLoaderSimple|LoraLoader/ });
    expect(pickers).toHaveLength(2);
    // 打开 lora 行 picker 选择 anime-lora.pt
    await user.click(pickers[1]);
    await user.click(await screen.findByText("anime-lora.pt"));
    expect(onRewrite).toHaveBeenCalledTimes(1);
    const nextJson = JSON.parse(onRewrite.mock.calls[0][0]) as typeof WORKFLOW;
    expect(nextJson["2"].inputs.lora_name).toBe("anime-lora.pt");
    expect(nextJson["1"].inputs.ckpt_name).toBe("majicMIX.safetensors");
  });

  it("体检：缺失项红叉 + 汇总文案", async () => {
    server.use(
      mockLibrary(),
      http.post(`${BASE}/preflight`, () =>
        HttpResponse.json({
          ok: true,
          data: {
            refs: [
              {
                node_id: "1",
                class_type: "CheckpointLoaderSimple",
                field: "ckpt_name",
                filename: "majicMIX.safetensors",
                expected_types: ["checkpoints"],
                present: true,
                present_anywhere: true,
              },
              {
                node_id: "2",
                class_type: "LoraLoader",
                field: "lora_name",
                filename: "missing-lora.pt",
                expected_types: ["loras"],
                present: false,
                present_anywhere: false,
              },
            ],
            missing: [
              {
                node_id: "2",
                class_type: "LoraLoader",
                field: "lora_name",
                filename: "missing-lora.pt",
                expected_types: ["loras"],
                present: false,
                present_anywhere: false,
              },
            ],
            total: 2,
            missing_count: 1,
            checked_at: 1,
          },
        }),
      ),
    );
    const user = userEvent.setup();
    render(
      <WorkflowRefsPanel draftText={JSON.stringify(WORKFLOW)} onRewrite={() => {}} />,
      { wrapper },
    );
    await user.click(screen.getByRole("button", { name: /settings.library.refs.preflight/ }));
    // 汇总文案（picker 当前值也含 missing-lora.pt，故校验汇总段本身）
    expect(
      await screen.findByText(/settings.library.refs.preflightMissing/),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("settings.library.refs.present")).toBeInTheDocument();
    expect(screen.getByLabelText("settings.library.refs.missing")).toBeInTheDocument();
  });

  it("体检全过文案", async () => {
    server.use(
      mockLibrary(),
      http.post(`${BASE}/preflight`, () =>
        HttpResponse.json({
          ok: true,
          data: { refs: [], missing: [], total: 2, missing_count: 0, checked_at: 1 },
        }),
      ),
    );
    const user = userEvent.setup();
    render(
      <WorkflowRefsPanel draftText={JSON.stringify(WORKFLOW)} onRewrite={() => {}} />,
      { wrapper },
    );
    await user.click(screen.getByRole("button", { name: /settings.library.refs.preflight/ }));
    expect(
      await screen.findByText(/settings.library.refs.preflightOk/),
    ).toBeInTheDocument();
  });

  it("非法 JSON 与无引用两种提示", () => {
    const { unmount } = render(
      <WorkflowRefsPanel draftText="{broken" onRewrite={() => {}} />,
      { wrapper },
    );
    expect(screen.getByText("settings.library.refs.invalidJson")).toBeInTheDocument();
    unmount();
    render(
      <WorkflowRefsPanel draftText='{"1":{"class_type":"KSampler","inputs":{}}}' onRewrite={() => {}} />,
      { wrapper },
    );
    expect(screen.getByText("settings.library.refs.noRefs")).toBeInTheDocument();
  });

  it("缺失一键补齐：缺失行显示「去下载」，点击写入跨页下载请求", async () => {
    server.use(mockLibrary());
    const user = userEvent.setup();
    render(
      <WorkflowRefsPanel draftText={JSON.stringify(WORKFLOW)} onRewrite={() => {}} />,
      { wrapper },
    );
    // 等库加载完成（picker 候选来自库查询）
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "settings.library.refs.goToDownload" }),
      ).toBeInTheDocument(),
    );
    // 仅缺失的 lora 行有按钮（missing-lora.pt 不在库；majicMIX 在位无按钮）
    expect(
      screen.getAllByRole("button", { name: "settings.library.refs.goToDownload" }),
    ).toHaveLength(1);
    await user.click(
      screen.getByRole("button", { name: "settings.library.refs.goToDownload" }),
    );
    const pending = useDownloadRequestStore.getState().pending;
    expect(pending).toMatchObject({ query: "missing lora", subdir: "loras" });
  });
});

describe("filenameToQuery", () => {
  it("去扩展名并把 -_ 转为空格", () => {
    expect(filenameToQuery("missing-lora.pt")).toBe("missing lora");
    expect(filenameToQuery("ip-adapter-plus-face_sdxl_vit-h.safetensors")).toBe(
      "ip adapter plus face sdxl vit h",
    );
    expect(filenameToQuery("majicMIX.safetensors")).toBe("majicMIX");
  });
});
