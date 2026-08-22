import { DrawLineCanvas } from "../DrawLineCanvas";
import { QuestionContent } from "../QuestionContent";
import type { Question } from "../types/index";

interface QuestionAnswerProps {
  question: Question;
  selectedOptions: string[];
  blankAnswers: Record<string, string>;
  numericAnswer: string;
  drawConnections: Array<[string, string]>;
  onSelectOption: (label: string, answerText: string) => void;
  onBlankChange: (id: string, value: string) => void;
  onNumericChange: (value: string) => void;
  onDrawConnectionsChange: (connections: Array<[string, string]>) => void;
  /** 复习/陪练展示原题时使用，避免学生误以为还能提交原题。 */
  readOnly?: boolean;
}

/**
 * 渲染一份题目契约对应的结构化作答控件。
 *
 * 组件保持完全受控：它不请求后端、不判断正误，也不把“我选 A”之类的自然语言反解析成答案。
 * 教材预览、学生试卷和错题陪练因此可以复用同一套交互，并由各自页面决定何时提交和如何持久化。
 */
export function QuestionAnswer({
  question,
  selectedOptions,
  blankAnswers,
  numericAnswer,
  drawConnections,
  onSelectOption,
  onBlankChange,
  onNumericChange,
  onDrawConnectionsChange,
  readOnly = false,
}: QuestionAnswerProps) {
  const questionType = question.questionType ?? "short-answer";
  const multiple = questionType === "multi-select" || question.selectionMode === "multiple";
  const contentBlocks = question.contentBlocks;
  const promptNode = <QuestionContent blocks={contentBlocks} showOptions={false} />;

  // 画线题的连接关系是结构化坐标，而不是画布截图；这样后端才能稳定判题并回放答案。
  if (questionType === "draw-line" && question.interaction) {
    return (
      <>
        {promptNode}
        <DrawLineCanvas
          interaction={question.interaction}
          connections={drawConnections}
          onChange={onDrawConnectionsChange}
          readOnly={readOnly}
        />
      </>
    );
  }

  if (questionType === "true-false") {
    return (
      <>
        {promptNode}
        <div className="question-options true-false-options">
          {["正确", "错误"].map((label) => (
            <button
              key={label}
              type="button"
              className={`question-option ${selectedOptions.includes(label) ? "selected" : ""}`}
              onClick={readOnly ? undefined : () => onSelectOption(label, label)}
              disabled={readOnly}
              aria-pressed={selectedOptions.includes(label)}
            >
              <span className="option-label">{label === "正确" ? "✓" : "✕"}</span>
              <span>{label}</span>
            </button>
          ))}
        </div>
      </>
    );
  }

  if (questionType === "fill-blank") {
    return (
      <>
        {promptNode}
        <div className="fill-blank-answers" aria-label="填空答案">
          {(question.blanks ?? []).map((blank, index) => (
            <label key={blank.id} className="fill-blank-field">
              <span>{blank.label || `第 ${index + 1} 空`}</span>
              <input
                type="text"
                value={blankAnswers[blank.id] ?? ""}
                onChange={(event) => onBlankChange(blank.id, event.target.value)}
                readOnly={readOnly}
                aria-label={blank.label || `第 ${index + 1} 空`}
              />
              {blank.unit && <small>{blank.unit}</small>}
            </label>
          ))}
        </div>
      </>
    );
  }

  if (questionType === "numeric") {
    return (
      <>
        {promptNode}
        <label className="numeric-answer-field">
          <span>答案</span>
          <input
            type="text"
            inputMode="decimal"
            value={numericAnswer}
            onChange={(event) => onNumericChange(event.target.value)}
            readOnly={readOnly}
            aria-label="数值答案"
            placeholder="输入数值或公式"
          />
          {question.answerSpec?.unit && <small>{question.answerSpec.unit}</small>}
        </label>
      </>
    );
  }

  if (questionType === "choice" || questionType === "multi-select" || question.options?.length) {
    // Structured content is normalized before it reaches this component, so it only owns input.
    return (
      <>
        <QuestionContent
          blocks={contentBlocks}
          selectedOption={selectedOptions[0] ?? null}
          selectedOptions={selectedOptions}
          multiple={multiple}
          onSelectOption={onSelectOption}
          readOnly={readOnly}
        />
        {selectedOptions.length > 0 && (
          <p className="selected-option">已选择 {selectedOptions.join("、")}，确认后点击“提交回答”。</p>
        )}
      </>
    );
  }

  return <>{promptNode}</>;
}
