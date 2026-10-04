// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, expect, it, vi } from "vitest";
import { MaterialsApp } from "./MaterialsApp";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });
const course = { chapterId: "en-1", title: "A day outdoors", subject: "english", status: "in_review", version: 1, recordVersion: 2, currentLessonIds: ["lesson-1"], reviewIssues: [], publicationId: null };
const upload = { uploadId: "pdf-1", filename: "数学教材.pdf", pageCount: 12, questionCount: 4, status: "complete" };
function setup(failedUploads = false) {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (url) => new Response(JSON.stringify({ items: String(url).endsWith("/api/library") ? [upload] : [course] }), { status: failedUploads && String(url).endsWith("/api/library") ? 500 : 200, headers: { "Content-Type": "application/json" } }));
  render(<MemoryRouter><MaterialsApp /></MemoryRouter>);
}
it("user Given an uploaded PDF and an English course When opening my materials Then both can be continued from one library", async () => {
  setup();
  expect(await screen.findByRole("heading", { name: "A day outdoors" })).toBeVisible();
  expect(screen.getByRole("heading", { name: "数学教材.pdf" })).toBeVisible();
  expect(screen.getByRole("link", { name: "继续复核 →" })).toHaveAttribute("href", "/studio/chapters/en-1");
  expect(screen.getByRole("link", { name: "选取页码，制作课程" })).toHaveAttribute("href", "/studio/chapters/new?uploadId=pdf-1");
  expect(screen.getByRole("link", { name: "查看练习 →" })).toHaveAttribute("href", "/studio/import?uploadId=pdf-1");
  fireEvent.change(screen.getByRole("searchbox"), { target: { value: "英语" } });
  expect(screen.queryByRole("heading", { name: "数学教材.pdf" })).not.toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "A day outdoors" })).toBeVisible();
});
it("user Given upload records cannot be fetched When opening the library Then English courses remain available with a specific retry message", async () => {
  setup(true);
  expect(await screen.findByRole("heading", { name: "A day outdoors" })).toBeVisible();
  expect(screen.getByRole("alert")).toHaveTextContent("已上传教材暂时无法读取");
  expect(screen.queryByText("从一本教材开始")).not.toBeInTheDocument();
});
