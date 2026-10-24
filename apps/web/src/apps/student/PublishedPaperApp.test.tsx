// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { loadPublicationMock, requestHelpMock, queueAttemptMock, sessionState } = vi.hoisted(() => ({
  loadPublicationMock: vi.fn(),
  requestHelpMock: vi.fn(),
  queueAttemptMock: vi.fn(),
  sessionState: {
    queueAttempt: vi.fn(),
    syncMessage: "学习记录已同步",
    mastery: [] as unknown[],
    attempts: [] as Array<Record<string, unknown>>,
    pendingAttemptIds: [] as string[],
    sessionReady: true,
  },
}));

vi.mock("../../api/publications", () => ({ loadPublishedPublication: loadPublicationMock }));
vi.mock("../../api/tutoring", () => ({ requestHelp: requestHelpMock }));
vi.mock("../../speech", () => ({ speak: vi.fn(), stopSpeech: vi.fn() }));
vi.mock("./usePublishedLearningSession", () => ({
  usePublishedLearningSession: () => sessionState,
}));
vi.mock("./PaperQuestionProgress", () => ({ PaperQuestionProgress: () => null }));
vi.mock("./PaperLearningProgress", () => ({ PaperLearningProgress: () => null }));
vi.mock("./StudentQuestionWorkspace", () => ({
  StudentQuestionWorkspace: ({ studentInput, onStudentInputChange, onSubmit }: { studentInput: string; onStudentInputChange: (value: string) => void; onSubmit: () => void }) => (
    <div>
      <button onClick={() => onStudentInputChange("甲的答案")}>填写答案</button>
      <output data-testid="student-input">{studentInput}</output>
      <button onClick={onSubmit}>提交答案</button>
    </div>
  ),
}));

import { resetLearnerIdCacheForTests, setCurrentLearnerId } from "../../api/identity";
import { PublishedPaperApp } from "./PublishedPaperApp";

function renderPaper() {
  return render(paperTree());
}

function paperTree() {
  function Location() {
    const location = useLocation();
    return <output data-testid="location">{location.pathname}</output>;
  }
  return (
    <MemoryRouter initialEntries={["/learn/papers/paper-1?continuous=1"]}>
      <Routes>
        <Route path="/learn/papers/:publicationId" element={<PublishedPaperApp />} />
        <Route path="/learn/continue" element={<p>连续学习入口</p>} />
      </Routes>
      <Location />
    </MemoryRouter>
  );
}

describe("user: 判题晚返回不能写入新学生或触发旧连续导航", () => {
  beforeEach(() => {
    resetLearnerIdCacheForTests();
    setCurrentLearnerId("learner-a");
    sessionState.queueAttempt = queueAttemptMock;
    sessionState.syncMessage = "学习记录已同步";
    sessionState.mastery = [];
    sessionState.attempts = [];
    sessionState.pendingAttemptIds = [];
    sessionState.sessionReady = true;
    loadPublicationMock.mockResolvedValue({
      publicationId: "paper-1",
      title: "测试练习",
      status: "published",
      version: 1,
      lessonIds: ["lesson-1"],
      lessonCount: 1,
      createdAt: 1,
      updatedAt: 1,
      lessons: [{
        lessonId: "lesson-1",
        title: "测试题",
        version: 1,
        status: "published",
        knowledgePointId: "kp-1",
        questionPayload: { question: {
          id: "question-1",
          questionType: "short-answer",
          chapter: "代数",
          knowledgePoint: "一次函数",
          prompt: "题目",
          givens: [],
          contentBlocks: [],
        } },
        guideCards: [],
      }],
    });
    queueAttemptMock.mockResolvedValue({ status: "saved" });
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
    resetLearnerIdCacheForTests();
  });

  it("Given 甲的判题请求未完成 When 切换到乙后判定返回 Then 不排队甲的作答也不导航", async () => {
    let finishJudgement!: (response: unknown) => void;
    requestHelpMock.mockImplementation(() => new Promise((resolve) => { finishJudgement = resolve; }));
    renderPaper();
    await screen.findByRole("button", { name: "填写答案" });
    await act(async () => { await Promise.resolve(); });
    fireEvent.click(screen.getByRole("button", { name: "填写答案" }));
    await waitFor(() => expect(screen.getByTestId("student-input")).toHaveTextContent("甲的答案"));
    fireEvent.click(screen.getByRole("button", { name: "提交答案" }));
    await waitFor(() => expect(requestHelpMock).toHaveBeenCalledTimes(1));

    act(() => setCurrentLearnerId("learner-b"));
    await act(async () => {
      finishJudgement({
        reply: "答对了",
        nextHintLevel: 0,
        guideContext: { assessment: "correct" },
      });
    });

    expect(queueAttemptMock).not.toHaveBeenCalled();
    expect(screen.queryByText("连续学习入口")).not.toBeInTheDocument();
    expect(screen.getByTestId("location")).toHaveTextContent("/learn/papers/paper-1");
  });

  it("Given 最后一题的答案仍在离线队列 When 页面重算进度 Then 只有队列确认后才显示完成态", async () => {
    sessionState.attempts = [{
      attemptId: "attempt-last",
      questionId: "question-1",
      response: { text: "已提交" },
      assessment: "correct",
      hintLevel: 0,
      durationMs: 100,
      createdAt: 1,
    }];
    sessionState.pendingAttemptIds = ["attempt-last"];
    const view = renderPaper();

    expect(await screen.findByRole("button", { name: "提交答案" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "这套练习已经完成" })).not.toBeInTheDocument();

    sessionState.pendingAttemptIds = [];
    view.rerender(paperTree());
    expect(await screen.findByRole("heading", { name: "这套练习已经完成" })).toBeInTheDocument();
  });
});
