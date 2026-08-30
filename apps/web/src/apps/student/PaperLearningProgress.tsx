import type { MasteryState } from "../../types/index";

interface PaperLearningProgressProps {
  knowledgePointId: string;
  mastery: MasteryState[];
}

/**
 * Presents the deterministic learning evidence returned by LearningStore.
 * This is deliberately separate from mistake-book mastery: paper practice
 * shows a lightweight score, while mistake mastery still requires two verified
 * variation answers and drives the 1/3/7-day review schedule.
 */
export function PaperLearningProgress({
  knowledgePointId,
  mastery,
}: PaperLearningProgressProps) {
  const current = mastery.find((item) => item.knowledgePointId === knowledgePointId);
  const score = Math.round((current?.score ?? 0) * 100);
  const correctCount = current?.correctCount ?? 0;
  // 答对数不可能超过证据数。字段缺失时 evidenceCount 会回退成 0，直接展示会得到
  // “已答对 1 道，共 0 道”这种自相矛盾的句子，因此取两者较大值兜底。
  const evidenceCount = Math.max(current?.evidenceCount ?? 0, correctCount);

  return (
    <section className="paper-learning-progress panel" aria-label="互动试卷学习进度">
      <div>
        <span className="eyebrow">本题掌握度</span>
        <small>已答对 {correctCount} 道，共 {evidenceCount} 道</small>
      </div>
      <div className="paper-mastery-score">
        <span>掌握度</span>
        <strong>{score}%</strong>
        <div className="paper-mastery-track" aria-label={`掌握度 ${score}%`}>
          <i style={{ width: `${score}%` }} />
        </div>
      </div>
    </section>
  );
}
