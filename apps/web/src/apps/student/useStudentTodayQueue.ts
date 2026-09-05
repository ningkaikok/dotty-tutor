import { useEffect, useState } from "react";
import { loadPublishedPublications } from "../../api/publications";
import { loadStudentAssignments } from "../../api/classroom";
import { useLearnerId } from "../../api/identity";
import { loadMistakes } from "../../api/mistakes";
import { loadLearningProgress } from "../../api/reviews";
import type { PublicationSummary } from "../../types/index";
import type { StudentAssignment } from "../../types/classroom";

export interface TodayQueuePaper extends PublicationSummary {
  /**
   * 本机是否曾为这套卷子开过学习会话。判据是
   * `usePublishedLearningSession.ts` 写入的 `dotty-learning-session:{publicationId}`
   * localStorage 键——这是本机信号，换设备或清缓存都会不准，因为服务端目前
   * 没有“列出某学生所有会话”的接口，无法据此做出权威判断。
   */
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

function hasStartedSession(publicationId: string): boolean {
  try {
    return localStorage.getItem(`dotty-learning-session:${publicationId}`) !== null;
  } catch {
    // 隐私模式或站点数据被禁用时 localStorage 访问会直接抛异常；把它当作
    // “未开始”处理即可，不能让今日队列因此白屏。
    return false;
  }
}

/**
 * 派生学生首页“今天要做什么”的数据快照。
 *
 * 作业指派是学生任务的权威来源；已发布试卷仍作为自由练习单独展示。任何一路失败都不应该拖垮
 * 其他内容，因此用 allSettled 语义（Promise.all 分别 catch）而不是让一次失败直接让整页失败。
 */
export function useStudentTodayQueue(): StudentTodayQueue {
  // 身份进入依赖数组：切换学生后四路数据必须整体重取，否则页面会把上一个学生的
  // 作业和错题继续显示给下一个学生。
  const learnerId = useLearnerId();
  const [pendingConfirmCount, setPendingConfirmCount] = useState(0);
  const [dueReviewCount, setDueReviewCount] = useState(0);
  const [unmasteredCount, setUnmasteredCount] = useState(0);
  const [papers, setPapers] = useState<TodayQueuePaper[]>([]);
  const [assignments, setAssignments] = useState<StudentAssignment[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [allFailed, setAllFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    // 试卷目录支持取消，卸载时直接中止在途请求；错题和复习进度的 loader 还没有
    // signal 参数，仍靠 cancelled 守卫拦住迟到的 setState。两者目的相同。
    const controller = new AbortController();

    const errors: string[] = [];

    const assignmentsRequest = loadStudentAssignments(learnerId)
      .then((items) => {
        if (!cancelled) setAssignments(items);
      })
      .catch((requestError) => {
        errors.push(requestError instanceof Error ? requestError.message : "作业加载失败");
      });

    const publicationsRequest = loadPublishedPublications(controller.signal)
      .then((items) => {
        if (cancelled) return;
        setPapers(items.map((item) => ({ ...item, started: hasStartedSession(item.publicationId) })));
      })
      .catch((requestError) => {
        if (controller.signal.aborted) return;
        errors.push(requestError instanceof Error ? requestError.message : "试卷目录加载失败");
      });

    const mistakesRequest = loadMistakes()
      .then((items) => {
        if (cancelled) return;
        setPendingConfirmCount(items.filter((item) => item.status === "pending_confirmation").length);
        setUnmasteredCount(items.filter((item) => item.status === "unmastered").length);
      })
      .catch((requestError) => {
        errors.push(requestError instanceof Error ? requestError.message : "错题本加载失败");
      });

    const progressRequest = loadLearningProgress()
      .then((progress) => {
        if (cancelled) return;
        setDueReviewCount(progress.dueReviewCount);
      })
      .catch((requestError) => {
        errors.push(requestError instanceof Error ? requestError.message : "复习进度加载失败");
      });

    void Promise.all([assignmentsRequest, publicationsRequest, mistakesRequest, progressRequest]).then(() => {
      if (cancelled) return;
      // 三路常常因为同一个后端不可用而失败，报错文案会完全重复；去重后只说一次。
      setError([...new Set(errors)].join("；"));
      setAllFailed(errors.length === 4);
      setLoading(false);
    });

    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [learnerId]);

  return { pendingConfirmCount, dueReviewCount, unmasteredCount, papers, assignments, loading, error, allFailed };
}
