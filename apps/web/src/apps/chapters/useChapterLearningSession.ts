import { useEffect, useMemo, useRef, useState } from "react";
import { loadChapterAttempt, submitChapterAttempt } from "../../api/chapters";
import { currentLearnerId, useLearnerId } from "../../api/identity";
import type { ChapterAttemptResult, ChapterEvidenceRef, PublishedChapter } from "../../types/chapter";

interface LessonDraft {
  answer: string;
  evidenceRefs: ChapterEvidenceRef[];
  attemptId?: string;
  result?: ChapterAttemptResult;
}

interface SavedSession {
  lessonIndex: number;
  drafts: Record<string, LessonDraft>;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function readEvidenceRefs(value: unknown): ChapterEvidenceRef[] {
  if (!Array.isArray(value)) return [];
  return value.filter(isRecord).filter((ref) => typeof ref.sourceRevisionId === "string" && typeof ref.page === "number")
    .map((ref) => ({
      sourceRevisionId: ref.sourceRevisionId as string,
      page: ref.page as number,
      ...(typeof ref.regionId === "string" ? { regionId: ref.regionId } : {}),
      ...(typeof ref.sentenceId === "string" ? { sentenceId: ref.sentenceId } : {}),
      ...(typeof ref.quote === "string" ? { quote: ref.quote } : {}),
    }));
}

function readSession(key: string, lessonCount: number): SavedSession {
  try {
    const raw: unknown = JSON.parse(localStorage.getItem(key) || "null");
    if (!isRecord(raw)) return { lessonIndex: 0, drafts: {} };
    const index = typeof raw.lessonIndex === "number" && Number.isInteger(raw.lessonIndex)
      ? Math.min(Math.max(0, raw.lessonIndex), Math.max(0, lessonCount - 1)) : 0;
    const sourceDrafts = isRecord(raw.drafts) ? raw.drafts : {};
    const drafts: Record<string, LessonDraft> = {};
    for (const [lessonId, draft] of Object.entries(sourceDrafts)) {
      if (!isRecord(draft)) continue;
      const refs = readEvidenceRefs(draft.evidenceRefs);
      drafts[lessonId] = {
        answer: typeof draft.answer === "string" ? draft.answer : "",
        evidenceRefs: refs,
        ...(typeof draft.attemptId === "string" ? { attemptId: draft.attemptId } : {}),
        // Cached feedback is not an authority. A reload restores only a server-verified attempt.
      };
    }
    return { lessonIndex: index, drafts };
  } catch {
    return { lessonIndex: 0, drafts: {} };
  }
}

function attemptId() {
  return globalThis.crypto?.randomUUID?.() ?? `chapter-attempt-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

/**
 * Isolates drafts by learner, chapter, and immutable publication; server attempts are authoritative feedback.
 * Async restore/submit responses are ignored after any part of that scope changes.
 */
export function useChapterLearningSession(chapter: PublishedChapter) {
  const learnerId = useLearnerId();
  const storageKey = `dotty-chapter-session:${encodeURIComponent(learnerId)}:${chapter.chapterId}:${chapter.publicationId}`;
  const [session, setSession] = useState<SavedSession>(() => readSession(storageKey, chapter.lessons.length));
  const [sessionScope, setSessionScope] = useState(storageKey);
  const currentSession = sessionScope === storageKey ? session : readSession(storageKey, chapter.lessons.length);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const restored = useRef("");
  const activeStorageKey = useRef(storageKey);
  const lesson = chapter.lessons[currentSession.lessonIndex] ?? chapter.lessons[0];
  const draft = useMemo(() => lesson ? currentSession.drafts[lesson.lessonId] ?? { answer: "", evidenceRefs: [] } : { answer: "", evidenceRefs: [] }, [currentSession.drafts, lesson]);

  useEffect(() => {
    activeStorageKey.current = storageKey;
  }, [storageKey]);

  useEffect(() => {
    if (sessionScope !== storageKey) return;
    try { localStorage.setItem(storageKey, JSON.stringify(session)); } catch { /* A private browser may disable local storage; server attempts still work. */ }
  }, [session, sessionScope, storageKey]);

  useEffect(() => {
    if (sessionScope === storageKey) return;
    setSession(currentSession);
    setSessionScope(storageKey);
    setError("");
    setBusy(false);
  }, [chapter.chapterId, chapter.publicationId, currentSession, learnerId, sessionScope, storageKey]);

  useEffect(() => {
    if (restored.current === storageKey) return;
    restored.current = storageKey;
    const saved = currentSession.drafts;
    void Promise.all(Object.entries(saved).filter(([, item]) => item.attemptId).map(async ([lessonId, item]) => {
      try {
        const result = await loadChapterAttempt(chapter.chapterId, item.attemptId!);
        if (activeStorageKey.current !== storageKey || currentLearnerId() !== learnerId
          || result.attemptId !== item.attemptId
          || (result.publicationId && result.publicationId !== chapter.publicationId)
          || (result.learnerId && result.learnerId !== learnerId)
          || (result.lessonId && result.lessonId !== lessonId)) return;
        setSession((current) => current.drafts[lessonId]?.attemptId !== item.attemptId ? current : ({
          ...current,
          drafts: { ...current.drafts, [lessonId]: { ...current.drafts[lessonId], result } },
        }));
      } catch {
        // No completed server attempt exists yet (for example the page was refreshed during a failed request).
      }
    }));
  }, [chapter.chapterId, chapter.publicationId, currentSession.drafts, learnerId, storageKey]);

  const updateDraft = (patch: Partial<LessonDraft>) => {
    if (!lesson) return;
    setSession((current) => ({
      ...(sessionScope === storageKey ? current : currentSession),
      drafts: {
        ...(sessionScope === storageKey ? current.drafts : currentSession.drafts),
        [lesson.lessonId]: {
          answer: (sessionScope === storageKey ? current.drafts : currentSession.drafts)[lesson.lessonId]?.answer ?? "",
          evidenceRefs: (sessionScope === storageKey ? current.drafts : currentSession.drafts)[lesson.lessonId]?.evidenceRefs ?? [],
          ...patch,
          attemptId: patch.attemptId,
          result: patch.result,
        },
      },
    }));
  };

  const changeAnswer = (answer: string) => updateDraft({ answer, attemptId: undefined, result: undefined });
  const changeEvidence = (evidenceRefs: ChapterEvidenceRef[]) => updateDraft({ evidenceRefs, attemptId: undefined, result: undefined });

  const submit = async () => {
    if (!lesson || !lesson.questionPayload?.question.id || !draft.answer.trim() || !draft.evidenceRefs.length || busy) return;
    const stableAttemptId = draft.attemptId || attemptId();
    const request = {
      attemptId: stableAttemptId,
      lessonId: lesson.lessonId,
      questionId: lesson.questionPayload.question.id,
      publicationId: chapter.publicationId,
      learnerId,
      answer: { text: draft.answer.trim() },
      evidenceRefs: draft.evidenceRefs,
    };
    updateDraft({ attemptId: stableAttemptId });
    try { localStorage.setItem(storageKey, JSON.stringify({ ...currentSession, drafts: { ...currentSession.drafts, [lesson.lessonId]: { ...draft, attemptId: stableAttemptId } } })); } catch { /* Storage is only a restore aid. */ }
    setBusy(true);
    setError("");
    try {
      const result = await submitChapterAttempt(chapter.chapterId, request);
      if (activeStorageKey.current === storageKey && currentLearnerId() === learnerId) {
        setSession((current) => current.drafts[lesson.lessonId]?.attemptId !== stableAttemptId ? current : ({
          ...current,
          drafts: { ...current.drafts, [lesson.lessonId]: { ...current.drafts[lesson.lessonId], result } },
        }));
      }
    } catch (requestError) {
      if (activeStorageKey.current === storageKey && currentLearnerId() === learnerId) setError(requestError instanceof Error ? requestError.message : "提交失败");
    } finally {
      if (activeStorageKey.current === storageKey && currentLearnerId() === learnerId) setBusy(false);
    }
  };

  const selectLesson = (lessonIndex: number) => setSession((current) => ({ ...(sessionScope === storageKey ? current : currentSession), lessonIndex }));

  return { lesson, lessonIndex: currentSession.lessonIndex, lessonCount: chapter.lessons.length, draft, busy, error, changeAnswer, changeEvidence, submit, selectLesson };
}
