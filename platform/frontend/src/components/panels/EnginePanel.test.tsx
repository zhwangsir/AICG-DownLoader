import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { vi } from "vitest";
import EnginePanel from "./EnginePanel";

vi.mock("../../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api/client")>();
  return {
    ...actual,
    getPanelStatus: vi.fn(),
  };
});

import { getPanelStatus } from "../../api/client";
const mockStatus = vi.mocked(getPanelStatus);

const SAMPLE: Awaited<ReturnType<typeof getPanelStatus>> = {
  backend: "ok",
  product: "AIGCPannel",
  downloader_config_path: "/tmp/config.json",
  downloader_config_readable: true,
  models_json_path: "/tmp/models.json",
  models_json_readable: false,
  dashbox: {
    web: "http://127.0.0.1:8080",
    api: "http://127.0.0.1:8780",
    note: "bundled",
    web_listening: true,
    api_listening: false,
  },
};

describe("EnginePanel", () => {
  beforeEach(() => {
    mockStatus.mockReset();
    mockStatus.mockResolvedValue(SAMPLE);
  });

  it("shows DashBox default URLs and ELv2 note", async () => {
    render(<EnginePanel />);
    expect(screen.getByText(/DramaClaw \/ DashBox/)).toBeInTheDocument();
    expect(screen.getByText(/Elastic License 2.0/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "刷新" })).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("http://127.0.0.1:8080")).toBeInTheDocument());
    expect(screen.getByText("http://127.0.0.1:8780")).toBeInTheDocument();
    expect(screen.getByText(/后端：ok/)).toBeInTheDocument();
    expect(screen.getByText(/models.json：不可读/)).toBeInTheDocument();
    expect(screen.getByText("在线")).toBeInTheDocument();
    expect(screen.getByText("离线")).toBeInTheDocument();
  });

  it("refresh re-calls GET /api/panel/status", async () => {
    render(<EnginePanel />);
    await waitFor(() => expect(mockStatus).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole("button", { name: "刷新" }));
    await waitFor(() => expect(mockStatus).toHaveBeenCalledTimes(2));
  });
});
