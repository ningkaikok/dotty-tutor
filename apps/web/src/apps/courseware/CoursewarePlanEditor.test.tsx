// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { CoursewarePlanEditor } from "./CoursewarePlanEditor";
import type { CoursewarePlanView } from "./coursewarePlanView";

afterEach(cleanup);

const plan = {
  schemaVersion: 1, chapterId: "chapter-1", sourceRevisionId: "revision-1", subject: "english", audience: "Grade 8",
  languagePolicy: "English", pageStart: 23, pageEnd: 25, objectives: [{ objectiveId: "goal-1", description: "Use one source detail", sourceRefs: [{ sourceRevisionId: "revision-1", page: 23 }] }],
  sessions: [{ sessionId: "session-1", title: "Unit 1", durationMinutes: 40, sections: [{ sectionId: "section-1", title: "Opening", purpose: "Begin", estimatedMinutes: 4, pages: [{ pageId: "page-1", title: "Opening", pageKind: "cover", layout: "hero_split", objectiveIds: ["goal-1"], sourceRefs: [], estimatedMinutes: 4, required: true }] }] }],
  status: "awaiting_confirmation", planVersion: 1, recordVersion: 1, generationStatus: "completed", createdAt: 1, updatedAt: 1,
} as unknown as CoursewarePlanView;

it("user Given a plan covering source pages 23–25 When narrowing it outside the source bounds Then the entered range stays visible and confirmation is blocked with an explanation", async () => {
  const user = userEvent.setup();
  const onSave = vi.fn(async () => undefined);
  const onApprove = vi.fn(async () => undefined);
  render(<CoursewarePlanEditor plan={plan} sourceBounds={{ start: 23, end: 52 }} busy={false} onSave={onSave} onApprove={onApprove} />);

  const startPage = screen.getByLabelText("规划来源起始页");
  await user.clear(startPage);
  await user.type(startPage, "22");

  expect(startPage).toHaveValue(22);
  expect(screen.getByRole("alert")).toHaveTextContent("教材第 23–52 页内");
  expect(screen.getByRole("button", { name: "确认规划并生成课件" })).toBeDisabled();
  expect(onSave).not.toHaveBeenCalled();
  expect(onApprove).not.toHaveBeenCalled();
});
