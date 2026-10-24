import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import {
  createLearningSession,
  loadLearningMastery,
  loadLearningSession,
  recordExerciseAttempt,
  syncExerciseAttempts,
} from "../../api/learning";
import type { ExerciseAttemptInput, ExerciseAttemptRecord, MasteryState, MistakeItem } from "../../types/index";
import { useLearnerId } from "../../api/identity";
import {
  enqueuePendingAttempt,
  OfflineAttemptQueueStorageError,
  readPendingAttempts,
  removePendingAttempts,
  type OfflineAttemptScope,
} from "./offlineAttemptQueue";
import {
  isLearningSessionForScope,
  learningSessionStorageKey,
  type LearningSessionScope,
} from "./learningSessionStorage";

export interface AttemptQueueResult {
  status: "saved" | "queued";
  autoMistake?: MistakeItem | null;
}

async function openOrRecoverSession(
  publicationId: string,
  assignmentId: string | undefined,
  learnerId: string,
  preferredSessionId?: string,
) {
  const scope: LearningSessionScope = { learnerId, publicationId, assignmentId };
  const sessionKey = learningSessionStorageKey(scope);
  if (preferredSessionId) {
    try {
      const preferred = await loadLearningSession(preferredSessionId);
      if (isLearningSessionForScope(preferred, scope)) {
        try { localStorage.setItem(sessionKey, preferred.sessionId); } catch { /* online session remains usable */ }
        return { session: preferred, replacedSessionId: "" };
      }
    } catch {
      // A stale or cross-scope URL hint falls through to the learner-scoped
      // pointer; the server still verifies ownership and the full session scope.
    }
  }
  let existingSessionId = "";
  let replacedSessionId = "";
  try {
    existingSessionId = localStorage.getItem(sessionKey) || "";
  } catch {
    // A session can still be used for online submissions when browser storage
    // is blocked; the offline queue will report the storage problem explicitly.
  }
  if (existingSessionId) {
    try {
      const session = await loadLearningSession(existingSessionId);
      if (isLearningSessionForScope(session, scope)) {
        return { session, replacedSessionId: "" };
      }
      // A pointer can be stale or tampered with. Never let a mismatched
      // learner/publication/assignment inherit another student's attempts.
      try { localStorage.removeItem(sessionKey); } catch { /* ignore blocked storage */ }
      existingSessionId = "";
    } catch {
      // localStorage 可能比重建后的数据库活得更久。删除失效指针，并把未发送作答绑定到新会话。
      try { localStorage.removeItem(sessionKey); } catch { /* queue reports storage failures when needed */ }
      replacedSessionId = existingSessionId;
    }
  }
  const session = await createLearningSession({ learnerId, publicationId, assignmentId });
  try { localStorage.setItem(sessionKey, session.sessionId); } catch { /* online session remains usable */ }
  return { session, replacedSessionId };
}

async function flushPending(scope: OfflineAttemptScope, replacedSessionId = ""): Promise<string[]> {
  const pending = readPendingAttempts(scope, { replacedSessionId });
  if (!pending.length) return [];
  try {
    await syncExerciseAttempts(scope.sessionId, pending.map((item) => item.attempt));
  } catch {
    // Keep the queue intact; online/visibility events will retry it later.
    return [];
  }
  const attemptIds = pending.map((item) => item.attempt.attemptId);
  removePendingAttempts(scope, attemptIds);
  return attemptIds;
}

/**
 * 管理一份已发布试卷的持久会话与离线重试策略。
 *
 * 状态机独立于页面，防止内容工作台预览状态再次与真实学生遥测耦合。attemptId 是幂等键：
 * 本地队列可重复发送，但服务端只累计一次掌握度。
 */
