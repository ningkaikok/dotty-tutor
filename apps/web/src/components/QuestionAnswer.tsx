import { useState } from "react";
import { DrawLineCanvas } from "../DrawLineCanvas";
import { QuestionContent } from "../QuestionContent";
import type { Question, SubQuestion, SubQuestionAnswer } from "../types/index";

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
  subQuestionAnswers?: Record<string, SubQuestionAnswer>;
  onSubQuestionChange?: (id: string, answer: SubQuestionAnswer) => void;
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
  subQuestionAnswers,
  onSubQuestionChange,
  readOnly = false,
}: QuestionAnswerProps) {
  const [localSubQuestionAnswers, setLocalSubQuestionAnswers] = useState<Record<string, SubQuestionAnswer>>({});
  const renderedSubQuestionAnswers = subQuestionAnswers ?? localSubQuestionAnswers;
  const questionType = question.questionType ?? "short-answer";
  const multiple = questionType === "multi-select" || question.selectionMode === "multiple";
  const contentBlocks = question.contentBlocks;
  const promptNode = <QuestionContent blocks={contentBlocks} showOptions={false} />;

  if (question.subQuestions?.length) {
    return (
      <>
        {promptNode}
        <div className="sub-question-list" aria-label="分小问作答">
        {question.subQuestions.map((subQuestion) => (
          <SubQuestionFields
            key={subQuestion.id}
            subQuestion={subQuestion}
            answer={renderedSubQuestionAnswers[subQuestion.id] ?? {}}
            onChange={(answer) => {
              if (onSubQuestionChange) onSubQuestionChange(subQuestion.id, answer);
              else setLocalSubQuestionAnswers((current) => ({ ...current, [subQuestion.id]: answer }));
            }}
            readOnly={readOnly}
          />
        ))}
        </div>
      </>
    );
  }

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

function SubQuestionFields({
  subQuestion,
  answer,
  onChange,
  readOnly,
}: {
  subQuestion: SubQuestion;
  answer: SubQuestionAnswer;
  onChange: (answer: SubQuestionAnswer) => void;
  readOnly: boolean;
}) {
  const blocks = subQuestion.contentBlocks?.length
    ? subQuestion.contentBlocks
    : [{ id: `${subQuestion.id}-prompt`, type: "text" as const, text: subQuestion.prompt, sourceOrder: 0 }];
  const multiple = subQuestion.questionType === "multi-select";
  const options = subQuestion.questionType === "true-false" ? ["正确", "错误"] : (subQuestion.options ?? []);
  const selected = answer.selectedOptions ?? [];
  const select = (label: string) => {
    const next = multiple
      ? (selected.includes(label) ? selected.filter((item) => item !== label) : [...selected, label])
      : [label];
    onChange({ ...answer, selectedOptions: next });
  };

  if (subQuestion.questionType === "draw-line" && subQuestion.interaction) {
    return (
      <fieldset className="sub-question-fieldset">
        <legend><span className="sub-question-label">{subQuestion.label}</span></legend>
        <QuestionContent blocks={blocks} showOptions={false} />
        <DrawLineCanvas
          interaction={subQuestion.interaction}
          connections={answer.connections ?? []}
          onChange={(connections) => onChange({ ...answer, connections })}
          readOnly={readOnly}
        />
        {subQuestion.evaluation.mode === "tutor" && <small className="sub-question-tutor-note">此小问由陪练反馈，不参与自动判分</small>}
      </fieldset>
    );
  }

  return (
    <fieldset className="sub-question-fieldset">
      <legend><span className="sub-question-label">{subQuestion.label}</span></legend>
      <QuestionContent blocks={blocks} showOptions={false} />
      {options.length > 0 && (subQuestion.questionType === "choice" || subQuestion.questionType === "multi-select" || subQuestion.questionType === "true-false") && (
        <div className="question-options sub-question-options">
          {options.map((option) => (
            <button
              key={option}
              type="button"
              className={`question-option ${selected.includes(option) ? "selected" : ""}`}
              onClick={() => select(option)}
              disabled={readOnly}
              aria-pressed={selected.includes(option)}
            >{option}</button>
          ))}
        </div>
      )}
      {subQuestion.questionType === "numeric" && (
        <input
          type="text"
          inputMode="decimal"
          value={answer.numericAnswer ?? ""}
          onChange={(event) => onChange({ ...answer, numericAnswer: event.target.value })}
          readOnly={readOnly}
          aria-label={`${subQuestion.label}数值答案`}
        />
      )}
      {subQuestion.questionType === "fill-blank" && (
        <div className="fill-blank-answers">
          {(subQuestion.blanks ?? []).map((blank) => (
            <label key={blank.id} className="fill-blank-field">
              <span>{blank.label}</span>
              <input
                type="text"
                value={answer.blankAnswers?.[blank.id] ?? ""}
                onChange={(event) => onChange({
                  ...answer,
                  blankAnswers: { ...(answer.blankAnswers ?? {}), [blank.id]: event.target.value },
                })}
                readOnly={readOnly}
                aria-label={`${subQuestion.label}${blank.label}`}
              />
            </label>
          ))}
        </div>
      )}
      {!["choice", "multi-select", "true-false", "numeric", "fill-blank"].includes(subQuestion.questionType) && (
        <textarea
          value={answer.text ?? ""}
          onChange={(event) => onChange({ ...answer, text: event.target.value })}
          readOnly={readOnly}
          aria-label={`${subQuestion.label}作答`}
          placeholder={subQuestion.evaluation.mode === "tutor" ? "写出你的理由或推导过程" : "写出答案或过程"}
        />
      )}
      {subQuestion.evaluation.mode === "tutor" && <small className="sub-question-tutor-note">此小问由陪练反馈，不参与自动判分</small>}
    </fieldset>
  );
}
