import { fireEvent, render, screen } from "@testing-library/react";
import {
  ComboInput,
  SmartSelect,
  modalScrollStyle,
  GENRE_OPTIONS,
  STYLE_OPTIONS,
} from "./shared";

/**
 * modals/shared.tsx 契约与边界测试。
 * 覆盖 2026-08-15 UI 打磨的模态滚动修复（overflowX:hidden 防横向滚动条）
 * 与 ComboInput/SmartSelect 的输入边界。
 */
describe("modalScrollStyle 契约（模态滚动修复回归）", () => {
  it("纵向可滚、横向禁滚、限高 86vh", () => {
    expect(modalScrollStyle.maxHeight).toBe("86vh");
    expect(modalScrollStyle.overflowY).toBe("auto");
    expect(modalScrollStyle.overflowX).toBe("hidden");
  });
});

describe("ComboInput（下拉预设 + 自定义输入）", () => {
  it("渲染输入框与 datalist 预设选项", () => {
    render(
      <ComboInput value="写实电影感" onChange={() => {}} options={STYLE_OPTIONS} />
    );
    const input = screen.getByRole("combobox") as HTMLInputElement;
    expect(input.value).toBe("写实电影感");
    const listId = input.getAttribute("list");
    expect(listId).toBeTruthy();
    const datalist = document.getElementById(listId!);
    expect(datalist).toBeInTheDocument();
    expect(datalist!.querySelectorAll("option").length).toBe(STYLE_OPTIONS.length);
  });

  it("输入变化触发 onChange（用户自定义值）", () => {
    const onChange = vi.fn();
    render(<ComboInput value="" onChange={onChange} options={GENRE_OPTIONS} />);
    fireEvent.change(screen.getByRole("combobox"), {
      target: { value: "自定义题材" },
    });
    expect(onChange).toHaveBeenCalledWith("自定义题材");
  });

  it("placeholder 透传", () => {
    render(
      <ComboInput value="" onChange={() => {}} options={[]} placeholder="请输入画风" />
    );
    expect(screen.getByPlaceholderText("请输入画风")).toBeInTheDocument();
  });

  it("空 options 边界：不崩溃且无 option", () => {
    render(<ComboInput value="x" onChange={() => {}} options={[]} />);
    const input = screen.getByRole("combobox") as HTMLInputElement;
    const datalist = document.getElementById(input.getAttribute("list")!);
    expect(datalist!.querySelectorAll("option").length).toBe(0);
  });
});

describe("SmartSelect（智能下拉：自定义值自动追加）", () => {
  it("当前值在预设中 → 选项数等于预设数，无重复追加", () => {
    render(
      <SmartSelect value="日系动漫" onChange={() => {}} options={STYLE_OPTIONS} />
    );
    const select = screen.getByRole("combobox") as HTMLSelectElement;
    expect(select.value).toBe("日系动漫");
    expect(select.querySelectorAll("option").length).toBe(STYLE_OPTIONS.length);
  });

  it("当前值不在预设中 → 自动追加为首选项且不重复", () => {
    render(
      <SmartSelect value="黏土定格" onChange={() => {}} options={STYLE_OPTIONS} />
    );
    const select = screen.getByRole("combobox") as HTMLSelectElement;
    const options = Array.from(select.querySelectorAll("option")).map((o) => o.value);
    expect(options[0]).toBe("黏土定格");
    expect(options.filter((v) => v === "黏土定格").length).toBe(1);
    expect(options.length).toBe(STYLE_OPTIONS.length + 1);
  });

  it("空值边界 → 不追加，选项数等于预设数", () => {
    render(<SmartSelect value="" onChange={() => {}} options={STYLE_OPTIONS} />);
    const select = screen.getByRole("combobox") as HTMLSelectElement;
    expect(select.querySelectorAll("option").length).toBe(STYLE_OPTIONS.length);
  });

  it("切换选项触发 onChange", () => {
    const onChange = vi.fn();
    render(
      <SmartSelect value="写实电影感" onChange={onChange} options={STYLE_OPTIONS} />
    );
    fireEvent.change(screen.getByRole("combobox"), {
      target: { value: "赛博朋克" },
    });
    expect(onChange).toHaveBeenCalledWith("赛博朋克");
  });
});
