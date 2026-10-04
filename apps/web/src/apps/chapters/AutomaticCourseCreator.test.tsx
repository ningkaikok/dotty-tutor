// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, expect, it, vi } from "vitest";
import { AutomaticCourseCreator } from "./AutomaticCourseCreator";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });
const completed = { jobId: "prepare", status: "succeeded", result: {
  chapterLimit: 5, chapters: [{ chapterId: "first", title: "Unit 1 Hello", pageStart: 3, pageEnd: 6 }],
  notices: ["来源许可待复核"],
} };

it("user Given an uploaded textbook When making courses Then no source options are required and created chapters can be reviewed", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify(completed)));
  render(<MemoryRouter><AutomaticCourseCreator uploadId="source" /></MemoryRouter>);
  expect(await screen.findByRole("link", { name: "Unit 1 Hello · 第 3–6 页 →" })).toHaveAttribute("href", "/studio/chapters/first");
  expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
  expect(screen.getByText("来源许可待复核")).toBeVisible();
});

it("user Given preparation failed When retrying Then the existing job is retried and chapter links become available", async () => {
  let retried = false;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (url) => {
    if (String(url).endsWith("/retry")) retried = true;
    return new Response(JSON.stringify(retried ? completed : { jobId: "prepare", status: "failed", lastError: { message: "OCR 暂时不可用" } }));
  });
  render(<MemoryRouter><AutomaticCourseCreator uploadId="source" /></MemoryRouter>);
  expect(await screen.findByRole("alert")).toHaveTextContent("OCR 暂时不可用");
  fireEvent.click(screen.getByRole("button", { name: "重试制作" }));
  expect(await screen.findByRole("link", { name: "Unit 1 Hello · 第 3–6 页 →" })).toBeVisible();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});
