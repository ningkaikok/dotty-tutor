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
}: QuestionAnswerProps) {
  const questionType = question.questionType ?? "short-answer";
  const multiple = questionType === "multi-select" || question.selectionMode === "multiple";
  const imageChoices = hasImageOptions(question);
  const promptNode = <MathText text={displayedPrompt(question)} className="question-prompt" block />;

  // 画线题的连接关系是结构化坐标，而不是画布截图；这样后端才能稳定判题并回放答案。
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
    // 新生成题优先走内容块协议，以保留题干、公式、图片和选项的原始顺序。
    // 下面的旧字段分支仅用于兼容早期已经持久化的课程。
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
            const inferredImageIndex = question.imageUrls?.length === 5 ? index + 1 : index;
            const optionImage = question.optionImageUrls?.[index]
              ?? (imageChoices ? question.imageUrls?.[inferredImageIndex] : null)
              ?? (imageOption ? question.imageUrls?.[inferredImageIndex] ?? imageOption[1] : null);
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
