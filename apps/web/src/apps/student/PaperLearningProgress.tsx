import type { MasteryState } from "../../types/index";

interface PaperLearningProgressProps {
  knowledgePointId: string;
  knowledgePoint: string;
  mastery: MasteryState[];
  syncMessage: string;
}

/**
 * Presents the deterministic learning evidence returned by LearningStore.
 * This is deliberately separate from mistake-book mastery: paper practice
 * shows a lightweight score, while mistake mastery still requires two verified
 * variation answers and drives the 1/3/7-day review schedule.
 */
export function PaperLearningProgress({
  knowledgePointId,
  knowledgePoint,
  mastery,
  syncMessage,
}: PaperLearningProgressProps) {
  const current = mastery.find((item) => item.knowledgePointId === knowledgePointId);
  const score = Math.round((current?.score ?? 0) * 100);
  const totalAttempts = mastery.reduce((sum, item) => sum + item.attemptCount, 0);

  return (
    <section className="paper-learning-progress panel" aria-label="互动试卷学习进度">
      <div>
        <span className="eyebrow">LEARNING EVIDENCE</span>
        <strong>{knowledgePoint}</strong>
        <small>{syncMessage}</small>
      </div>
      <div className="paper-mastery-score">
        <span>掌握度</span>
        <strong>{score}%</strong>
        <div className="paper-mastery-track" aria-label={`掌握度 ${score}%`}>
          <i style={{ width: `${score}%` }} />
        </div>
      </div>
      <dl>
        <div><dt>不同题证据</dt><dd>{current?.evidenceCount ?? 0} 道</dd></div>
        <div><dt>答对证据</dt><dd>{current?.correctCount ?? 0} 道</dd></div>
        <div><dt>置信度</dt><dd>{Math.round((current?.evidenceConfidence ?? 0) * 100)}%</dd></div>
        <div><dt>不同题总数</dt><dd>{totalAttempts} 道</dd></div>
      </dl>
    </section>
  );
}
