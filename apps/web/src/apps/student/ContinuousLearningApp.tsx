import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { useNavigate } from "react-router";
import { AppHeader, Button } from "../../components/ui";
import { answerReview } from "../../api/reviews";
import {
  loadNextLearningAction,
  startNextLearningAction,
  type ContinuousLearningSnapshot,
} from "../../api/continuousLearning";
import { useLearnerId } from "../../api/identity";
import { ApiRequestError } from "../../api/client";
import { Notice, Skeleton } from "../../components/ui";
import type { ReviewTask, StructuredAnswerInput } from "../../types/index";
import { ReviewTaskCard } from "../mistake/components/ReviewTaskCard";
import "./student.css";

const inFlightStarts = new Map<string, Promise<ContinuousLearningSnapshot>>();

function startNextOnce(learnerId: string): Promise<ContinuousLearningSnapshot> {
  const existing = inFlightStarts.get(learnerId);
  if (existing) return existing;
  const request = startNextLearningAction(learnerId).finally(() => {
    if (inFlightStarts.get(learnerId) === request) inFlightStarts.delete(learnerId);
  });
  inFlightStarts.set(learnerId, request);
  return request;
}

/** One task at a time; the backend selects from saved attempt, mistake, and review facts. */
export function ContinuousLearningApp() {
  const navigate = useNavigate();
  const learnerId = useLearnerId();
  const learnerIdAtRender = useRef(learnerId);
  useLayoutEffect(() => { learnerIdAtRender.current = learnerId; }, [learnerId]);
  const [reviewState, setReviewState] = useState<{ learnerId: string; task: ReviewTask | null }>({ learnerId, task: null });
  const [busyState, setBusyState] = useState<{ learnerId: string; taskId: string } | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [pausedLearnerId, setPausedLearnerId] = useState("");
  const requestId = useRef(0);
  const mounted = useRef(false);
  const retryTimer = useRef<number | null>(null);
  const advanceTimer = useRef<number | null>(null);
  const reviewTask = reviewState.learnerId === learnerId ? reviewState.task : null;
  const busyTaskId = busyState?.learnerId === learnerId ? busyState.taskId : "";
  const autoContinuePaused = pausedLearnerId === learnerId;

  const deliver = useCallback((snapshot: ContinuousLearningSnapshot) => {
    const item = snapshot.next;
    setError("");
    setReviewState({ learnerId, task: null });
    if (item.kind === "assignment" && item.publicationId) {
      const query = new URLSearchParams({ continuous: "1" });
      if (item.assignmentId) query.set("assignmentId", item.assignmentId);
      if (item.sessionId) query.set("sessionId", item.sessionId);
      navigate(`/learn/papers/${encodeURIComponent(item.publicationId)}?${query.toString()}`);
    } else if (item.kind === "practice" && item.publicationId) {
      const query = new URLSearchParams({ continuous: "1" });
      if (item.sessionId) query.set("sessionId", item.sessionId);
      navigate(`/learn/papers/${encodeURIComponent(item.publicationId)}?${query.toString()}`);
    } else if (item.kind === "confirm_mistake" && item.mistakeId) {
      navigate(`/mistakes/${encodeURIComponent(item.mistakeId)}/confirm?continuous=1`);
    } else if (item.kind === "correct_mistake" && item.mistakeId) {
      navigate(`/mistakes/${encodeURIComponent(item.mistakeId)}/tutor?continuous=1`);
    } else if (item.kind === "review" && item.taskId && item.status === "ready") {
      setReviewState({ learnerId, task: item as unknown as ReviewTask });
      setLoading(false);
    } else {
      navigate("/learn", { replace: true });
    }
  }, [learnerId, navigate]);

  const beginNext = useCallback(async () => {
    const requestLearnerId = learnerId;
    const currentRequest = ++requestId.current;
    const isCurrent = () => mounted.current && currentRequest === requestId.current
      && learnerIdAtRender.current === requestLearnerId;
    setLoading(true);
    setError("");
    setReviewState({ learnerId: requestLearnerId, task: null });
    setBusyState(null);
    setPausedLearnerId("");
    try {
      let snapshot: ContinuousLearningSnapshot | null = null;
      for (let attempt = 0; attempt < 20; attempt += 1) {
        try {
          snapshot = await startNextOnce(requestLearnerId);
          break;
        } catch (requestError) {
          if (!(requestError instanceof ApiRequestError) || requestError.status !== 409 || attempt === 19) throw requestError;
          const preview = await loadNextLearningAction(requestLearnerId);
          if (preview.next.kind === "review" && preview.next.status === "generating") {
            await new Promise<void>((resolve) => {
              retryTimer.current = window.setTimeout(resolve, 500);
            });
            if (!isCurrent()) return;
            continue;
          }
        }
      }
      if (isCurrent() && snapshot) deliver(snapshot);
      else if (isCurrent()) navigate("/learn", { replace: true });
    } catch (requestError) {
      if (isCurrent()) {
        setError(requestError instanceof Error ? requestError.message : "下一项学习任务暂时无法打开");
        setLoading(false);
      }
    }
  }, [deliver, learnerId, navigate]);

  useEffect(() => {
    mounted.current = true;
    void beginNext();
    return () => {
      mounted.current = false;
      requestId.current += 1;
      if (retryTimer.current !== null) window.clearTimeout(retryTimer.current);
      if (advanceTimer.current !== null) window.clearTimeout(advanceTimer.current);
    };
  }, [beginNext]);

  const answer = async (taskId: string, input: StructuredAnswerInput) => {
    if (busyTaskId || !reviewTask) return;
    const answerLearnerId = learnerId;
    const answerRequest = requestId.current;
    const isCurrent = () => mounted.current && answerRequest === requestId.current
      && learnerIdAtRender.current === answerLearnerId;
    setBusyState({ learnerId: answerLearnerId, taskId });
    setError("");
    try {
      const completed = await answerReview(taskId, input);
      if (!isCurrent()) return;
      setReviewState({ learnerId: answerLearnerId, task: completed });
      // Keep the result visible for a short reading window; the learner can pause it here.
      if (completed.status === "completed") {
        setPausedLearnerId("");
        advanceTimer.current = window.setTimeout(() => {
          if (isCurrent()) void beginNext();
        }, 2500);
      }
    } catch (requestError) {
      if (isCurrent()) setError(requestError instanceof Error ? requestError.message : "复习答案提交失败");
    } finally {
      if (isCurrent()) setBusyState(null);
    }
  };

  const pauseAutoContinue = () => {
    if (advanceTimer.current !== null) window.clearTimeout(advanceTimer.current);
    setPausedLearnerId(learnerId);
  };
  const pageHeader = <AppHeader
    backTo="/learn"
    backLabel="暂停，回到学习入口"
    subtitle="继续学习"
    actions={reviewTask?.status === "completed" && (autoContinuePaused
      ? <Button type="button" className="continuous-learning-action" onClick={() => void beginNext()}>继续下一项</Button>
      : <Button type="button" className="continuous-learning-action" onClick={pauseAutoContinue}>留在这道复习题</Button>)}
  />;

  if (error && !reviewTask) {
    return (
      <main className="continuous-learning-shell">
        {pageHeader}
        <Notice tone="danger">{error}</Notice>
        <button type="button" className="continuous-retry" onClick={() => void beginNext()}>重试</button>
      </main>
    );
  }

  if (loading && !reviewTask) return <main className="continuous-learning-shell">{pageHeader}<Skeleton label="正在恢复学习进度…" /></main>;

  return (
    <main className="continuous-learning-shell">
      {pageHeader}
      {error && <Notice tone="danger">{error}</Notice>}
      {reviewTask && <ReviewTaskCard
        key={`${learnerId}:${reviewTask.taskId}`}
        task={reviewTask}
        serverTime={Date.now() / 1000}
        busy={busyTaskId === reviewTask.taskId}
        onStart={async () => undefined}
        onAnswer={answer}
      />}
    </main>
  );
}
