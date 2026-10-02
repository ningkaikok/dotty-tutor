// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { PromptDetail } from "../../api/prompts";
import { PromptManagerApp } from "./PromptManagerApp";

function fixture(publisher = false, conflict = false) {
  const detail: PromptDetail = {
    id: "generation.solution", variables: ["question_ir", "repair"], editable: true, activeRevisionId: "baseline", events: [],
    revisions: [{ revisionId: "baseline", templateId: "generation.solution", version: "v1", contentHash: "a".repeat(64), text: "原规则 ${question_ir}${repair}", baseRevisionId: null, createdAt: 0, createdBy: "git", previewed: true, published: true }],
  };
  vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
    const body = init?.body ? JSON.parse(String(init.body)) : {};
    let result: unknown;
    if (input.endsWith("/drafts")) {
      result = { ...detail.revisions[0], revisionId: "draft", version: "online-draft", contentHash: "b".repeat(64), text: body.text, baseRevisionId: body.baseRevisionId, previewed: false, published: false };
      detail.revisions.unshift(result as PromptDetail["revisions"][number]);
    } else if (input.endsWith("/preview")) {
      const revision = detail.revisions.find((item) => item.revisionId === body.revisionId)!;
      revision.previewed = true;
      result = { rendered: revision.text.replace("${question_ir}", body.variables.question_ir).replace("${repair}", body.variables.repair) };
    } else if (input.endsWith("/activate")) {
      if (conflict) return { ok: false, json: async () => ({ message: "发布版本已变化，请刷新后比较再操作" }) };
      detail.events.unshift({ revisionId: body.revisionId, previousRevisionId: detail.activeRevisionId, action: body.action, createdAt: 1, createdBy: "content-publisher" });
      detail.activeRevisionId = body.revisionId;
      detail.revisions.find((item) => item.revisionId === body.revisionId)!.published = true;
      result = detail;
    } else if (input.endsWith("/generation.solution")) result = detail;
    else result = { canPublish: publisher, items: [detail] };
    return { ok: true, json: async () => JSON.parse(JSON.stringify(result)) };
  }));
}
async function connect(query = "") {
  render(<MemoryRouter initialEntries={[`/studio/prompts?template=generation.solution${query}`]}><PromptManagerApp /></MemoryRouter>);
  fireEvent.change(screen.getByLabelText("内容平台凭据"), { target: { value: "test-credential" } });
  fireEvent.click(screen.getByRole("button", { name: "进入管理" }));
  await screen.findByRole("heading", { name: "独立求解" });
  await waitFor(() => expect(screen.getByLabelText("提示词正文")).toHaveValue("原规则 ${question_ir}${repair}"));
}
async function savePreview() {
  fireEvent.change(screen.getByLabelText("提示词正文"), { target: { value: "新规则 ${question_ir}${repair}" } });
  expect(screen.getByRole("button", { name: "预览已保存版本" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "保存为新草稿" }));
  await screen.findByText(/草稿已保存/);
  fireEvent.click(screen.getByRole("button", { name: "预览已保存版本" }));
  await screen.findByRole("region", { name: "渲染预览" });
}

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
describe("内容平台提示词管理", () => {
  it("user editor saves and previews a draft without publishing permissions", async () => {
    fixture(); await connect();
    expect(JSON.parse((screen.getByLabelText("样例变量") as HTMLTextAreaElement).value).repair).toBe("");
    await savePreview();
    expect(screen.getByText(/当前生效：v1/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "发布此草稿" })).not.toBeInTheDocument();
    expect(screen.getByRole("region", { name: "渲染预览" })).toHaveTextContent("新规则");
    fireEvent.click(screen.getByRole("button", { name: "退出管理" }));
    expect(screen.getByLabelText("内容平台凭据")).toHaveValue("");
    expect(screen.queryByLabelText("提示词正文")).not.toBeInTheDocument();
  });
  it("user publisher publishes a previewed draft and rolls back to the archived original", async () => {
    fixture(true); await connect(); await savePreview();
    fireEvent.click(screen.getByRole("button", { name: "发布此草稿" }));
    await screen.findByText(/已发布。新任务/);
    expect(screen.getByText(/当前生效：online-draft/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("查看版本"), { target: { value: "baseline" } });
    fireEvent.click(screen.getByRole("button", { name: "回滚到此版本" }));
    await screen.findByText(/已回滚。新任务/);
    expect(screen.getByText(/当前生效：v1/)).toBeInTheDocument();
  });
  it("user sees missing historical revision explicitly instead of treating the current template as history", async () => {
    fixture(); await connect("&hash=missing");
    expect(screen.getByRole("alert")).toHaveTextContent("历史版本尚未归档");
  });
  it("user sees publication conflict and the original active version remains visible", async () => {
    fixture(true, true); await connect(); await savePreview();
    fireEvent.click(screen.getByRole("button", { name: "发布此草稿" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("发布版本已变化");
    expect(screen.getByText(/当前生效：v1/)).toBeInTheDocument();
  });
});
