import { useReviewProgress } from "../useReviewProgress";
import { ReviewTaskCard } from "./ReviewTaskCard";

export function MistakeProgress() {
  const state = useReviewProgress();
  if (state.loading) return <div className="mistake-empty">正在计算学习进度…</div>;
  if (!state.progress) return <p className="mistake-error">{state.error || "无法读取学习进度"}</p>;
  const displayRate = (value: number | null) => value == null ? "暂无" : `${Math.round(value * 100)}%`;

  return (
    <section className="progress-page">
      <div className="mistake-section-heading">
        <h1>掌握与复习</h1>
        <p>掌握不是一次答对，而是在不同时间仍能独立解决同类问题。</p>
      </div>
      <div className="progress-summary">
        <div><strong>{Math.round(state.progress.masteryRate * 100)}%</strong><span>错题掌握率</span></div>
        <div><strong>{state.progress.dueReviewCount}</strong><span>当前待复习</span></div>
        <div><strong>{state.progress.completedReviewCount}</strong><span>已完成复习</span></div>
        <div><strong>{Math.round(state.progress.reviewAccuracy * 100)}%</strong><span>复习正确率</span></div>
        <div><strong>{displayRate(state.progress.verificationAccuracy)}</strong><span>变式验证正确率</span></div>
        <div><strong>{displayRate(state.progress.reviewCompletionRate)}</strong><span>复习完成率</span></div>
      </div>
      {state.error && <p className="mistake-error" role="alert">{state.error}</p>}
      <div className="progress-grid">
        <section>
          <h2>1 · 3 · 7 天复习任务</h2>
          {state.tasks.length ? state.tasks.map((task) => (
            <ReviewTaskCard
              key={task.taskId}
              task={task}
              serverTime={state.serverTime}
              busy={state.busyTaskId === task.taskId}
              onStart={state.start}
              onAnswer={state.answer}
            />
          )) : <div className="mistake-empty compact">完成一道掌握验证题后，这里会自动生成复习任务。</div>}
        </section>
        <aside className="knowledge-progress-card">
          <h2>知识点掌握</h2>
          {state.progress.knowledgePoints.length ? state.progress.knowledgePoints.map((point) => {
            const rate = Math.round(point.mastered / point.total * 100);
            return (
              <div key={point.knowledgePoint}>
                <span><strong>{point.knowledgePoint}</strong><small>{point.mastered}/{point.total}</small></span>
                <progress max={100} value={rate}>{rate}%</progress>
              </div>
            );
          }) : <p>确认错题后将显示知识点进度。</p>}
        </aside>
      </div>
      <section className="progress-evidence-note" aria-label="学习证据说明">
        <h2>这些数字如何得出</h2>
        <p>验证正确率按每次变式作答证据计算，答错后改对不会抹掉之前的记录；复习完成率按已完成的 1、3、7 天任务计算。</p>
        <p>同知识点再错率只统计同一发布版本内有后续作答的知识点路径；掌握状态来自服务端确定性判题，不来自学生端自报。</p>
      </section>
    </section>
  );
}
