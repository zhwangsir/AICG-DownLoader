// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import ky from "ky";
import type { ReactNode } from "react";
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", () => ({
  api: ky.create({ baseUrl: "http://localhost:3000/" }),
}));

import { BillingRuleNotConfiguredError } from "@/lib/api-errors";
import {
  useChapters,
  useStartIngest,
  useUploadNovel,
} from "@/lib/queries/ingest";

const server = setupServer();

beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

describe("ingest query error contract", () => {
  it("requests and caches chapter previews by spine template", async () => {
    let requestedTemplate = "";
    server.use(
      http.get("http://localhost:3000/api/v1/projects/demo/chapters", ({ request }) => {
        requestedTemplate = new URL(request.url).searchParams.get("spine_template") ?? "";
        return HttpResponse.json({
          ok: true,
          data: { chapters: [], total_chars: 0, count: 0 },
        });
      }),
    );

    const { result } = renderHook(() => useChapters("demo", "drama"), {
      wrapper,
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(requestedTemplate).toBe("drama");
  });

  it("rejects upload responses that return ok:false with a backend error", async () => {
    server.use(
      http.post("http://localhost:3000/api/v1/projects/demo/ingest/upload", async ({ request }) => {
        // 不断言 request.formData() / 文件名与内容：jsdom 的 File 与 undici
        // FormData 品牌检查不兼容，序列化后文件名与内容会丢失（仅测试环境如此，
        // 浏览器中为原生类不受影响）。仅校验 multipart 结构存在。
        const raw = await request.text();
        expect(raw).toContain('name="spine_template"');
        expect(raw).toContain("drama");
        expect(raw).toContain('name="file"');
        expect(raw).toContain("Content-Type: text/plain");
        return HttpResponse.json({ ok: false, error: "解析章节失败: 文件编码不支持" });
      }),
    );

    const { result } = renderHook(() => useUploadNovel("demo"), { wrapper });
    result.current.mutate({
      file: new File(["bad"], "broken.txt", { type: "text/plain" }),
      spineTemplate: "drama",
    });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.error).toBeInstanceOf(Error);
    expect(result.current.error?.message).toBe("解析章节失败: 文件编码不支持");
  });

  it("rejects start responses that return ok:false with a backend error", async () => {
    server.use(
      http.post("http://localhost:3000/api/v1/projects/demo/ingest/start", () =>
        HttpResponse.json({ ok: false, error: "File 'missing.txt' not found in uploads/" }),
      ),
    );

    const { result } = renderHook(() => useStartIngest("demo"), { wrapper });
    result.current.mutate({ filename: "missing.txt", rebuild: true });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.error).toBeInstanceOf(Error);
    expect(result.current.error?.message).toBe("File 'missing.txt' not found in uploads/");
  });

  it("maps start 409 billing rule responses to the billing rule error", async () => {
    server.use(
      http.post("http://localhost:3000/api/v1/projects/demo/ingest/start", () =>
        HttpResponse.json(
          {
            ok: false,
            error: "计费规则未配置，请联系管理员设置积分规则",
            data: {
              error_code: "BILLING_RULE_NOT_CONFIGURED",
              billing_kind: "feature",
              billing_key: "mainline.ingest_fast",
            },
          },
          { status: 409 },
        ),
      ),
    );

    const { result } = renderHook(() => useStartIngest("demo"), { wrapper });
    result.current.mutate({ filename: "novel.txt", rebuild: true });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.error).toBeInstanceOf(BillingRuleNotConfiguredError);
    expect(result.current.error?.message).toBe("计费规则未配置，请联系管理员设置积分规则");
  });
});
