// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { CoursewareCoveragePanel } from "./CoursewareCoveragePanel";
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
it("user Given a partially reviewed unit When opening coverage Then missing original pages remain visible without claiming quality approval", async () => {
  vi.stubGlobal("fetch", async () => new Response(JSON.stringify({ chapterId: "c", sourceRevisionId: "r", pageStart: 1, pageEnd: 4,
    missingSourcePages: [], unplannedPages: [4], ungeneratedPages: [4], unreviewedPages: [3, 4], unpublishedPages: [1, 2, 3, 4], complete: false })));
  render(<CoursewareCoveragePanel chapterId="c" />);
  expect(await screen.findByRole("region", { name: "完整单元覆盖" })).toHaveTextContent("仍有缺口");
  expect(screen.getByRole("region")).toHaveTextContent("原页 3、4");
  expect(screen.getByRole("region")).toHaveTextContent("教学质量仍需教师验收");
});
it("user Given a coverage request failure When opening the unit Then the failure is not displayed as complete", async () => {
  vi.stubGlobal("fetch", async () => new Response(JSON.stringify({ detail: "Source missing" }), { status: 409 }));
  render(<CoursewareCoveragePanel chapterId="c" />);
  expect(await screen.findByRole("alert")).toHaveTextContent("完整单元覆盖暂时无法读取");
  expect(screen.queryByText("结构覆盖完整")).not.toBeInTheDocument();
});
