import { QuestionAnswer } from "../../../components/QuestionAnswer";
import MathText from "../../../MathText";
import type { TutorStage } from "../../../types";
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

/** 变式练习承载 practice/verify 两个后端阶段，学生只看到一个连续练习流。 */
export function VariationPractice({ mistakeId, autoStart = false, onStageChange }: VariationPracticeProps) {
  const state = useVariationPractice(mistakeId, autoStart, onStageChange);

  if (state.loading) return <div className="variation-practice loading">正在恢复掌握验证记录…</div>;
  if (!state.active) {
    return (
      <section className="variation-practice">
        <span className="eyebrow">MASTERY CHECK</span>
        <h3>先做一道变式练习</h3>
        <p>系统会根据错误原因调整练习策略，不会直接重复原题。首题答对后进入掌握验证。</p>
        {state.error && <p className="mistake-error" role="alert">{state.error}</p>}
        <button className="mistake-primary-action compact" disabled={state.submitting} onClick={() => void state.generate()}>
          {state.submitting ? "正在生成…" : "开始变式练习"}
        </button>
      </section>
    );
  }

  const question = state.active.questionPayload.question;
  const answered = state.active.status === "answered";
  return (
    <section className="variation-practice">
      <header>
        <div>
          <span className="eyebrow">MASTERY CHECK · {state.active.sequence}</span>
          <h3>{LEVEL_LABELS[state.active.level]}</h3>
        </div>
        <span className="variation-count">已完成 {state.items.filter((item) => item.status === "answered").length} 题</span>
      </header>
      <div className={answered ? "variation-question answered" : "variation-question"}>
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
        />
      </div>
      {answered && (
        <div className={`variation-feedback ${state.active.assessment}`} role="status">
          <strong>{state.active.assessment === "correct" ? "回答正确" : "这次还没有答对"}</strong>
          <p><MathText text={state.active.feedback} /></p>
        </div>
      )}
      {answered && state.active.mastery && (
        <div className="mastery-progress" role="status">
          <strong>{state.active.mastery.mastered ? "已通过掌握验证" : "继续验证掌握"}</strong>
          <span>连续答对 {state.active.mastery.correctStreak} / {state.active.mastery.requiredCorrect}</span>
          {state.active.mastery.mastered && <p>这道题已从错题本进入进阶本，后续会按计划安排复习。</p>}
        </div>
      )}
      {state.error && <p className="mistake-error" role="alert">{state.error}</p>}
      <div className="variation-actions">
        {answered && !state.active.mastery?.mastered ? (
          <button className="mistake-primary-action compact" disabled={state.submitting} onClick={() => void state.generate()}>
            {state.submitting ? "正在生成…" : "生成下一道"}
          </button>
        ) : !answered ? (
          <button className="mistake-primary-action compact" disabled={state.submitting} onClick={() => void state.submit()}>
            {state.submitting ? "正在判定…" : "提交验证答案"}
          </button>
        ) : null}
      </div>
    </section>
  );
}
