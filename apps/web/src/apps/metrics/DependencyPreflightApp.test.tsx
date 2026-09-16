// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DependencyPreflightApp } from "./DependencyPreflightApp";

const okReport = {
  ok: true,
  checks: [
    { key: "pypdf", label: "pypdf 文字层解析", ok: true, detail: "pypdf 已安装（版本 6.18.1）", optional: false },
    { key: "postgresql", label: "PostgreSQL 数据库", ok: true, detail: "PostgreSQL 可达且 schema 就绪", optional: false },
    { key: "mineru", label: "MinerU OCR", ok: false, detail: "未找到 MinerU 命令；会回退到 pypdf 文字层", optional: true },
  ],
};

const failingReport = {
  ok: false,
  checks: [
    { key: "pypdf", label: "pypdf 文字层解析", ok: true, detail: "pypdf 已安装", optional: false },
    { key: "postgresql", label: "PostgreSQL 数据库", ok: false, detail: "连接失败", optional: false },
  ],
};

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/studio/dependency-preflight"]}>
      <DependencyPreflightApp />
    </MemoryRouter>,
  );
}

describe("DependencyPreflightApp", () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("renders every check with its required/optional badge", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, status: 200, json: async () => okReport })));
    renderPage();

    expect(screen.getByRole("status")).toHaveTextContent("正在检查依赖");
    expect(await screen.findByText("必需依赖全部就绪")).toBeVisible();
    expect(screen.getByText("MinerU OCR")).toBeVisible();
    expect(screen.getByText("未找到 MinerU 命令；会回退到 pypdf 文字层")).toBeVisible();
    expect(screen.getAllByText("必需")).toHaveLength(2);
    expect(screen.getAllByText("可选")).toHaveLength(1);
  });

  it("surfaces the overall failure banner when a required check fails", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, status: 200, json: async () => failingReport })));
    renderPage();

    expect(await screen.findByText("有必需依赖未就绪，对应功能会失败")).toBeVisible();
  });

  it("re-fetches the report when the refresh button is clicked", async () => {
    const fetchMock = vi.fn(async () => ({ ok: true, status: 200, json: async () => okReport }));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    renderPage();

    await screen.findByText("必需依赖全部就绪");
    await user.click(screen.getByRole("button", { name: "重新检查" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(fetchMock).toHaveBeenLastCalledWith("/api/system/dependency-preflight", { cache: "no-store" });
  });
});
