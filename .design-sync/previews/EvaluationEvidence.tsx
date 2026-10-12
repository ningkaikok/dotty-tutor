import { EvaluationEvidence } from "dotty-tutor";

// <details> starts collapsed; the expanded cells mirror a student having
// clicked "为什么这样判" so the grading facts are visible on the card.
const expand = (node: HTMLDivElement | null) => {
  node?.querySelector("details")?.setAttribute("open", "");
};

const frame = { width: 440, fontFamily: "var(--font-bright)", color: "var(--c-ink)" };
const version = "answer-evaluator-v1";

export const Collapsed = () => (
  <div style={frame}>
    <EvaluationEvidence evidence={{ strategy: "choice-set-match", submittedLabels: ["A", "C"], expectedCount: 3, evaluatorVersion: version }} />
  </div>
);

export const ChoiceSet = () => (
  <div ref={expand} style={frame}>
    <EvaluationEvidence evidence={{ strategy: "choice-set-match", submittedLabels: ["A", "C"], expectedCount: 3, evaluatorVersion: version }} />
  </div>
);

export const FillBlanks = () => (
  <div ref={expand} style={frame}>
    <EvaluationEvidence
      question={{ blanks: [{ id: "b1", label: "横坐标" }, { id: "b2", label: "纵坐标" }] }}
      evidence={{ strategy: "fill-blank-parts", totalBlanks: 3, matchedCount: 1, failedBlankIds: ["b1", "b2"], evaluatorVersion: version }}
    />
  </div>
);

export const SubQuestionParts = () => (
  <div ref={expand} style={frame}>
    <EvaluationEvidence
      question={{ subQuestions: [{ id: "sq-1" }, { id: "sq-2" }, { id: "sq-3" }] }}
      evidence={{
        strategy: "sub-question-parts",
        parts: [
          { subQuestionId: "sq-1", status: "correct" },
          { subQuestionId: "sq-2", status: "incorrect" },
          { subQuestionId: "sq-3", status: "ungraded" },
        ],
        gradableCount: 2,
        matchedCount: 1,
        ungradedCount: 1,
        complete: true,
        masteryEligible: false,
        evaluatorVersion: version,
      }}
    />
  </div>
);
