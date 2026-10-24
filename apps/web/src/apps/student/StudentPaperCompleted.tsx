interface StudentPaperCompletedProps {
  questionCount: number;
  onReview: () => void;
  onBack: () => void;
  syncMessage?: string;
}

/** 全部题目完成后的稳定落点；离线作答排队时明确等网络恢复后再衔接下一项。 */
export function StudentPaperCompleted({ questionCount, onReview, onBack, syncMessage = "" }: StudentPaperCompletedProps) {
  const waitingForSync = /排队|暂存/.test(syncMessage);
  return (
    <section className="student-paper-completed panel" aria-label="练习已完成">
      <h1>这套练习已经完成</h1>
      <p>{waitingForSync
        ? `你已完成 ${questionCount} 道题，答案暂存在本机，网络恢复后会自动补传。`
        : `你已完成 ${questionCount} 道题，答案和学习记录已经保存。可以返回学生空间查看错题，或回看已提交的题目。`}</p>
      <div className="student-completed-actions">
        <button onClick={onBack}>返回学生空间</button>
        <button className="student-submit-button" onClick={onReview}>回看已完成题目</button>
      </div>
    </section>
  );
}
