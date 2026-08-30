import { useEffect, useState } from "react";
import { loadPublishedPublications } from "../../api/publications";
import { loadMistakes } from "../../api/mistakes";
import { loadLearningProgress } from "../../api/reviews";
import type { PublicationSummary } from "../../types/index";

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
  loading: boolean;
  /** 三路请求里失败的部分才会在这里留言；能拿到的数据仍然照常展示。 */
  error: string;
  /**
   * 三路全部失败。此时“没有待办”是读不到数据的假象，不是真的做完了——
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
 * 班级和作业指派的后端表还不存在，这里刻意不发明新接口：只组合三个已有的
 * 只读接口（已发布试卷、错题、复习进度），任何一路失败都不应该拖垮另外
 * 两路——因此用 allSettled 语义（Promise.all 分别 catch）而不是让一次失败
 * 直接让整页失败。
 */
export function useStudentTodayQueue(): StudentTodayQueue {
  const [pendingConfirmCount, setPendingConfirmCount] = useState(0);
  const [dueReviewCount, setDueReviewCount] = useState(0);
  const [unmasteredCount, setUnmasteredCount] = useState(0);
  const [papers, setPapers] = useState<TodayQueuePaper[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [allFailed, setAllFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    // 试卷目录支持取消，卸载时直接中止在途请求；错题和复习进度的 loader 还没有
    // signal 参数，仍靠 cancelled 守卫拦住迟到的 setState。两者目的相同。
    const controller = new AbortController();

    const errors: string[] = [];

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

    void Promise.all([publicationsRequest, mistakesRequest, progressRequest]).then(() => {
      if (cancelled) return;
      // 三路常常因为同一个后端不可用而失败，报错文案会完全重复；去重后只说一次。
      setError([...new Set(errors)].join("；"));
      setAllFailed(errors.length === 3);
      setLoading(false);
    });

    return () => {
      cancelled = true;
      controller.abort();
    };
  }, []);

  return { pendingConfirmCount, dueReviewCount, unmasteredCount, papers, loading, error, allFailed };
}
