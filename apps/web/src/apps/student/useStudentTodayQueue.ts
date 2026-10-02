import { useEffect, useState } from "react";
import { loadPublishedPublications } from "../../api/publications";
import { loadStudentAssignments } from "../../api/classroom";
import { useLearnerId } from "../../api/identity";
import { loadMistakes } from "../../api/mistakes";
import { loadLearningProgress } from "../../api/reviews";
import type { PublicationSummary } from "../../types/index";
import type { StudentAssignment } from "../../types/classroom";
import { learningSessionStorageKey } from "./learningSessionStorage";

export interface TodayQueuePaper extends PublicationSummary {
  /** 本机曾保存学习会话；只用于“开始/继续”提示，不代表服务端完成状态。 */
  started: boolean;
}

export interface StudentTodayQueue {
  pendingConfirmCount: number;
  dueReviewCount: number;
  unmasteredCount: number;
  papers: TodayQueuePaper[];
  assignments: StudentAssignment[];
  loading: boolean;
  /** 四路请求里失败的部分才会在这里留言；能拿到的数据仍然照常展示。 */
  error: string;
  /**
   * 四路全部失败。此时“没有待办”是读不到数据的假象，不是真的做完了——
   * 页面必须据此换一套文案，否则会让学生以为今天的任务已经清空。
   */
  allFailed: boolean;
}

function hasStartedSession(learnerId: string, publicationId: string): boolean {
  try {
    return localStorage.getItem(learningSessionStorageKey({ learnerId, publicationId })) !== null;
  } catch {
    // 隐私模式或站点数据被禁用时 localStorage 访问会直接抛异常；把它当作
    // “未开始”处理即可，不能让今日队列因此白屏。
    return false;
  }
}

const EMPTY_QUEUE: StudentTodayQueue = {
  pendingConfirmCount: 0,
  dueReviewCount: 0,
  unmasteredCount: 0,
  papers: [],
  assignments: [],
  loading: true,
  error: "",
  allFailed: false,
};

/** 四路只读数据形成一个身份绑定的快照，失败项留空，不沿用上一位学生的数据。 */
export function useStudentTodayQueue(): StudentTodayQueue {
  const learnerId = useLearnerId();
  const [snapshot, setSnapshot] = useState<{ learnerId: string; queue: StudentTodayQueue } | null>(null);

  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();
    void Promise.allSettled([
      loadStudentAssignments(learnerId),
      loadPublishedPublications(controller.signal),
      loadMistakes(),
      loadLearningProgress(),
    ]).then(([assignments, publications, mistakes, progress]) => {
      if (cancelled) return;
      const results = [assignments, publications, mistakes, progress];
      const labels = ["作业", "练习目录", "错题本", "复习进度"];
      const errors = results.flatMap((result, index) => result.status === "rejected"
        ? [`${labels[index]}：${result.reason instanceof Error ? result.reason.message : "加载失败"}`]
        : []);
      const items = mistakes.status === "fulfilled" ? mistakes.value : [];
      setSnapshot({
        learnerId,
        queue: {
          pendingConfirmCount: items.filter((item) => item.status === "pending_confirmation").length,
          unmasteredCount: items.filter((item) => item.status === "unmastered").length,
          dueReviewCount: progress.status === "fulfilled" ? progress.value.dueReviewCount : 0,
          papers: publications.status === "fulfilled"
            ? publications.value.map((item) => ({ ...item, started: hasStartedSession(learnerId, item.publicationId) })) : [],
          assignments: assignments.status === "fulfilled" ? assignments.value : [],
          loading: false,
          error: errors.join("；"),
          allFailed: errors.length === results.length,
        },
      });
    });
    return () => { cancelled = true; controller.abort(); };
  }, [learnerId]);

  // 身份切换的首次渲染就隐藏旧快照，不等 effect 执行后才清空。
  return snapshot?.learnerId === learnerId ? snapshot.queue : EMPTY_QUEUE;
}
