import { useCallback, useEffect, useState } from "react";
import {
  createLearningSession,
  loadLearningMastery,
  loadLearningSession,
  recordExerciseAttempt,
  syncExerciseAttempts,
} from "../../api";
import type { ExerciseAttemptInput, ExerciseAttemptRecord, MasteryState, MistakeItem } from "../../types";

const PENDING_KEY = "dotty-learning-pending-attempts";
const DEMO_LEARNER_ID = "local-demo";

interface PendingAttempt {
  sessionId: string;
  attempt: ExerciseAttemptInput;
}

export interface AttemptQueueResult {
  status: "saved" | "queued";
  autoMistake?: MistakeItem | null;
}

function readPending(): PendingAttempt[] {
  try {
    const value = JSON.parse(localStorage.getItem(PENDING_KEY) || "[]");
    return Array.isArray(value) ? value as PendingAttempt[] : [];
  } catch {
    // 本地队列损坏不能阻止试卷打开；服务端幂等键仍会防止后续重复计分。
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
      // localStorage 可能比重建后的数据库活得更久。删除失效指针，并把未发送作答绑定到新会话。
      localStorage.removeItem(sessionKey);
    }
  }
  const session = await createLearningSession({ learnerId: DEMO_LEARNER_ID, publicationId });
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
 * 管理一份已发布试卷的持久会话与离线重试策略。
 *
 * 状态机独立于页面，防止内容工作台预览状态再次与真实学生遥测耦合。attemptId 是幂等键：
 * 本地队列可重复发送，但服务端只累计一次掌握度。
 */
export function usePublishedLearningSession(publicationId: string | undefined) {
  const [sessionId, setSessionId] = useState("");
  const [syncMessage, setSyncMessage] = useState("正在连接学习记录…");
  const [mastery, setMastery] = useState<MasteryState[]>([]);
  const [attempts, setAttempts] = useState<ExerciseAttemptRecord[]>([]);

  const mergeMastery = useCallback((next: MasteryState) => {
    setMastery((current) => [
      next,
      ...current.filter((item) => item.knowledgePoint !== next.knowledgePoint),
    ]);
  }, []);

  useEffect(() => {
    if (!publicationId) return;
    let cancelled = false;
    // 新打开的试卷绝不能短暂复用上一份试卷的 sessionId。
    setSessionId("");
    setAttempts([]);
    setSyncMessage("正在连接学习记录…");
    void openOrRecoverSession(publicationId).then(async ({ session, replacedSessionId }) => {
      if (cancelled) return;
      setSessionId(session.sessionId);
      setAttempts(session.attempts ?? []);
      const delivered = await flushPending(session.sessionId, replacedSessionId);
      if (!cancelled) setSyncMessage(delivered ? "离线学习记录已补传" : "学习记录已同步");
      // 补传可能包含上一次离线作答；重新读取一次会话，确保题目状态与服务端一致。
      if (delivered) {
        void loadLearningSession(session.sessionId).then((latest) => {
          if (!cancelled) setAttempts(latest.attempts ?? []);
        }).catch(() => undefined);
      }
      // 掌握度是作答日志的派生投影；先补传离线记录再加载，避免页面分数落后于答案历史。
      void loadLearningMastery(DEMO_LEARNER_ID).then((items) => {
        if (!cancelled) setMastery(items);
      }).catch(() => undefined);
    }).catch(() => {
      if (!cancelled) setSyncMessage("学习记录暂未连接，答案会在本机排队");
    });
    return () => { cancelled = true; };
  }, [publicationId]);

  const queueAttempt = useCallback(async (attempt: ExerciseAttemptInput): Promise<AttemptQueueResult> => {
    // 先更新本地快照，再等待网络。这样切题或刷新前，学生刚提交的答案不会因为
    // 请求延迟而从控件中消失；attemptId 保证随后服务端补传仍然幂等。
    setAttempts((current) => [
      ...current.filter((item) => item.attemptId !== attempt.attemptId),
      attempt,
    ]);
    if (!sessionId) {
      const pending = readPending().filter((item) => item.attempt.attemptId !== attempt.attemptId);
      writePending([...pending, { sessionId: "", attempt }]);
      setSyncMessage("学习会话尚未连接，答案已暂存");
      return { status: "queued" };
    }
    try {
      const result = await recordExerciseAttempt(sessionId, attempt);
      mergeMastery(result.mastery);
      setSyncMessage("学习记录已同步");
      return { status: "saved", autoMistake: result.autoMistake };
    } catch {
      const pending = readPending().filter((item) => item.attempt.attemptId !== attempt.attemptId);
      writePending([...pending, { sessionId, attempt }]);
      setSyncMessage("网络暂时不可用，记录已排队，稍后自动补传");
      return { status: "queued" };
    }
  }, [mergeMastery, sessionId]);

  return { queueAttempt, syncMessage, mastery, attempts };
}
