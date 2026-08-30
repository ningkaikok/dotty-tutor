interface StudentPaperCompletedProps {
  questionCount: number;
  onReview: () => void;
  onBack: () => void;
}

/** 全部题目完成后的稳定落点；不再让学生停在最后一道题的“已正确”反馈里。 */
export function StudentPaperCompleted({ questionCount, onReview, onBack }: StudentPaperCompletedProps) {
  return (
    <section className="student-paper-completed panel" aria-label="互动试卷已完成">
      <h1>这套试卷已经完成</h1>
      <p>你已完成 {questionCount} 道题，答案和学习记录已经保存。可以返回学生空间查看错题，或回看已完成的题目。</p>
      <div className="student-completed-actions">
        <button onClick={onBack}>返回学生空间</button>
        <button className="student-submit-button" onClick={onReview}>回看已完成题目</button>
      </div>
    </section>
  );
}
