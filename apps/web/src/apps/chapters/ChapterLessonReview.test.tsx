// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
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

it("user Given AI answer variants, a rubric and three cited hints When the teacher edits a draft Then variants stay unapproved until selected and all hint levels remain editable", async () => {
  const sourceRef = { sourceRevisionId: "revision-1", page: 2, quote: "The source sentence." };
  const lesson = {
    lessonId: "lesson-ai", title: "AI 草稿", version: 1, status: "in_review", sourceRevisionId: "revision-1",
    sourceLocator: { sourceRevisionId: "revision-1", page: 2, regions: [] }, knowledgePoints: [],
    blocks: [
      { id: "concept", type: "markdown", title: "概念", payload: { markdown: "按来源归纳概念", sourceRefs: [sourceRef] } },
      { id: "hint-1", type: "hint", title: "第 1 级提示", payload: { level: 1, hint: "先找线索", sourceRefs: [sourceRef] } },
      { id: "hint-2", type: "hint", title: "第 2 级提示", payload: { level: 2, hint: "再比较条件", sourceRefs: [sourceRef] } },
      { id: "hint-3", type: "hint", title: "第 3 级提示", payload: { level: 3, hint: "结合上下文", sourceRefs: [sourceRef] } },
    ],
    questionPayload: { question: {
      id: "lesson-ai", prompt: "What happened?", questionType: "short-answer", acceptedAnswers: ["Mina left."],
      teacherVariants: ["Mina went away."], variantReviewStatus: "needs_teacher_review", rubric: { supportStatus: "needs_review", criteria: ["符合原文"] },
      requiredEvidenceRefs: [sourceRef],
    } },
    evidenceOptions: [{ ...sourceRef, label: sourceRef.quote }], reviewIssues: [],
  };
  vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({
    chapterId: "chapter-ai", subject: "english", title: "Reading", status: "in_review", version: 1, recordVersion: 2,
    sourceRevisions: [], currentLessonIds: ["lesson-ai"], reviewIssues: [], publications: [], publicationId: null,
    lessons: [lesson],
  }), { status: 200, headers: { "Content-Type": "application/json" } }));
  const chapter = await loadChapter("chapter-ai");
  let saved: ChapterLessonEditInput | undefined;
  render(<ChapterLessonReview lessons={chapter.lessons} busyLessonId="" busyAction=""
    onEdit={async (_lesson, value) => { saved = value; return chapter; }}
    onReview={() => undefined} onFocusSource={() => undefined} />);

  expect(screen.getByText("标准答案候选：Mina left.")).toBeInTheDocument();
  expect(screen.getByText(/尚未纳入自动判分/)).toBeInTheDocument();
  expect(screen.getByText("评分依据 · 需教师审核")).toBeInTheDocument();
  expect(screen.getByText(/生成内容引用（4 项）/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "编辑检查题" }));
  expect(screen.queryByLabelText("概念讲解")).not.toBeInTheDocument();
  expect(screen.getByLabelText("确认接受：Mina went away.")).not.toBeChecked();
  expect(screen.getByLabelText("标准答案")).toHaveValue("Mina left.");
  expect(screen.getByLabelText("第 1 级提示")).toHaveValue("先找线索");
  expect(screen.getByLabelText("第 2 级提示")).toHaveValue("再比较条件");
  expect(screen.getByLabelText("第 3 级提示")).toHaveValue("结合上下文");
  fireEvent.click(screen.getByLabelText("确认接受：Mina went away."));
  fireEvent.change(screen.getByLabelText("第 2 级提示"), { target: { value: "先对比两个情节" } });
  fireEvent.click(screen.getByRole("button", { name: "保存题目" }));

  await waitFor(() => expect(saved?.hints).toEqual(["先找线索", "先对比两个情节", "结合上下文"]));
  expect(saved?.acceptedAnswers).toContain("Mina went away.");
  expect(saved?.teacherVariants).toEqual(["Mina went away."]);
  expect(saved?.rubric).toEqual({ supportStatus: "needs_review", criteria: ["符合原文"] });
  expect(saved?.conceptMarkdown).toBeUndefined();
});
