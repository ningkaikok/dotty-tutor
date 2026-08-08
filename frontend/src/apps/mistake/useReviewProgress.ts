import { useEffect, useState } from "react";
import { answerReview, loadLearningProgress, loadReviews, startReview } from "../../api";
import type { LearningProgress, ReviewTask, StructuredAnswerInput } from "../../types";

/** Keep the progress page's remote data and task transitions in one place. */
export function useReviewProgress() {
  const [progress, setProgress] = useState<LearningProgress | null>(null);
  const [tasks, setTasks] = useState<ReviewTask[]>([]);
  const [serverTime, setServerTime] = useState(Date.now() / 1000);
  const [busyTaskId, setBusyTaskId] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const refresh = async () => {
    const [nextProgress, reviewResult] = await Promise.all([
      loadLearningProgress(),
      loadReviews(),
    ]);
    setProgress(nextProgress);
    setTasks(reviewResult.items);
    setServerTime(reviewResult.serverTime);
  };

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    Promise.all([loadLearningProgress(), loadReviews()])
      .then(([nextProgress, reviewResult]) => {
        if (cancelled) return;
        setProgress(nextProgress);
        setTasks(reviewResult.items);
        setServerTime(reviewResult.serverTime);
      })
      .catch((requestError) => {
        if (!cancelled) setError(requestError instanceof Error ? requestError.message : "学习进度加载失败");
      })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  const updateTask = (updated: ReviewTask) => {
    setTasks((current) => current.map((task) => task.taskId === updated.taskId ? { ...task, ...updated } : task));
  };

  const start = async (taskId: string) => {
    setBusyTaskId(taskId);
    setError("");
    try {
      updateTask(await startReview(taskId));
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "复习题生成失败");
    } finally {
      setBusyTaskId("");
    }
  };

  const answer = async (taskId: string, input: StructuredAnswerInput) => {
    setBusyTaskId(taskId);
    setError("");
    try {
      updateTask(await answerReview(taskId, input));
      await refresh();
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "复习答案提交失败");
    } finally {
      setBusyTaskId("");
    }
  };

  return { progress, tasks, serverTime, busyTaskId, loading, error, start, answer };
}
