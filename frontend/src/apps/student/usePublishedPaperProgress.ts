import { useMemo } from "react";
import type { ExerciseAttemptRecord, PublicationDetail } from "../../types";

export interface PublishedPaperProgress {
  /** 每道题最近一次提交；历史记录仍完整保留在 attempts 中。 */
  latestAttempts: ReadonlyMap<string, ExerciseAttemptRecord>;
  /** 第一题尚未以 correct 结论完成时的索引。全部完成时为 -1。 */
  firstIncompleteIndex: number;
  completed: boolean;
  isQuestionCompleted: (questionId: string) => boolean;
  /** 从当前题之后寻找下一道未完成题，必要时从试卷开头环回。 */
  nextIncompleteIndex: (currentIndex: number, justCompletedQuestionId?: string) => number | null;
}

/**
 * 将“题目顺序”和“作答日志”组合成学生端唯一的进度状态机。
 *
 * 服务器保存的是不可变 attempt 日志，页面不能把“当前索引”当作完成依据：
 * 学生刷新、离线补传或从目录重新进入时都可能先拿到不同时间点的快照。因此这里
 * 只看每道题最新一次 assessment，correct 才代表完成，partial/incorrect 都允许重提。
 */
export function usePublishedPaperProgress(
  publication: PublicationDetail | null,
  attempts: ExerciseAttemptRecord[],
): PublishedPaperProgress {
  return useMemo(() => {
    const latest = new Map<string, ExerciseAttemptRecord>();
    for (const attempt of attempts) {
      const previous = latest.get(attempt.questionId);
      if (!previous || attempt.createdAt > previous.createdAt
        // createdAt 来自客户端时钟，同一毫秒内重提可能相同。attempts 本身是
        // 服务端/本地队列的追加日志，因此时间相同时以后出现的记录为准；不能
        // 用随机 UUID 的字典序猜测先后关系。
        || attempt.createdAt === previous.createdAt) {
        latest.set(attempt.questionId, attempt);
      }
    }

    const lessons = publication?.lessons ?? [];
    const isComplete = (questionId: string) => latest.get(questionId)?.assessment === "correct";
    const firstIncompleteIndex = lessons.findIndex((lesson) => !isComplete(lesson.questionPayload?.question.id ?? lesson.lessonId));

    return {
      latestAttempts: latest,
      firstIncompleteIndex,
      completed: lessons.length > 0 && firstIncompleteIndex === -1,
      isQuestionCompleted: isComplete,
      nextIncompleteIndex: (currentIndex: number, justCompletedQuestionId?: string) => {
        if (!lessons.length) return null;
        // 正常路径先向后推进；如果学生从目录手动跳题，环回可回到最早未完成题，
        // 避免“后面的题做完了但前面漏题”时错误地显示完成。
        const indexes = [
          ...Array.from({ length: lessons.length - currentIndex - 1 }, (_, offset) => currentIndex + offset + 1),
          ...Array.from({ length: Math.min(currentIndex + 1, lessons.length) }, (_, index) => index),
        ];
        return indexes.find((index) => {
          const questionId = lessons[index].questionPayload?.question.id ?? lessons[index].lessonId;
          return questionId !== justCompletedQuestionId && !isComplete(questionId);
        }) ?? null;
      },
    };
  }, [attempts, publication]);
}
