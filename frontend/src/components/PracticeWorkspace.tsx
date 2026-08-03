import { useState } from "react";
import { QuestionAnswer } from "./QuestionAnswer";
import type { QuestionPayload, TextbookImportResult, TutorReply } from "../types";

type PracticeWorkspaceProps = {
  payload: QuestionPayload;
  textbookImport: TextbookImportResult;
  questionIndex: number;
  questionCount: number;
  questionLimit: number;
  loadingQuestion: boolean;
  loading: boolean;
  selectedOptions: string[];
  blankAnswers: Record<string, string>;
  numericAnswer: string;
  drawConnections: Array<[string, string]>;
  studentInput: string;
  interactionError: string;
  reply: TutorReply | null;
  onRegenerate: () => void;
  onPrevious: () => void;
  onNext: () => void;
  onSelectOption: (label: string, answerText: string) => void;
  onBlankChange: (id: string, value: string) => void;
  onNumericChange: (value: string) => void;
  onDrawConnectionsChange: (connections: Array<[string, string]>) => void;
  onStudentInputChange: (value: string) => void;
  onSubmit: () => void;
  onHelp: () => void;
};

export function PracticeWorkspace({
  payload,
  textbookImport,
  questionIndex,
  questionCount,
  questionLimit,
  loadingQuestion,
  loading,
  selectedOptions,
  blankAnswers,
  numericAnswer,
  drawConnections,
  studentInput,
  interactionError,
  reply,
  onRegenerate,
  onPrevious,
  onNext,
  onSelectOption,
  onBlankChange,
  onNumericChange,
  onDrawConnectionsChange,
  onStudentInputChange,
  onSubmit,
  onHelp,
}: PracticeWorkspaceProps) {
  const [debugOpen, setDebugOpen] = useState(false);
  const isDrawLine = payload.question.questionType === "draw-line";
  const hasStructuredAnswer = selectedOptions.length > 0
    || Object.values(blankAnswers).some((value) => value.trim())
    || Boolean(numericAnswer.trim())
    || drawConnections.length > 0;
  const hasQueuedBatch = textbookImport.batches?.some((batch) => batch.status === "queued");
  const isLastLoadedQuestion = questionIndex === questionCount - 1;

  return (
    <>
      <section className="workspace panel">
        <div className="question-block">
          <div className="question-toolbar">
            <span className="eyebrow">课后练习 · 题目 {questionIndex + 1}/{questionCount}</span>
            <div className="question-navigation">
              {textbookImport.uploadId && (
                <>
                  <span
                    className={`active-model ${payload.modelRun.fallback ? "fallback" : "live"}`}
                    title="重新生成会使用当前选择的模型；如需更换，请返回教材库重新选择模型"
                  >{payload.modelRun.provider} · {payload.modelRun.model}</span>
                  <button className="ghost compact" disabled={loadingQuestion} onClick={onRegenerate}>
                    {loadingQuestion ? "生成中…" : "重新生成本题"}
                  </button>
                </>
              )}
              <button className="ghost compact" disabled={questionIndex === 0 || loadingQuestion} onClick={onPrevious}>上一题</button>
              <button
                className="ghost compact"
                disabled={loadingQuestion || (isLastLoadedQuestion && (questionCount >= questionLimit || !hasQueuedBatch))}
                onClick={onNext}
              >
                {!isLastLoadedQuestion ? "下一题" : questionCount >= questionLimit ? `已达 ${questionLimit} 题上限` : loadingQuestion ? "正在识别下个 5 页…" : "生成下一题"}
              </button>
            </div>
          </div>
          <div className="question-source-meta">
            {payload.question.questionNumber && <b>原题 {payload.question.questionNumber}</b>}
            {payload.question.sourcePages && <span>来源第 {payload.question.sourcePages.start}-{payload.question.sourcePages.end} 页</span>}
            {payload.review && (
              <b className={payload.review.needsHumanReview ? "review-warning" : "review-passed"}>
                {payload.review.needsHumanReview ? "需要人工复核" : "双模型审校通过"}
              </b>
            )}
            {payload.quality && (
              <b className={payload.quality.status === "ready" ? "review-passed" : "review-warning"}>
                {payload.quality.status === "ready" ? "结构校验通过" : "结构校验未通过"}
              </b>
            )}
          </div>
          {(payload.question.sourceArtifactUrl || payload.question.promptArtifactUrl) && (
            <div className="artifact-links">
              {payload.question.sourceArtifactUrl && <a href={payload.question.sourceArtifactUrl} target="_blank" rel="noreferrer">查看 OCR Markdown</a>}
              {payload.question.promptArtifactUrl && <a href={payload.question.promptArtifactUrl} target="_blank" rel="noreferrer">查看模型提示词</a>}
            </div>
          )}
          <QuestionAnswer
            question={payload.question}
            selectedOptions={selectedOptions}
            blankAnswers={blankAnswers}
            numericAnswer={numericAnswer}
            drawConnections={drawConnections}
            onSelectOption={onSelectOption}
            onBlankChange={onBlankChange}
            onNumericChange={onNumericChange}
            onDrawConnectionsChange={onDrawConnectionsChange}
          />
          {payload.quality && (payload.quality.errors.length > 0 || payload.quality.warnings.length > 0) && (
            <details className="quality-details">
              <summary>结构质量门禁 · {payload.quality.errors.length} 个错误 · {payload.quality.warnings.length} 个警告</summary>
              {payload.quality.errors.map((error, index) => <p key={`quality-error-${index}`}>⛔ {error}</p>)}
              {payload.quality.warnings.map((warning, index) => <p key={`quality-warning-${index}`}>⚠ {warning}</p>)}
            </details>
          )}
          {payload.review && (
            <details className="review-details">
              <summary>审校记录 · 文字 {payload.review.text.confidence}% · 视觉 {payload.review.vision.confidence}% · {payload.review.text.corrections.length} 处修正</summary>
              {payload.review.text.corrections.map((item, index) => (
                <p key={`${item.field}-${index}`}><b>{item.field}</b>：{item.original} → {item.corrected}（{item.reason}）</p>
              ))}
              {payload.review.vision.correctAnswer && <p><b>视觉判定答案：</b>{payload.review.vision.correctAnswer}</p>}
              {[...payload.review.text.issues, ...payload.review.vision.issues].map((issue, index) => <p key={`${issue}-${index}`}>⚠ {issue}</p>)}
              {payload.question.visualContext?.map((context, index) => <p key={`visual-${index}`}><b>题图理解：</b>{context.description}</p>)}
            </details>
          )}
          <div className="givens">{payload.question.givens.map((given) => <span key={given}>{given}</span>)}</div>
        </div>

        <div className="answer-block">
          <label htmlFor="answer">{isDrawLine ? "完成作图后提交" : "写下你的想法"}</label>
          <textarea
            id="answer"
            value={studentInput}
            onChange={(event) => onStudentInputChange(event.target.value)}
            onKeyDown={(event) => {
              if ((event.metaKey || event.ctrlKey) && event.key === "Enter") onSubmit();
            }}
            placeholder="写下你已经完成的步骤，或者说明自己卡在哪里……"
          />
          <div className="answer-actions">
            <button className="ghost" onClick={() => onStudentInputChange("我不知道怎么开始")}>我卡住了</button>
            <button className="ghost submit-answer" disabled={loading || (!studentInput.trim() && !hasStructuredAnswer)} onClick={onSubmit}>
              {loading ? "正在分析…" : isDrawLine ? "提交作图" : "提交回答"}
            </button>
            <button className="help-button" disabled={loading} onClick={onHelp}>{loading ? "正在分析…" : "Help · 下一步提示"}</button>
          </div>
          {interactionError && <p className="interaction-error">{interactionError}</p>}
        </div>
      </section>

      {reply && (
        <section className="tutor-panel panel">
          <div className="avatar">D</div>
          <div className="tutor-content">
            <div className="tutor-meta">
              <strong>Dotty</strong>
              {reply.guideContext.assessment && (
                <b className={`assessment ${reply.guideContext.assessment}`}>
                  {reply.guideContext.assessment === "correct" ? "回答正确" : reply.guideContext.assessment === "incorrect" ? "需要修正" : "部分正确"}
                </b>
              )}
              <span>
                {reply.source === "model-generated"
                  ? `实时生成 · ${reply.modelRun.provider}/${reply.modelRun.model}`
                  : reply.modelRun.fallback
                    ? `模型失败，已回退 · ${reply.modelRun.error ?? "Mock"}`
                    : reply.source === "stored-guide-card" ? "来自预制引导卡" : "已检查学生答案"}
              </span>
            </div>
            {reply.reply.split("\n").map((line, index) => <p key={index}>{line || <br />}</p>)}
            <button className="debug-toggle" onClick={() => setDebugOpen((value) => !value)}>{debugOpen ? "收起数据流" : "查看本次 guide_context"}</button>
            {debugOpen && <pre>{JSON.stringify(reply.guideContext, null, 2)}</pre>}
          </div>
        </section>
      )}
    </>
  );
}
