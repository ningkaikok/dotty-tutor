import type { ExerciseAttemptRecord, PublishedLesson } from "../../types/index";

interface PaperQuestionProgressProps {
  lessons: PublishedLesson[];
  currentIndex: number;
  latestAttempts: ReadonlyMap<string, ExerciseAttemptRecord>;
  onJump: (index: number) => void;
}

/**
 * 题号进度条：把“当前第几题、哪些题已答对/待修正”统一到一处，
 * 顶栏和面板标题不再各自重复展示题号。
 *
 * 题目 id 与完成态的判定口径必须和 usePublishedPaperProgress 保持一致
 * （取每题最近一次 attempt，assessment 为 correct 才算完成），否则两处
 * 会对“这道题算不算做完”给出不同答案。
 */
export function PaperQuestionProgress({ lessons, currentIndex, latestAttempts, onJump }: PaperQuestionProgressProps) {
  const questionIdOf = (lesson: PublishedLesson) => lesson.questionPayload?.question.id ?? lesson.lessonId;
  const correctCount = lessons.filter((lesson) => latestAttempts.get(questionIdOf(lesson))?.assessment === "correct").length;

  return (
    <nav className="paper-question-progress panel" aria-label="题目进度">
      <div className="paper-question-progress-summary">
        第 {currentIndex + 1}/{lessons.length} 题 · 已完成 {correctCount} 道
      </div>
      <ol className="paper-question-progress-list">
        {lessons.map((lesson, index) => {
          const questionId = questionIdOf(lesson);
          const assessment = latestAttempts.get(questionId)?.assessment;
          const isCorrect = assessment === "correct";
          const needsFix = assessment === "incorrect" || assessment === "partial";
          const isCurrent = index === currentIndex;
          const statusLabel = isCorrect ? "已答对" : needsFix ? "待修正" : "未作答";
          const className = [
            "paper-question-progress-item",
            isCorrect && "correct",
            needsFix && "needs-fix",
            isCurrent && "current",
            !isCorrect && !needsFix && "todo",
          ].filter(Boolean).join(" ");
          return (
            <li key={lesson.lessonId}>
              <button
                type="button"
                className={className}
                aria-current={isCurrent ? "step" : undefined}
                aria-label={`第 ${index + 1} 题 · ${statusLabel}`}
                onClick={() => onJump(index)}
              >
                {index + 1}
              </button>
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
