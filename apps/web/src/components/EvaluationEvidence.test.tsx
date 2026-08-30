// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { Question } from "../types/index";
import { EvaluationEvidence } from "./EvaluationEvidence";

const version = "answer-evaluator-v1";
const question: Question = {
  id: "q-1",
  chapter: "",
  knowledgePoint: "",
  prompt: "",
  givens: [],
  contentBlocks: [],
  blanks: [
    { id: "b1", label: "横坐标", answerType: "text" },
    { id: "b3", label: "纵坐标", answerType: "text" },
  ],
  subQuestions: [
    { id: "uuid-a", label: "(1)", prompt: "", questionType: "short-answer", evaluation: { mode: "deterministic" } },
    { id: "uuid-b", label: "(2)", prompt: "", questionType: "short-answer", evaluation: { mode: "deterministic" } },
  ],
};

describe("EvaluationEvidence", () => {
  afterEach(cleanup);

  it("renders choice-set-match facts", () => {
    render(<EvaluationEvidence evidence={{ strategy: "choice-set-match", submittedLabels: ["A", "C"], expectedCount: 3, evaluatorVersion: version }} />);
    expect(screen.getByText("你选了 A、C，共 2 项；这道题要求选择 3 项。")).toBeInTheDocument();
  });

  it("renders fill-blank-parts facts", () => {
    render(<EvaluationEvidence evidence={{ strategy: "fill-blank-parts", totalBlanks: 5, matchedCount: 3, failedBlankIds: ["2", "4"], evaluatorVersion: version }} />);
    expect(screen.getByText("5 个空里对了 3 个，2、4还需要修改。")).toBeInTheDocument();
  });

  it("uses question labels for blanks and real positions for sub-questions", () => {
    const { rerender } = render(<EvaluationEvidence
      question={question}
      evidence={{ strategy: "fill-blank-parts", totalBlanks: 3, matchedCount: 1, failedBlankIds: ["b1", "b3"] }}
    />);
    expect(screen.getByText("3 个空里对了 1 个，横坐标、纵坐标还需要修改。")).toBeInTheDocument();

    rerender(<EvaluationEvidence
      question={question}
      evidence={{
        strategy: "sub-question-parts",
        parts: [{ subQuestionId: "uuid-b", status: "incorrect" }],
        gradableCount: 1,
        matchedCount: 0,
        ungradedCount: 0,
        complete: true,
        masteryEligible: true,
      }}
    />);
    expect(screen.getByText("第 2 小问")).toBeInTheDocument();
  });

  it("falls back to raw ids when the question is not provided", () => {
    render(<EvaluationEvidence evidence={{
      strategy: "sub-question-parts",
      parts: [{ subQuestionId: "uuid-b", status: "incorrect" }],
      gradableCount: 1,
      matchedCount: 0,
      ungradedCount: 0,
      complete: true,
      masteryEligible: true,
    }} />);
    expect(screen.getByText("uuid-b")).toBeInTheDocument();
  });

  it("renders numeric-tolerance facts", () => {
    render(<EvaluationEvidence evidence={{ strategy: "numeric-tolerance", submittedRaw: "3.14", tolerance: 0.01, expectedCount: 1, evaluatorVersion: version }} />);
    expect(screen.getByText("你填的是 “3.14”；本题允许误差 ±0.01。")).toBeInTheDocument();
  });

  it("omits zero numeric tolerance", () => {
    render(<EvaluationEvidence evidence={{ strategy: "numeric-tolerance", submittedRaw: "3", tolerance: 0, expectedCount: 1 }} />);
    expect(screen.getByText("你填的是 “3”。")).toBeInTheDocument();
    expect(screen.queryByText(/允许误差/)).not.toBeInTheDocument();
  });

  it("renders short-answer-text-match facts", () => {
    render(<EvaluationEvidence evidence={{ strategy: "short-answer-text-match", submittedRaw: "因为温度升高", expectedCount: 1, evaluatorVersion: version }} />);
    expect(screen.getByText("你填写的是“因为温度升高”。")).toBeInTheDocument();
  });

  it("omits the expected count for a single-choice question", () => {
    render(<EvaluationEvidence evidence={{ strategy: "choice-set-match", submittedLabels: ["A"], expectedCount: 1 }} />);
    expect(screen.getByText("你选了 A，共 1 项。")).toBeInTheDocument();
    expect(screen.queryByText(/要求选择/)).not.toBeInTheDocument();
  });

  it("renders line-connections facts", () => {
    render(<EvaluationEvidence evidence={{ strategy: "line-connections", submittedCount: 2, requiredCount: 3, evaluatorVersion: version }} />);
    expect(screen.getByText("你完成了 2 条连接；题目要求 3 条连接。")).toBeInTheDocument();
  });

  it("renders sub-question-parts facts", () => {
    render(<EvaluationEvidence evidence={{
      strategy: "sub-question-parts",
      parts: [
        { subQuestionId: "sq-1", status: "correct" },
        { subQuestionId: "sq-2", status: "incorrect" },
        { subQuestionId: "sq-3", status: "tutor", feedbackRequired: true },
      ],
      gradableCount: 2,
      matchedCount: 1,
      ungradedCount: 1,
      complete: true,
      masteryEligible: false,
      evaluatorVersion: version,
    }} />);
    expect(screen.getByText("共 2 个可判分小问，答对 1 个；1 个小问暂未计入判分。")).toBeInTheDocument();
    expect(screen.getByText("待陪练反馈")).toBeInTheDocument();
  });

  it("renders nothing for missing or empty evidence", () => {
    const { container, rerender } = render(<EvaluationEvidence />);
    expect(container).toBeEmptyDOMElement();

    rerender(<EvaluationEvidence evidence={{}} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("ignores an unknown strategy without throwing", () => {
    expect(() => render(<EvaluationEvidence evidence={{ strategy: "future-strategy", evaluatorVersion: version, secret: "not shown" }} />)).not.toThrow();
    expect(screen.queryByText("not shown")).not.toBeInTheDocument();
  });
});
