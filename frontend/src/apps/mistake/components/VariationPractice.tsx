import { QuestionAnswer } from "../../../components/QuestionAnswer";
import { useVariationPractice } from "../useVariationPractice";

const LEVEL_LABELS = {
  foundation: "基础验证",
  parallel: "同类迁移",
  transfer: "综合迁移",
};

interface VariationPracticeProps {
  mistakeId: string;
}

/** Phase-four verification appears only after the conversational tutor ends. */
export function VariationPractice({ mistakeId }: VariationPracticeProps) {
  const state = useVariationPractice(mistakeId);

  if (state.loading) return <div className="variation-practice loading">正在恢复掌握验证记录…</div>;
  if (!state.active) {
    return (
      <section className="variation-practice">
        <span className="eyebrow">MASTERY CHECK</span>
        <h3>用一道新题验证是否真正理解</h3>
        <p>系统会根据错误原因调整练习策略，不会直接重复原题。</p>
        {state.error && <p className="mistake-error" role="alert">{state.error}</p>}
        <button className="mistake-primary-action compact" disabled={state.submitting} onClick={() => void state.generate()}>
          {state.submitting ? "正在生成…" : "生成第一道验证题"}
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
          <p>{state.active.feedback}</p>
        </div>
      )}
      {state.error && <p className="mistake-error" role="alert">{state.error}</p>}
      <div className="variation-actions">
        {answered ? (
          <button className="mistake-primary-action compact" disabled={state.submitting} onClick={() => void state.generate()}>
            {state.submitting ? "正在生成…" : "生成下一道"}
          </button>
        ) : (
          <button className="mistake-primary-action compact" disabled={state.submitting} onClick={() => void state.submit()}>
            {state.submitting ? "正在判定…" : "提交验证答案"}
          </button>
        )}
      </div>
    </section>
  );
}
