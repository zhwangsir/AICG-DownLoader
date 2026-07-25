import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import { ThemeSwitcher } from "./ThemeSwitcher";

describe("ThemeSwitcher", () => {
  beforeEach(() => {
    localStorage.clear();
    document.documentElement.removeAttribute("data-theme");
    cleanup();
  });

  it("renders the theme toggle button", () => {
    render(<ThemeSwitcher />);
    expect(screen.getByRole("button", { name: "切换主题" })).toBeInTheDocument();
  });

  it("defaults to darkroom-amber and applies data-theme on html", () => {
    render(<ThemeSwitcher />);
    expect(document.documentElement.dataset.theme).toBe("darkroom-amber");
    expect(localStorage.getItem("film-atelier-theme")).toBe("darkroom-amber");
  });

  it("opens dropdown and lists three themes on click", () => {
    render(<ThemeSwitcher />);
    fireEvent.click(screen.getByRole("button", { name: "切换主题" }));
    expect(screen.getByText("暗房琥珀")).toBeInTheDocument();
    expect(screen.getByText("银盐冷调")).toBeInTheDocument();
    expect(screen.getByText("蓝晒")).toBeInTheDocument();
  });

  it("switches theme on option click and persists to localStorage", () => {
    render(<ThemeSwitcher />);
    fireEvent.click(screen.getByRole("button", { name: "切换主题" }));
    fireEvent.click(screen.getByText("蓝晒"));
    expect(document.documentElement.dataset.theme).toBe("cyanotype");
    expect(localStorage.getItem("film-atelier-theme")).toBe("cyanotype");
  });

  it("marks current theme as aria-checked", () => {
    render(<ThemeSwitcher />);
    fireEvent.click(screen.getByRole("button", { name: "切换主题" }));
    const amberOption = screen.getByText("暗房琥珀").closest("button");
    expect(amberOption?.getAttribute("aria-checked")).toBe("true");
    const cyanOption = screen.getByText("蓝晒").closest("button");
    expect(cyanOption?.getAttribute("aria-checked")).toBe("false");
  });

  it("hydrates from localStorage on subsequent mount", () => {
    localStorage.setItem("film-atelier-theme", "silver-halide");
    render(<ThemeSwitcher />);
    expect(document.documentElement.dataset.theme).toBe("silver-halide");
  });

  it("ignores invalid persisted value and falls back to default", () => {
    localStorage.setItem("film-atelier-theme", "nonexistent-theme");
    render(<ThemeSwitcher />);
    expect(document.documentElement.dataset.theme).toBe("darkroom-amber");
  });

  it("closes dropdown on outside click", () => {
    render(
      <div>
        <ThemeSwitcher />
        <div data-testid="outside" />
      </div>
    );
    fireEvent.click(screen.getByRole("button", { name: "切换主题" }));
    expect(screen.getByText("暗房琥珀")).toBeInTheDocument();
    fireEvent.mouseDown(screen.getByTestId("outside"));
    expect(screen.queryByText("暗房琥珀")).not.toBeInTheDocument();
  });
});
