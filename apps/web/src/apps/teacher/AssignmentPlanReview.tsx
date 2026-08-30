import type { AssignmentPlan } from "../../types/classroom";

interface Props {
  plan: AssignmentPlan;
  confirming: boolean;
  onConfirm: (confirmWarnings: boolean) => void;
  onRegenerate: () => void;
  onPersonalize?: () => void;
  personalizing?: boolean;
}

export function AssignmentPlanReview({ plan, confirming, onConfirm, onRegenerate, onPersonalize, personalizing }: Props) {
  return (
    <section className="assignment-plan-review" aria-label="作业计划审阅">
      <div className="teacher-section-heading">
        <div><span className="eyebrow">班级分析与作业计划</span><h3>请审阅后确认</h3></div>
        <span>{plan.result.fallback ? "确定性规则回退" : "模型辅助表达"}</span>
      </div>
      {plan.result.fallback && <p className="teacher-field-hint" role="note">模型不可用或结果未通过校验，已使用确定性规则；统计事实仍来自班级证据。</p>}
      {plan.warnings.map((warning) => <p className="teacher-plan-warning" role="note" key={warning.code}>{warning.message}</p>)}
      <ol className="assignment-goal-list">
        {plan.result.goals.map((goal) => <li key={goal.planningTopicKey}><strong>{goal.topic}</strong><span>{goal.objective}</span><small>{goal.reason}</small></li>)}
      </ol>
      <div className="assignment-plan-coverage">
        {plan.result.coverage.map((item) => <span key={item.planningTopicKey}>{item.topic} · {item.questionCount} 题</span>)}
      </div>
      <div className="assignment-plan-actions">
        <button className="secondary-button" onClick={onRegenerate} disabled={confirming}>重新分析</button>
        {onPersonalize && !plan.result.personalized && <button className="secondary-button" onClick={onPersonalize} disabled={confirming || personalizing}>{personalizing ? "生成中…" : "生成个性化作业"}</button>}
        <button onClick={() => onConfirm(plan.warnings.length > 0)} disabled={confirming}>{confirming ? "布置中…" : "确认并布置作业"}</button>
      </div>
    </section>
  );
}
