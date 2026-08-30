import { QuestionAnswer } from "../../../components/QuestionAnswer";
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

/** 变式练习承载 practice/verify 两个后端阶段，学生只需完成一道验证题。 */
export function VariationPractice({ mistakeId, autoStart = false, onStageChange }: VariationPracticeProps) {
  const state = useVariationPractice(mistakeId, autoStart, onStageChange);

  if (state.loading) return <div className="variation-practice loading">正在恢复掌握验证记录…</div>;
  if (!state.active) {
    return (
      <section className="variation-practice">
        <span className="eyebrow">掌握验证</span>
        <h3>先做一道掌握验证题</h3>
        <p>系统会根据错误原因生成一道新题。答错可以修改后重新提交，答对一次即可完成掌握验证。</p>
        {state.error && <p className="mistake-error" role="alert">{state.error}</p>}
        <button className="mistake-primary-action compact" disabled={state.submitting} onClick={() => void state.generate()}>
          {state.submitting ? "正在生成…" : "开始变式练习"}
        </button>
      </section>
    );
  }

  const question = state.active.questionPayload.question;
  const answered = state.active.status === "answered";
  const retryable = answered && state.active.assessment !== "correct";
  const locked = answered && !retryable;
  return (
    <section className="variation-practice">
      <header>
        <div>
          <span className="eyebrow">掌握验证 · 单题</span>
          <h3>{LEVEL_LABELS[state.active.level]}</h3>
        </div>
        <span className="variation-count">答对 1 次即可完成</span>
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
              <strong>{state.active.mastery.mastered ? "已完成掌握验证" : "修改答案后重新提交即可完成"}</strong>
              {state.active.mastery.mastered && <p>这道题已从错题本进入进阶本，后续会按计划安排复习。</p>}
            </div>
          )}
        </div>
      )}
      {state.error && <p className="mistake-error" role="alert">{state.error}</p>}
      <div className="variation-actions">
        {!locked ? (
          <button className="mistake-primary-action compact" disabled={state.submitting} onClick={() => void state.submit()}>
            {state.submitting ? "正在判定…" : retryable ? "重新提交" : "提交验证答案"}
          </button>
        ) : null}
      </div>
    </section>
  );
}
