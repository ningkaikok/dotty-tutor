// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { MistakeItem } from "../../types/index";

const { loadMistakeMock, tutorCallbacks } = vi.hoisted(() => ({
  loadMistakeMock: vi.fn(),
  tutorCallbacks: [] as Array<() => void>,
}));

vi.mock("../../api/mistakes", () => ({
  archiveMistake: vi.fn(),
  loadMistake: loadMistakeMock,
  loadMistakes: vi.fn().mockResolvedValue([]),
}));
vi.mock("../student/StudentNav", () => ({ StudentNav: () => null }));
vi.mock("./components/MistakeTutor", () => ({
  MistakeTutor: ({ item, onMastered }: { item: { learnerId: string }; onMastered: () => void }) => {
    tutorCallbacks.push(onMastered);
    return <button onClick={onMastered}>{item.learnerId}</button>;
  },
}));

import { resetLearnerIdCacheForTests, setCurrentLearnerId } from "../../api/identity";
import { MistakeCoachApp } from "./MistakeCoachApp";

function detail(learnerId: string): MistakeItem {
  return {
    mistakeId: "shared-mistake",
    learnerId,
    sourceFilename: "练习题",
    contentType: "image/jpeg",
    sourceImageUrl: "",
    questionPayload: { question: {
      id: "question-1",
      chapter: "代数",
      knowledgePoint: "一次函数",
      prompt: "原题",
      givens: [],
      contentBlocks: [],
    }, lessonSteps: [], architecture: {}, modelRun: {
      requestedProvider: "mock", provider: "mock", model: "test", fallback: false,
    } },
    guideCards: [],
    originalAnswer: "答案",
    subject: "math",
    gradeBand: "初中",
    chapter: "代数",
    knowledgePoint: "一次函数",
    notes: "",
    status: "unmastered",
    createdAt: 1,
    updatedAt: 1,
  };
}

function renderTutorRoute() {
  function Location() {
    const location = useLocation();
    return <output data-testid="location">{location.pathname}</output>;
  }
  return render(
    <MemoryRouter initialEntries={["/mistakes/shared-mistake/tutor?continuous=1"]}>
      <Routes>
        <Route path="/mistakes/:mistakeId/tutor" element={<MistakeCoachApp />} />
        <Route path="/learn/continue" element={<p>连续学习入口</p>} />
      </Routes>
      <Location />
    </MemoryRouter>,
  );
}

describe("user: 错题辅导在学生身份切换后隔离详情和自动导航", () => {
  beforeEach(() => {
    resetLearnerIdCacheForTests();
    setCurrentLearnerId("learner-a");
    tutorCallbacks.length = 0;
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
    resetLearnerIdCacheForTests();
  });

  it("Given 甲的错题请求未完成 When 切换到乙且甲晚返回 Then 页面只显示乙的错题", async () => {
    const pending: Array<(item: MistakeItem) => void> = [];
    loadMistakeMock.mockImplementation(() => new Promise((resolve) => pending.push(resolve)));
    renderTutorRoute();
    await waitFor(() => expect(pending).toHaveLength(1));

    act(() => setCurrentLearnerId("learner-b"));
    await waitFor(() => expect(pending).toHaveLength(2));
    await act(async () => { pending[0](detail("learner-a")); });
    expect(screen.queryByRole("button", { name: "learner-a" })).not.toBeInTheDocument();

    await act(async () => { pending[1](detail("learner-b")); });
    expect(await screen.findByRole("button", { name: "learner-b" })).toBeInTheDocument();
  });

  it("Given 甲的旧掌握回调仍被保留 When 乙已接管页面 Then 旧回调不会导航到连续学习", async () => {
    const pending: Array<(item: MistakeItem) => void> = [];
    loadMistakeMock.mockImplementation(() => new Promise((resolve) => pending.push(resolve)));
    renderTutorRoute();
    await waitFor(() => expect(pending).toHaveLength(1));
    await act(async () => { pending[0](detail("learner-a")); });
    expect(await screen.findByRole("button", { name: "learner-a" })).toBeInTheDocument();
    const oldCallback = tutorCallbacks[tutorCallbacks.length - 1];

    act(() => setCurrentLearnerId("learner-b"));
    await waitFor(() => expect(pending).toHaveLength(2));
    await act(async () => { pending[1](detail("learner-b")); });
    expect(await screen.findByRole("button", { name: "learner-b" })).toBeInTheDocument();

    act(() => oldCallback());
    expect(screen.getByTestId("location")).toHaveTextContent("/mistakes/shared-mistake/tutor");
    expect(screen.queryByText("连续学习入口")).not.toBeInTheDocument();
  });
});
