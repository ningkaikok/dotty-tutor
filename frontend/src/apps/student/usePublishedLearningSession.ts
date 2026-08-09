import { useCallback, useEffect, useState } from "react";
import {
  createLearningSession,
  loadLearningSession,
  recordExerciseAttempt,
  syncExerciseAttempts,
} from "../../api";
import type { ExerciseAttemptInput } from "../../types";

const PENDING_KEY = "dotty-learning-pending-attempts";

interface PendingAttempt {
  sessionId: string;
  attempt: ExerciseAttemptInput;
}

function readPending(): PendingAttempt[] {
  try {
    const value = JSON.parse(localStorage.getItem(PENDING_KEY) || "[]");
    return Array.isArray(value) ? value as PendingAttempt[] : [];
  } catch {
    // A malformed local queue must not prevent the paper itself from opening.
    return [];
  }
}

function writePending(items: PendingAttempt[]) {
  localStorage.setItem(PENDING_KEY, JSON.stringify(items));
}

async function openOrRecoverSession(publicationId: string) {
  const sessionKey = `dotty-learning-session:${publicationId}`;
  const existingSessionId = localStorage.getItem(sessionKey);
  if (existingSessionId) {
    try {
      return { session: await loadLearningSession(existingSessionId), replacedSessionId: "" };
    } catch {
      // Local storage can outlive a recreated database. Remove the stale
      // pointer and bind its unsent attempts to the replacement session.
      localStorage.removeItem(sessionKey);
    }
  }
  const session = await createLearningSession({ learnerId: "local-demo", lessonId: publicationId });
  localStorage.setItem(sessionKey, session.sessionId);
  return { session, replacedSessionId: existingSessionId ?? "" };
}

async function flushPending(activeSessionId: string, replacedSessionId = ""): Promise<number> {
  const pending = readPending();
  if (!pending.length) return 0;
  const normalized = pending.map((item) => ({
    ...item,
    sessionId: !item.sessionId || item.sessionId === replacedSessionId ? activeSessionId : item.sessionId,
  }));
  const groups = [...new Set(normalized.map((item) => item.sessionId))];
  const results = await Promise.allSettled(groups.map((sessionId) =>
    syncExerciseAttempts(
      sessionId,
      normalized.filter((item) => item.sessionId === sessionId).map((item) => item.attempt),
    ),
  ));
  const delivered = new Set<string>();
  results.forEach((result, index) => {
    if (result.status !== "fulfilled") return;
    normalized
      .filter((item) => item.sessionId === groups[index])
      .forEach((item) => delivered.add(item.attempt.attemptId));
  });
  if (delivered.size) {
    writePending(readPending().filter((item) => !delivered.has(item.attempt.attemptId)));
  }
  return delivered.size;
}

/**
 * Owns the durable session and offline retry policy for one published paper.
 * Keeping this state machine outside the page prevents studio preview state
 * and real learner telemetry from being accidentally coupled again.
 */
export function usePublishedLearningSession(publicationId: string | undefined) {
  const [sessionId, setSessionId] = useState("");
  const [syncMessage, setSyncMessage] = useState("正在连接学习记录…");

  useEffect(() => {
    if (!publicationId) return;
    let cancelled = false;
    void openOrRecoverSession(publicationId).then(async ({ session, replacedSessionId }) => {
      if (cancelled) return;
      setSessionId(session.sessionId);
      const delivered = await flushPending(session.sessionId, replacedSessionId);
      if (!cancelled) setSyncMessage(delivered ? "离线学习记录已补传" : "学习记录已同步");
    }).catch(() => {
      if (!cancelled) setSyncMessage("学习记录暂未连接，答案会在本机排队");
    });
    return () => { cancelled = true; };
  }, [publicationId]);

  const queueAttempt = useCallback((attempt: ExerciseAttemptInput) => {
    if (!sessionId) {
      const pending = readPending().filter((item) => item.attempt.attemptId !== attempt.attemptId);
      writePending([...pending, { sessionId: "", attempt }]);
      setSyncMessage("学习会话尚未连接，答案已暂存");
      return;
    }
    void recordExerciseAttempt(sessionId, attempt).then(
      () => setSyncMessage("学习记录已同步"),
      () => {
        const pending = readPending().filter((item) => item.attempt.attemptId !== attempt.attemptId);
        writePending([...pending, { sessionId, attempt }]);
        setSyncMessage("网络暂时不可用，记录已排队，稍后自动补传");
      },
    );
  }, [sessionId]);

  return { queueAttempt, syncMessage };
}
