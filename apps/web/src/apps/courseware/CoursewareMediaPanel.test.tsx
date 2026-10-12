// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { CoursewareMediaPanel } from "./CoursewareMediaPanel";
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
it("user Given an immutable publication When selecting three pages and creating video Then only the pinned page identifiers enter the courseware request", async () => {
  let saved: unknown;
  vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
    if (url.includes("/presentation")) return new Response(JSON.stringify({ document: { chapterId: "chapter", sourceRevisionId: "frozen-source", title: "Original publication",
      pages: [1, 2, 3].map((n) => ({ pageId: `pub-page-${n}`, title: `原发布页 ${n}` })) } }));
    if (init?.method === "POST") { saved = JSON.parse(String(init.body)); return new Response(JSON.stringify({ mediaId: "draft", title: "Draft", version: 1, scenes: [], sourceSnapshot: {}, rendererSnapshot: {}, reviews: [], status: "draft" })); }
    return new Response(JSON.stringify({ items: [] }));
  });
  render(<CoursewareMediaPanel coursewareId="courseware" publicationId="immutable-publication" />);
  const button = await screen.findByRole("button", { name: "从选定发布页创建分镜" });
  await userEvent.click(button);
  expect(saved).toEqual({ publicationId: "immutable-publication", sourceRevisionId: "frozen-source", pageIds: ["pub-page-1", "pub-page-2", "pub-page-3"] });
});
