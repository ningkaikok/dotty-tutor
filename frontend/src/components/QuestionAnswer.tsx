import { DrawLineCanvas } from "../DrawLineCanvas";
import MathText from "../MathText";
import { QuestionContent } from "../QuestionContent";
import { displayedPrompt, hasImageOptions, optionLabel, optionText } from "../questionPresentation";
import type { Question } from "../types";

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
}

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
}: QuestionAnswerProps) {
  const questionType = question.questionType ?? "short-answer";
  const multiple = questionType === "multi-select" || question.selectionMode === "multiple";
  const imageChoices = hasImageOptions(question);
  const promptNode = <MathText text={displayedPrompt(question)} className="question-prompt" block />;

  if (questionType === "draw-line" && question.interaction) {
    return (
      <>
        {promptNode}
        <DrawLineCanvas
          interaction={question.interaction}
          connections={drawConnections}
          onChange={onDrawConnectionsChange}
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
              onClick={() => onSelectOption(label, label)}
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
            aria-label="数值答案"
            placeholder="输入数值或公式"
          />
          {question.answerSpec?.unit && <small>{question.answerSpec.unit}</small>}
        </label>
      </>
    );
  }

  if (question.contentBlocks?.length) {
    return (
      <QuestionContent
        blocks={question.contentBlocks}
        selectedOption={selectedOptions[0] ?? null}
        selectedOptions={selectedOptions}
        multiple={multiple}
        onSelectOption={onSelectOption}
      />
    );
  }

  return (
    <>
      <MathText text={displayedPrompt(question)} className="question-prompt" block />
      {question.imageUrls?.length && !imageChoices && (
        <div className="question-images">
          {question.imageUrls.map((url, index) => (
            <a key={url} href={url} target="_blank" rel="noreferrer" title="打开原始题图">
              <img src={url} alt={`题目对应图片 ${index + 1}`} loading="lazy" />
            </a>
          ))}
        </div>
      )}
      {question.options?.length ? (
        <ol className={`question-options ${imageChoices ? "image-options" : ""}`}>
          {question.options.map((option, index) => {
            const label = optionLabel(index);
            const imageOption = /!\[[^\]]*\]\(([^)]+)\)/.exec(option);
            const optionImage = question.optionImageUrls?.[index]
              ?? (imageChoices ? question.imageUrls?.[index] : null)
              ?? (imageOption ? question.imageUrls?.[index] ?? imageOption[1] : null);
            const selected = selectedOptions.includes(label);
            return (
              <li key={`${option}-${index}`}>
                <button
                  type="button"
                  className={`question-option ${selected ? "selected" : ""}`}
                  onClick={() => onSelectOption(label, optionText(option))}
                  aria-pressed={selected}
                >
                  <span className="option-label">{label}</span>
                  {optionImage ? <img src={optionImage} alt={`选项${String.fromCharCode(65 + index)}`} loading="lazy" /> : <MathText text={optionText(option)} />}
                </button>
              </li>
            );
          })}
        </ol>
      ) : null}
      {selectedOptions.length > 0 && (
        <p className="selected-option">已选择 {selectedOptions.join("、")}，确认后点击“提交回答”。</p>
      )}
    </>
  );
}
