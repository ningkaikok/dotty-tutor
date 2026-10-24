// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { listVariationsMock, createVariationMock } = vi.hoisted(() => ({
  listVariationsMock: vi.fn(),
  createVariationMock: vi.fn(),
}));

vi.mock("../../../api/practice", () => ({
  answerVariation: vi.fn(),
  createVariation: createVariationMock,
  listVariations: listVariationsMock,
}));
vi.mock("../../../components/QuestionAnswer", () => ({ QuestionAnswer: () => <div /> }));
vi.mock("../../../components/EvaluationEvidence", () => ({ EvaluationEvidence: () => null }));
vi.mock("../../../RichText", () => ({ RichText: ({ text }: { text: string }) => <span>{text}</span> }));

import { VariationPractice } from "./VariationPractice";

function answeredExercise(mastered: boolean, sequence = 1) {
  return {
    variationId: `variation-${sequence}`,
    mistakeId: "mistake-1",
    learnerId: "learner-1",
    strategy: "parallel-calculation" as const,
    level: "parallel" as const,
    sequence,
    questionPayload: { question: { id: `question-${sequence}`, questionType: "choice", prompt: "选择" } },
    status: "answered" as const,
    assessment: "correct" as const,
    response: {},
    feedback: `第 ${sequence} 题反馈已保留。`,
    createdAt: 1,
    attemptId: "attempt-1",
    mastery: { correctStreak: mastered ? 2 : 1, requiredCorrect: 2, mastered, answeredCount: 1, nextAction: mastered ? "advance" as const : "stay" as const },
    nextAction: mastered ? "advance" as const : "stay" as const,
  };
}

function readyExercise(sequence: number) {
  return {
    ...answeredExercise(false, sequence),
    variationId: `variation-${sequence}`,
    status: "ready" as const,
    assessment: undefined,
    attemptId: undefined,
    feedback: "",
  };
}

describe("user: 连续巩固练习可以在反馈期间暂停并继续", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    listVariationsMock.mockReset();
    createVariationMock.mockReset();
    createVariationMock.mockResolvedValue({ ...answeredExercise(false), variationId: "variation-2", status: "ready", assessment: undefined, attemptId: undefined });
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  it("Given 还需要一道正确证据 When 学生暂停后恢复 Then 只在恢复后的计时结束时生成下一题", async () => {
    listVariationsMock.mockResolvedValue([answeredExercise(false)]);
    render(<VariationPractice mistakeId="mistake-1" autoContinue />);

    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    expect(screen.getByRole("button", { name: "留在这道题" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "留在这道题" }));
    expect(screen.getByText("自动继续已暂停，这道题的反馈会保留。")).toBeInTheDocument();
    await act(async () => { await vi.advanceTimersByTimeAsync(1500); });
    expect(createVariationMock).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "继续巩固练习" }));
    await act(async () => { await vi.advanceTimersByTimeAsync(1200); });
    expect(createVariationMock).toHaveBeenCalledTimes(1);
  });

  it("Given 本题已达到掌握门槛 When 学生暂停后恢复 Then 只在恢复后的计时结束时进入下一项", async () => {
    const onMastered = vi.fn();
    listVariationsMock.mockResolvedValue([answeredExercise(true)]);
    render(<VariationPractice mistakeId="mistake-1" autoContinue onMastered={onMastered} />);

    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    expect(screen.getByRole("button", { name: "留在这道题" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "留在这道题" }));
    await act(async () => { await vi.advanceTimersByTimeAsync(1500); });
    expect(onMastered).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "继续下一项" }));
    await act(async () => { await vi.advanceTimersByTimeAsync(1200); });
    expect(onMastered).toHaveBeenCalledTimes(1);
    expect(createVariationMock).not.toHaveBeenCalled();
  });

  it("Given 第四题答对但证据未达掌握门槛 When 自动补练停止 Then 学生可在原页明确再练一道且本次只生成一题", async () => {
    listVariationsMock.mockResolvedValue([answeredExercise(false, 4)]);
    createVariationMock.mockResolvedValue(readyExercise(5));
    render(<VariationPractice mistakeId="mistake-1" autoContinue />);

    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    expect(screen.getByText("第 4 题反馈已保留。")).toBeInTheDocument();
    expect(screen.getByText(/自动连续补练已到本轮上限/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "再练一道" })).toBeInTheDocument();

    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(createVariationMock).not.toHaveBeenCalled();

    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "再练一道" })); });
    expect(createVariationMock).toHaveBeenCalledTimes(1);
    expect(screen.getByText("第 5 道巩固练习")).toBeInTheDocument();
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(createVariationMock).toHaveBeenCalledTimes(1);
  });

  it("Given 达到自动上限后再练一道生成失败 When 学生同页重试 Then 原作答反馈保留并只重试这一题", async () => {
    listVariationsMock.mockResolvedValue([answeredExercise(false, 4)]);
    createVariationMock
      .mockRejectedValueOnce(new Error("题目暂时没准备好"))
      .mockResolvedValueOnce(readyExercise(5));
    render(<VariationPractice mistakeId="mistake-1" autoContinue />);

    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "再练一道" })); });

    expect(screen.getByText("第 4 题反馈已保留。")).toBeInTheDocument();
    expect(screen.getByText("题目暂时没准备好")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重试准备这一道" })).toBeInTheDocument();

    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "重试准备这一道" })); });
    expect(createVariationMock).toHaveBeenCalledTimes(2);
    expect(screen.getByText("第 5 道巩固练习")).toBeInTheDocument();
  });

  it("Given 学生已点击再练一道且请求仍在处理 When 再次点击 Then 不会重复创建题目", async () => {
    let resolveGeneration!: (exercise: ReturnType<typeof readyExercise>) => void;
    listVariationsMock.mockResolvedValue([answeredExercise(false, 4)]);
    createVariationMock.mockReturnValue(new Promise((resolve) => { resolveGeneration = resolve; }));
    render(<VariationPractice mistakeId="mistake-1" autoContinue />);

    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    const continueButton = screen.getByRole("button", { name: "再练一道" });
    fireEvent.click(continueButton);
    expect(continueButton).toBeDisabled();
    fireEvent.click(continueButton);
    expect(createVariationMock).toHaveBeenCalledTimes(1);

    await act(async () => { resolveGeneration(readyExercise(5)); await Promise.resolve(); });
    expect(screen.getByText("第 5 道巩固练习")).toBeInTheDocument();
  });
});
