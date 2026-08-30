import type { EvaluationEvidence as EvaluationEvidenceData, Question } from "../types/index";

interface EvaluationEvidenceProps {
  evidence?: Record<string, unknown>;
  question?: Question;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function stringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

function numberValue(value: unknown, fallback = 0): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function booleanValue(value: unknown): boolean {
  return value === true;
}

function textValue(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function asEvidence(value: Record<string, unknown>): EvaluationEvidenceData | null {
  const strategy = value.strategy;
  const evaluatorVersion = textValue(value.evaluatorVersion);
  if (typeof strategy !== "string") return null;

  switch (strategy) {
    case "choice-set-match":
      return {
        strategy,
        submittedLabels: stringArray(value.submittedLabels),
        expectedCount: numberValue(value.expectedCount),
        evaluatorVersion,
      };
    case "fill-blank-parts":
      return {
        strategy,
        totalBlanks: numberValue(value.totalBlanks),
        matchedCount: numberValue(value.matchedCount),
        failedBlankIds: stringArray(value.failedBlankIds),
        evaluatorVersion,
      };
    case "numeric-tolerance":
      return {
        strategy,
        submittedRaw: textValue(value.submittedRaw),
        tolerance: numberValue(value.tolerance),
        expectedCount: numberValue(value.expectedCount),
        evaluatorVersion,
      };
    case "short-answer-text-match":
      return {
        strategy,
        submittedRaw: textValue(value.submittedRaw),
        expectedCount: numberValue(value.expectedCount),
        evaluatorVersion,
      };
    case "line-connections":
      return {
        strategy,
        submittedCount: numberValue(value.submittedCount),
        requiredCount: numberValue(value.requiredCount),
        evaluatorVersion,
      };
    case "sub-question-parts": {
      const parts = Array.isArray(value.parts)
        ? value.parts.filter(isRecord).flatMap((part) => {
          const subQuestionId = textValue(part.subQuestionId);
          const status = part.status;
          if (!subQuestionId || !["correct", "incorrect", "tutor", "incomplete", "ungraded"].includes(String(status))) {
            return [];
          }
          return [{
            subQuestionId,
            status: status as "correct" | "incorrect" | "tutor" | "incomplete" | "ungraded",
            ...(typeof part.feedbackRequired === "boolean" ? { feedbackRequired: part.feedbackRequired } : {}),
          }];
        })
        : [];
      return {
        strategy,
        parts,
        gradableCount: numberValue(value.gradableCount),
        matchedCount: numberValue(value.matchedCount),
        ungradedCount: numberValue(value.ungradedCount),
        complete: booleanValue(value.complete),
        masteryEligible: booleanValue(value.masteryEligible),
        evaluatorVersion,
      };
    }
    default:
      return null;
  }
}

function renderEvidence(evidence: EvaluationEvidenceData, question?: Question) {
  const blankLabel = (id: string) => {
    return question?.blanks?.find((blank) => blank.id === id)?.label || id;
  };
  const subQuestionLabel = (id: string) => {
    const index = question?.subQuestions?.findIndex((subQuestion) => subQuestion.id === id) ?? -1;
    return index >= 0 ? `第 ${index + 1} 小问` : id;
  };

  switch (evidence.strategy) {
    case "choice-set-match":
      return <p>你选了 {evidence.submittedLabels.length ? evidence.submittedLabels.join("、") : "没有选项"}，共 {evidence.submittedLabels.length} 项{evidence.expectedCount > 1 ? `；这道题要求选择 ${evidence.expectedCount} 项` : ""}。</p>;
    case "fill-blank-parts":
      return (
        <p>
          {evidence.totalBlanks} 个空里对了 {evidence.matchedCount} 个
          {evidence.failedBlankIds.length
            ? `，${evidence.failedBlankIds.map(blankLabel).join("、")}还需要修改。`
            : "，全部匹配。"}
        </p>
      );
    case "numeric-tolerance":
      return <p>你填的是 {evidence.submittedRaw ? `“${evidence.submittedRaw}”` : "空白"}{evidence.tolerance > 0 ? `；本题允许误差 ±${evidence.tolerance}` : ""}。</p>;
    case "short-answer-text-match":
      return <p>你填写的是“{evidence.submittedRaw}”。</p>;
    case "line-connections":
      return <p>你完成了 {evidence.submittedCount} 条连接；题目要求 {evidence.requiredCount} 条连接。</p>;
    case "sub-question-parts":
      return (
        <div>
          <p>共 {evidence.gradableCount} 个可判分小问，答对 {evidence.matchedCount} 个；{evidence.ungradedCount ? `${evidence.ungradedCount} 个小问暂未计入判分。` : "可判分小问均已参与判定。"}</p>
          <ul className="evaluation-evidence-parts">
            {evidence.parts.map((part) => (
              <li key={part.subQuestionId}>
                <span>{subQuestionLabel(part.subQuestionId)}</span>
                <strong>
                  {part.status === "correct" ? "正确" : part.status === "incorrect" ? "需要修正" : part.status === "tutor" ? "待陪练反馈" : part.status === "incomplete" ? "未完成" : "暂未判定"}
                </strong>
              </li>
            ))}
          </ul>
          <p>{evidence.complete ? "本次小问作答已完成。" : "本次仍有小问没有完成。"}</p>
        </div>
      );
  }
}

/** Render deterministic grading facts without revealing answer content. */
export function EvaluationEvidence({ evidence, question }: EvaluationEvidenceProps) {
  if (!evidence || Object.keys(evidence).length === 0 || !isRecord(evidence)) return null;
  const parsed = asEvidence(evidence);
  if (!parsed) return null;

  return (
    <details className="evaluation-evidence">
      <summary>为什么这样判</summary>
      <div className="evaluation-evidence-body">
        {renderEvidence(parsed, question)}
        {parsed.evaluatorVersion && <small>判定规则版本：{parsed.evaluatorVersion}</small>}
      </div>
    </details>
  );
}
