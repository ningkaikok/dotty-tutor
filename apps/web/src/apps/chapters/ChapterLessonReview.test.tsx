// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { loadChapter } from "../../api/chapters";
import type { ChapterLessonEditInput } from "../../types/chapter";
import { ChapterLessonReview } from "./ChapterLessonReview";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("user Given an unauthored reading item When the teacher selects a source sentence Then the saved question includes that evidence", async () => {
  const locator = { sourceRevisionId: "revision-1", page: 1, regions: [] };
  const reference = { ...locator, sentenceId: "sentence-1", quote: "Mina moved to Boston." };
  vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({
    chapterId: "chapter-1", subject: "english", title: "Reading", status: "in_review",
    version: 1, recordVersion: 2, sourceRevisions: [], currentLessonIds: ["lesson-1"],
    reviewIssues: [], publications: [], publicationId: null,
    lessons: [{
      lessonId: "lesson-1", title: "Reading", version: 1, status: "in_review",
      sourceRevisionId: "revision-1", sourceLocator: locator, knowledgePoints: [],
      blocks: [{ id: "concept", type: "markdown", title: "原文", payload: { markdown: reference.quote } }],
      questionPayload: { question: { id: "lesson-1", prompt: "请补充检查题", questionType: "short-answer", requiredEvidenceRefs: [] } },
      evidenceOptions: [{ ...reference, label: reference.quote }], reviewIssues: [],
    }],
  }), { status: 200, headers: { "Content-Type": "application/json" } }));
  const chapter = await loadChapter("chapter-1");
  let saved: ChapterLessonEditInput | undefined;
  render(<ChapterLessonReview lessons={chapter.lessons} busyLessonId="" busyAction=""
    onEdit={async (_lesson, value) => { saved = value; return chapter; }}
    onReview={() => undefined} onFocusSource={() => undefined} />);

  fireEvent.click(screen.getByRole("button", { name: "补充检查题" }));
  fireEvent.change(screen.getByLabelText("检查题"), { target: { value: "Where did Mina move?" } });
  fireEvent.change(screen.getByLabelText("标准答案"), { target: { value: "Boston" } });
  fireEvent.click(screen.getByLabelText(/Mina moved to Boston/));
  fireEvent.click(screen.getByRole("button", { name: "保存题目" }));

  await waitFor(() => expect(saved?.requiredEvidenceRefs).toEqual([{
    sourceRevisionId: "revision-1", page: 1, sentenceId: "sentence-1", quote: reference.quote,
  }]));
  expect(saved?.prompt).toBe("Where did Mina move?");
});
