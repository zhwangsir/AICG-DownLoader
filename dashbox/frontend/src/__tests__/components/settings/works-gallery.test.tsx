// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import ky from "ky";
import type { ReactNode } from "react";
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

vi.mock("@/lib/api", () => ({
  api: ky.create({ baseUrl: "http://localhost:3000/" }),
}));

import { WorksGallery } from "@/components/settings/works-gallery";

const server = setupServer();
const BASE = "http://localhost:3000/api/v1/works";

beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function wrapper({ children }: { children: ReactNode }) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}

function mockWorks(items: unknown[]) {
  return http.get(`${BASE}`, () =>
    HttpResponse.json({ ok: true, data: { items, total: items.length } }),
  );
}

const WORK = {
  id: "N1",
  title: "夜樱站台",
  titleEn: "Night Sakura Platform",
  category: "anime",
  duration: "5s",
  engine: "Wan 2.2",
  features: ["动漫", "5s"],
  nsfw: false,
  desc: "desc",
  has_cover: true,
};

const WORK_R18 = {
  ...WORK,
  id: "R1",
  title: "蜜月之夜",
  engine: "Wan 2.2 + DR34ML4Y",
  nsfw: true,
  features: ["R18", "完整动作"],
  category: "real",
};

describe("WorksGallery — 作品库画廊", () => {
  it("渲染作品卡片（标题/引擎/时长徽章/R18 徽章）", async () => {
    server.use(mockWorks([WORK, WORK_R18]));
    render(<WorksGallery />, { wrapper });
    expect(await screen.findByText("夜樱站台")).toBeInTheDocument();
    expect(screen.getByText("Wan 2.2")).toBeInTheDocument();
    const card = screen.getByTestId("works-card-N1");
    expect(card).toHaveTextContent("5s");
    expect(screen.getByText("R18")).toBeInTheDocument();
    expect(screen.getByTestId("works-card-R1")).toBeInTheDocument();
  });

  it("空态与错误态", async () => {
    server.use(mockWorks([]));
    const { unmount } = render(<WorksGallery />, { wrapper });
    expect(await screen.findByText("settings.library.works.empty")).toBeInTheDocument();
    unmount();

    server.use(http.get(`${BASE}`, () => HttpResponse.error()));
    render(<WorksGallery />, { wrapper });
    expect(await screen.findByText("settings.library.works.loadError")).toBeInTheDocument();
  });

  it("赛道过滤 chip 触发 category 参数请求", async () => {
    let lastUrl = "";
    server.use(
      http.get(`${BASE}`, ({ request }) => {
        lastUrl = request.url;
        return HttpResponse.json({ ok: true, data: { items: [WORK], total: 1 } });
      }),
    );
    const user = userEvent.setup();
    render(<WorksGallery />, { wrapper });
    await screen.findByText("夜樱站台");
    const chip = screen.getByTestId("works-category-anime");
    expect(chip).toHaveAttribute("aria-selected", "false");
    await user.click(chip);
    await waitFor(() => expect(lastUrl).toContain("category=anime"));
    expect(chip).toHaveAttribute("aria-selected", "true");
  });

  it("特性过滤 chip 触发 feature 参数请求", async () => {
    let lastUrl = "";
    server.use(
      http.get(`${BASE}`, ({ request }) => {
        lastUrl = request.url;
        return HttpResponse.json({ ok: true, data: { items: [WORK_R18], total: 1 } });
      }),
    );
    const user = userEvent.setup();
    render(<WorksGallery />, { wrapper });
    await screen.findByText("蜜月之夜");
    await user.click(screen.getByTestId("works-feature-完整动作"));
    await waitFor(() => expect(lastUrl).toContain("feature="));
  });

  it("搜索提交 q 参数", async () => {
    let lastUrl = "";
    server.use(
      http.get(`${BASE}`, ({ request }) => {
        lastUrl = request.url;
        return HttpResponse.json({ ok: true, data: { items: [], total: 0 } });
      }),
    );
    const user = userEvent.setup();
    render(<WorksGallery />, { wrapper });
    const input = screen.getByTestId("works-search");
    await user.type(input, "便利店");
    await user.type(input, "{Enter}");
    await waitFor(() => expect(lastUrl).toContain("q="));
  });

  it("点击卡片打开播放器（video url 指向 media 端点）", async () => {
    server.use(mockWorks([WORK]));
    const user = userEvent.setup();
    const { container } = render(<WorksGallery />, { wrapper });
    await user.click(await screen.findByTestId("works-card-N1"));
    await waitFor(() => {
      const video = container.querySelector("video");
      expect(video).not.toBeNull();
      expect(video?.getAttribute("src")).toContain("api/v1/works/N1/media");
    });
  });
});
