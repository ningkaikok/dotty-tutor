import { QuestionAnswer } from "../../../components/QuestionAnswer";
import { EvaluationEvidence } from "../../../components/EvaluationEvidence";
import { RichText } from "../../../RichText";
import type { TutorStage } from "../../../types/index";
import { useVariationPractice } from "../useVariationPractice";

const LEVEL_LABELS = {
  foundation: "基础验证",
  parallel: "同类迁移",
  transfer: "综合迁移",
};

interface VariationPracticeProps {
  mistakeId: string;
  autoStart?: boolean;
  onStageChange?: (stage: TutorStage) => void;
}

/** 巩固练习承载 practice/verify 两个后端阶段，学生只需完成一道验证题。 */
export function VariationPractice({ mistakeId, autoStart = false, onStageChange }: VariationPracticeProps) {
  const state = useVariationPractice(mistakeId, autoStart, onStageChange);

  if (state.loading) return <div className="variation-practice loading">正在恢复掌握验证记录…</div>;
  if (!state.active) {
    return (
      <section className="variation-practice">
        <span className="eyebrow">掌握验证</span>
        <h3>先做一道掌握验证题</h3>
        <p>系统会根据错误原因生成巩固练习，并按掌握策略累计独立证据；达到领域门槛后才会进入下一阶段。</p>
        {state.error && <p className="mistake-error" role="alert">{state.error}</p>}
        <button className="mistake-primary-action compact" disabled={state.submitting} onClick={() => void state.generate()}>
          {state.submitting ? "正在生成…" : "开始巩固练习"}
        </button>
      </section>
    );
  }

  const question = state.active.questionPayload.question;
  const answered = state.active.status === "answered";
  const retryable = answered && state.active.assessment !== "correct";
  const nextAction = state.active.nextAction ?? state.active.mastery?.nextAction ?? "stay";
  const mastered = state.active.mastery?.mastered || nextAction === "advance" || nextAction === "test_out";
  const locked = answered && !retryable;
  return (
    <section className="variation-practice">
      <header>
        <div>
          <span className="eyebrow">掌握验证 · 单题</span>
          <h3>{LEVEL_LABELS[state.active.level]}</h3>
        </div>
        <span className="variation-count">第 {state.active.sequence} 道巩固练习</span>
      </header>
      <div className={locked ? "variation-question answered" : retryable ? "variation-question retryable" : "variation-question"}>
        <QuestionAnswer
          question={question}
          selectedOptions={state.selectedOptions}
          blankAnswers={state.blankAnswers}
          numericAnswer={state.numericAnswer}
          drawConnections={[]}
          onSelectOption={(label) => state.selectOption(label)}
          onBlankChange={(id, value) => state.setBlankAnswers((current) => ({ ...current, [id]: value }))}
          onNumericChange={state.setNumericAnswer}
          onDrawConnectionsChange={() => undefined}
          readOnly={locked}
        />
      </div>
      {answered && (
        // 合并原先两个 role="status" 区块：判定结果和掌握结果说的是同一件事，
        // 分开渲染会让读屏把近义的话朗读两遍。层次是判定结果 → 反馈正文 →
        // 掌握结果的增量信息，掌握结果那段只保留判定结果没有覆盖的新信息。
        <div className={`variation-feedback ${state.active.assessment}`} role="status">
          <strong>{state.active.assessment === "correct" ? "回答正确" : "这次还没有答对"}</strong>
          <p><RichText text={state.active.feedback} /></p>
          {state.active.mastery && (
            <div className="variation-mastery-note">
              <strong>{mastered ? "已通过掌握门槛" : nextAction === "needs_review" ? "还需要补充可判定证据" : "还需要继续巩固练习"}</strong>
              {mastered
                ? <p>这道题已从错题本进入已掌握，后续会按计划安排复习。</p>
                : <p>当前结果不会直接改变掌握状态，请完成下一道巩固练习。</p>}
            </div>
          )}
        </div>
      )}
      {answered && <EvaluationEvidence evidence={state.active.evaluationEvidence as Record<string, unknown> | undefined} question={question} />}
      {state.error && <p className="mistake-error" role="alert">{state.error}</p>}
      <div className="variation-actions">
        {!locked ? (
          <button className="mistake-primary-action compact" disabled={state.submitting} onClick={() => void state.submit()}>
            {state.submitting ? "正在判定…" : retryable ? "重新提交" : "提交验证答案"}
          </button>
        ) : !mastered ? (
          <button className="mistake-primary-action compact" disabled={state.submitting} onClick={() => void state.generate()}>
            {state.submitting ? "正在生成…" : "开始下一道巩固练习"}
          </button>
        ) : null}
      </div>
    </section>
  );
}