export function usePublishedLearningSession(
  publicationId: string | undefined,
  assignmentId?: string,
  preferredSessionId?: string,
) {
  const learnerId = useLearnerId();
  const scopeKey = `${learnerId}:${publicationId ?? ""}:${assignmentId || "practice"}:${preferredSessionId || ""}`;
  const scopeKeyRef = useRef(scopeKey);
  useLayoutEffect(() => { scopeKeyRef.current = scopeKey; }, [scopeKey]);
  const [loadedScopeKey, setLoadedScopeKey] = useState(scopeKey);
  const sessionRequestRef = useRef<{ sessionKey: string; promise: ReturnType<typeof openOrRecoverSession> } | null>(null);
  const [sessionId, setSessionId] = useState("");
  const [syncMessage, setSyncMessage] = useState("正在连接学习记录…");
  const [mastery, setMastery] = useState<MasteryState[]>([]);
  const [attempts, setAttempts] = useState<ExerciseAttemptRecord[]>([]);
  const [pendingAttemptIds, setPendingAttemptIds] = useState<string[]>([]);
  // 只有首次读取会话（或明确进入离线回退）后才可以用 attempts 决定起始题目。
  // 否则空数组会短暂把已完成试卷误判为未开始。
  const [sessionReady, setSessionReady] = useState(false);
  const replacedSessionIdRef = useRef("");
  const flushInFlightRef = useRef<Promise<string[]> | null>(null);

  const mergeMastery = useCallback((next: MasteryState) => {
    setMastery((current) => [
      next,
      ...current.filter((item) => item.knowledgePointId !== next.knowledgePointId),
    ]);
  }, []);

  useEffect(() => {
    if (!publicationId) return;
    let cancelled = false;
    // 新打开的试卷绝不能短暂复用上一份试卷的 sessionId。
    setSessionId("");
    setAttempts([]);
    setMastery([]);
    setPendingAttemptIds([]);
    setSessionReady(false);
    setSyncMessage("正在连接学习记录…");
    const sessionKey = `${learnerId}:${publicationId}:${assignmentId || "practice"}:${preferredSessionId || ""}`;
    setLoadedScopeKey(scopeKey);
    const existingRequest = sessionRequestRef.current?.sessionKey === sessionKey
      ? sessionRequestRef.current.promise
      : null;
    const sessionRequest = existingRequest ?? openOrRecoverSession(publicationId, assignmentId, learnerId, preferredSessionId);
    if (!existingRequest) sessionRequestRef.current = { sessionKey, promise: sessionRequest };
    void sessionRequest.then(async ({ session, replacedSessionId }) => {
      if (cancelled) return;
      setSessionId(session.sessionId);
      replacedSessionIdRef.current = replacedSessionId;
      const scope: OfflineAttemptScope = {
        learnerId,
        publicationId,
        sessionId: session.sessionId,
      };
      const savedAttempts = session.attempts ?? [];
      const savedAttemptIds = new Set(savedAttempts.map((attempt) => attempt.attemptId));
      const queuedAttempts = readPendingAttempts(scope, { replacedSessionId })
        .filter((item) => !savedAttemptIds.has(item.attempt.attemptId));
      setAttempts([...savedAttempts, ...queuedAttempts.map((item) => item.attempt)]);
      setPendingAttemptIds(queuedAttempts.map((item) => item.attempt.attemptId));
      setSessionReady(true);
      const deliveredIds = await flushPending(scope, replacedSessionId);
      if (!cancelled) {
        setPendingAttemptIds((current) => current.filter((id) => !deliveredIds.includes(id)));
        setSyncMessage(deliveredIds.length ? "离线学习记录已补传" : "学习记录已同步");
      }
      // 补传可能包含上一次离线作答；重新读取一次会话，确保题目状态与服务端一致。
      if (deliveredIds.length) {
        void loadLearningSession(session.sessionId).then((latest) => {
          if (!cancelled) setAttempts(latest.attempts ?? []);
        }).catch(() => undefined);
      }
      // 掌握度是作答日志的派生投影；先补传离线记录再加载，避免页面分数落后于答案历史。
      void loadLearningMastery(learnerId).then((items) => {
        if (!cancelled) setMastery(items);
      }).catch(() => undefined);
    }).catch((requestError) => {
      if (!cancelled) {
        setSessionReady(true);
        setSyncMessage(requestError instanceof OfflineAttemptQueueStorageError
          ? requestError.message
          : "学习记录暂未连接，答案会在本机排队");
      }
    });
    return () => { cancelled = true; };
  }, [assignmentId, learnerId, preferredSessionId, publicationId, scopeKey]);

  useEffect(() => {
    if (!publicationId || !sessionReady || loadedScopeKey !== scopeKey) return;
    let cancelled = false;
    const flush = () => {
      if (flushInFlightRef.current) return;
      let flushedSessionId = sessionId;
      const request = (async () => {
        let activeSessionId = sessionId;
        if (!activeSessionId) {
          // If the first session request failed while offline, retry it when
          // the browser becomes usable again instead of waiting for a reload.
          const recovered = await openOrRecoverSession(publicationId, assignmentId, learnerId, preferredSessionId);
          if (cancelled) return [];
          activeSessionId = recovered.session.sessionId;
          replacedSessionIdRef.current = recovered.replacedSessionId;
          setSessionId(activeSessionId);
          setAttempts(recovered.session.attempts ?? []);
        }
        flushedSessionId = activeSessionId;
        const scope: OfflineAttemptScope = {
          learnerId,
          publicationId,
          sessionId: activeSessionId,
        };
        return flushPending(scope, replacedSessionIdRef.current);
      })()
        .then((delivered) => {
          if (!cancelled && delivered.length) {
            setPendingAttemptIds((current) => current.filter((id) => !delivered.includes(id)));
            setSyncMessage("离线学习记录已补传");
            // The local snapshot already contains the answer, but the server
            // response is the source of truth for mastery and normalized
            // attempt records after a background retry.
            void loadLearningSession(flushedSessionId).then((latest) => {
              if (!cancelled) setAttempts(latest.attempts ?? []);
            }).catch(() => undefined);
            void loadLearningMastery(learnerId).then((items) => {
              if (!cancelled) setMastery(items);
            }).catch(() => undefined);
          }
          return delivered;
        })
        .catch((requestError) => {
          if (!cancelled && requestError instanceof OfflineAttemptQueueStorageError) {
            setSyncMessage(requestError.message);
          }
          return [];
        })
        .finally(() => { flushInFlightRef.current = null; });
      flushInFlightRef.current = request;
    };
    const handleOnline = () => flush();
    const handleVisibility = () => {
      if (document.visibilityState === "visible") flush();
    };
    window.addEventListener("online", handleOnline);
    document.addEventListener("visibilitychange", handleVisibility);
    return () => {
      cancelled = true;
      window.removeEventListener("online", handleOnline);
      document.removeEventListener("visibilitychange", handleVisibility);
    };
  }, [assignmentId, learnerId, loadedScopeKey, preferredSessionId, publicationId, scopeKey, sessionId, sessionReady]);

  const queueAttempt = useCallback(async (attempt: ExerciseAttemptInput): Promise<AttemptQueueResult> => {
    const requestScopeKey = scopeKey;
    const isCurrentScope = () => scopeKeyRef.current === requestScopeKey;
    if (!isCurrentScope()) throw new Error("学习身份已切换，请重新打开练习");
    // 先更新本地快照，再等待网络。这样切题或刷新前，学生刚提交的答案不会因为
    // 请求延迟而从控件中消失；attemptId 保证随后服务端补传仍然幂等。
    setAttempts((current) => [
      ...current.filter((item) => item.attemptId !== attempt.attemptId),
      attempt,
    ]);
    setPendingAttemptIds((current) => current.includes(attempt.attemptId)
      ? current
      : [...current, attempt.attemptId]);
    if (!sessionId) {
      try {
        enqueuePendingAttempt({ learnerId, publicationId: publicationId ?? "", sessionId: "" }, attempt);
      } catch (requestError) {
        if (isCurrentScope()) {
          setAttempts((current) => current.filter((item) => item.attemptId !== attempt.attemptId));
          setPendingAttemptIds((current) => current.filter((id) => id !== attempt.attemptId));
        }
        if (requestError instanceof OfflineAttemptQueueStorageError) {
          if (isCurrentScope()) setSyncMessage(requestError.message);
          throw requestError;
        }
        throw requestError;
      }
      setSyncMessage("学习会话尚未连接，答案已暂存");
      return { status: "queued" };
    }
    try {
      const result = await recordExerciseAttempt(sessionId, attempt);
      // A request may finish after the student changes identity or opens a
      // different session. The server write stays tied to its original session,
      // but its projection must never enter the newly active learner's view.
      if (!isCurrentScope()) return { status: "saved", autoMistake: result.autoMistake };
      setPendingAttemptIds((current) => current.filter((id) => id !== attempt.attemptId));
      mergeMastery(result.mastery);
      setSyncMessage("学习记录已同步");
      return { status: "saved", autoMistake: result.autoMistake };
    } catch {
      try {
        enqueuePendingAttempt({ learnerId, publicationId: publicationId ?? "", sessionId }, attempt);
      } catch (requestError) {
        if (isCurrentScope()) {
          setAttempts((current) => current.filter((item) => item.attemptId !== attempt.attemptId));
          setPendingAttemptIds((current) => current.filter((id) => id !== attempt.attemptId));
        }
        if (requestError instanceof OfflineAttemptQueueStorageError) {
          if (isCurrentScope()) {
            setSyncMessage(requestError.message);
          }
          throw requestError;
        }
        throw requestError;
      }
      if (isCurrentScope()) setSyncMessage("网络暂时不可用，记录已排队，稍后自动补传");
      return { status: "queued" };
    }
  }, [learnerId, mergeMastery, publicationId, scopeKey, sessionId]);

  const scopeReady = loadedScopeKey === scopeKey;
  return {
    queueAttempt,
    syncMessage: scopeReady ? syncMessage : "正在连接学习记录…",
    mastery: scopeReady ? mastery : [],
    attempts: scopeReady ? attempts : [],
    pendingAttemptIds: scopeReady ? pendingAttemptIds : [],
    sessionReady: scopeReady && sessionReady,
  };
}
