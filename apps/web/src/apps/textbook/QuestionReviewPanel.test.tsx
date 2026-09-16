// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { QuestionPayload } from "../../types/question";
import type { QuestionReviewItem, RevisionSummary } from "../../types/textbook";
import { QuestionReviewPanel } from "./QuestionReviewPanel";

const UPLOAD_ID = "upload-1";
const SOURCE_KEY = "batch-001-q-1";

function buildPayload(prompt: string, correctAnswer: string): QuestionPayload {
  return {
    question: {
      id: "question-batch-001-q-1",
      chapter: "章节",
      knowledgePoint: "知识点",
      prompt,
      correctAnswer,
      givens: [],
      contentBlocks: [],
      sourceQuestionKey: SOURCE_KEY,
    },
    lessonSteps: [],
    architecture: {},
    modelRun: { requestedProvider: "mock", provider: "mock", model: "test", fallback: false },
  };
}

function buildReview(payload: QuestionPayload, currentRevisionId: string | null): QuestionReviewItem {
  return {
    uploadId: UPLOAD_ID,
    sourceQuestionKey: SOURCE_KEY,
    questionPayload: payload,
    provenance: {},
    issues: [],
    stageRuns: [],
    currentRevisionId,
  };
}

/** 匹配现有测试文件的风格：用一个可变的服务端状态驱动 `fetch` mock，而不是逐条硬编码 URL。 */
function stubServer(initial: { revisions: RevisionSummary[]; currentRevisionId: string | null; payload: QuestionPayload }) {
  const state = { ...initial };
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = (init?.method || "GET").toUpperCase();

    if (url.includes("/review-queue/") && method === "GET") {
      return jsonResponse(buildReview(state.payload, state.currentRevisionId));
    }
    if (url.endsWith("/revisions") && method === "GET") {
      return jsonResponse(state.revisions);
    }
    if (url.match(/\/questions\/[^/]+$/) && method === "PATCH") {
      const body = JSON.parse(String(init?.body ?? "{}"));
      if ((body.baseRevisionId ?? null) !== state.currentRevisionId) {
        return jsonResponse({ detail: { message: "题目已被其他修改更新，请基于最新版本重新编辑" } }, 409);
      }
      const nextPayload = buildPayload(body.prompt ?? state.payload.question.prompt, body.correctAnswer ?? state.payload.question.correctAnswer ?? "");
      const revisionNumber = state.revisions.length + 1;
      const revision: RevisionSummary = {
        revisionId: `rev-${revisionNumber}`,
        uploadId: UPLOAD_ID,
        sourceQuestionKey: SOURCE_KEY,
        revisionNumber,
        operation: "question_manual_edit",
        revisionSource: "manual_edit",
        previousRevisionId: state.currentRevisionId,
        runId: "run-1",
        createdAt: revisionNumber,
      };
      state.revisions = [...state.revisions, revision];
      state.currentRevisionId = revision.revisionId;
      state.payload = nextPayload;
      return jsonResponse({
        run: { runId: "run-1", operation: "question_manual_edit", scope: "question", status: "succeeded", config: {}, startedAt: 0 },
        batch: null,
        questionPayload: nextPayload,
        guideCards: [],
        edit: { scope: "question", operation: "question_manual_edit", fields: Object.keys(body).filter((key) => key !== "baseRevisionId") },
        revision,
      });
    }
    if (url.match(/\/revisions\/([^/]+)\/activate$/) && method === "POST") {
      const revisionId = url.match(/\/revisions\/([^/]+)\/activate$/)?.[1];
      const target = state.revisions.find((item) => item.revisionId === revisionId);
      if (!target) return jsonResponse({ detail: "这条历史修订不存在" }, 404);
      state.currentRevisionId = target.revisionId;
      state.payload = buildPayload(`第${target.revisionNumber}版题干`, "回滚答案");
      return jsonResponse({
        run: { runId: "run-2", operation: "question_revision_activate", scope: "question", status: "succeeded", config: {}, startedAt: 0 },
        batch: null,
        questionPayload: state.payload,
        guideCards: [],
        activation: { scope: "question", operation: "question_revision_activate", activatedRevisionId: target.revisionId },
        activatedRevision: target,
      });
    }
    throw new Error(`未预期的请求：${method} ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  return { fetchMock, state };
}

function jsonResponse(data: unknown, status = 200) {
  return { ok: status < 400, status, json: async () => data } as Response;
}

describe("QuestionReviewPanel", () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("编辑题干并保存后回填新版本并把新题目回传给父组件", async () => {
    const user = userEvent.setup();
    const payload = buildPayload("原始题干", "A");
    stubServer({ revisions: [], currentRevisionId: null, payload });
    const onPayload = vi.fn();

    render(<QuestionReviewPanel uploadId={UPLOAD_ID} payload={payload} onPayload={onPayload} />);

    await user.click(await screen.findByRole("button", { name: "人工编辑" }));
    const textarea = screen.getByLabelText("题干");
    await user.clear(textarea);
    await user.type(textarea, "老师改写后的题干");
    await user.click(screen.getByRole("button", { name: "保存编辑" }));

    await waitFor(() => expect(onPayload).toHaveBeenCalled());
    expect(onPayload.mock.calls[0][0].question.prompt).toBe("老师改写后的题干");
    expect(await screen.findByText(/第 1 版 · 人工编辑 · 当前版本/)).toBeInTheDocument();
    // 保存成功后编辑表单应当收起。
    expect(screen.queryByLabelText("题干")).not.toBeInTheDocument();
  });

  it("历史版本列表可以把某个旧版本设为当前版本", async () => {
    const user = userEvent.setup();
    const revisionOne: RevisionSummary = {
      revisionId: "rev-1", uploadId: UPLOAD_ID, sourceQuestionKey: SOURCE_KEY, revisionNumber: 1,
      operation: "question_manual_edit", revisionSource: "manual_edit", previousRevisionId: null,
      runId: "run-1", createdAt: 1,
    };
    const revisionTwo: RevisionSummary = {
      revisionId: "rev-2", uploadId: UPLOAD_ID, sourceQuestionKey: SOURCE_KEY, revisionNumber: 2,
      operation: "question_manual_edit", revisionSource: "manual_edit", previousRevisionId: "rev-1",
      runId: "run-1", createdAt: 2,
    };
    const payload = buildPayload("第二版题干", "B");
    const { fetchMock } = stubServer({ revisions: [revisionOne, revisionTwo], currentRevisionId: "rev-2", payload });
    const onPayload = vi.fn();

    render(<QuestionReviewPanel uploadId={UPLOAD_ID} payload={payload} onPayload={onPayload} />);

    const firstItem = (await screen.findByText(/第 1 版/)).closest("li");
    expect(firstItem).not.toBeNull();
    await user.click(within(firstItem as HTMLElement).getByRole("button", { name: /设为当前版本/ }));

    await waitFor(() => expect(onPayload).toHaveBeenCalled());
    expect(onPayload.mock.calls[0][0].question.prompt).toBe("第1版题干");
    expect(await screen.findByText(/第 1 版 · 人工编辑 · 当前版本/)).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/questions/batch-001-q-1/revisions/rev-1/activate"),
      expect.objectContaining({ method: "POST" }),
    );
  });
});
