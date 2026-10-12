import { useCallback, useEffect, useMemo, useState } from "react";
import type { CoursewarePresentationDocument } from "./coursewarePresentation";

type PlaybackPanel = "content" | "notes" | "answers";
type PresentationMode = "deck" | "rail";

interface PlaybackSession {
  pageId: string;
  panel: PlaybackPanel;
  mode: PresentationMode;
  highContrast: boolean;
  timerSeconds: number;
  timerDeadline: number | null;
  responses: Record<string, string[]>;
  checks: Record<string, string[]>;
  revealed: Record<string, string[]>;
}

interface StoredPlayback {
  publicationId: string;
  actorScope: string;
  state: PlaybackSession;
}

function sessionKey(publicationId: string, actorScope: string) {
  return `dotty-courseware-playback:v2:${encodeURIComponent(publicationId)}:${encodeURIComponent(actorScope)}`;
}

function initialSession(document: CoursewarePresentationDocument): PlaybackSession {
  return {
    pageId: document.pages[0]?.pageId ?? "",
    panel: "content",
    mode: "deck",
    highContrast: false,
    timerSeconds: document.durationMinutes * 60,
    timerDeadline: null,
    responses: {},
    checks: {},
    revealed: {},
  };
}

function parseSession(document: CoursewarePresentationDocument, actorScope: string, now: number): PlaybackSession {
  const fallback = initialSession(document);
  try {
    const stored = JSON.parse(localStorage.getItem(sessionKey(document.publicationId, actorScope)) || "null") as StoredPlayback | null;
    if (!stored || stored.publicationId !== document.publicationId || stored.actorScope !== actorScope || !stored.state || typeof stored.state !== "object") return fallback;
    const value = stored.state;
    const pageExists = document.pages.some((page) => page.pageId === value.pageId);
    const timerSeconds = Number.isFinite(value.timerSeconds) ? Math.max(0, Math.floor(value.timerSeconds)) : fallback.timerSeconds;
    const timerDeadline = Number.isFinite(value.timerDeadline) && value.timerDeadline !== null
      ? value.timerDeadline
      : null;
    return {
      pageId: pageExists ? value.pageId : fallback.pageId,
      panel: value.panel === "notes" && document.canRevealAnswers && document.pages.some((page) => Boolean(page.teacherNotes))
        ? "notes"
        : value.panel === "answers" && document.canRevealAnswers ? "answers" : "content",
      mode: value.mode === "rail" ? "rail" : "deck",
      highContrast: Boolean(value.highContrast),
      timerSeconds: timerDeadline === null ? timerSeconds : Math.max(0, Math.ceil((timerDeadline - now) / 1000)),
      timerDeadline: timerDeadline !== null && timerDeadline > now ? timerDeadline : null,
      responses: isStringArrayRecord(value.responses) ? value.responses : {},
      checks: isStringArrayRecord(value.checks) ? value.checks : {},
      revealed: isStringArrayRecord(value.revealed) ? value.revealed : {},
    };
  } catch {
    return fallback;
  }
}

function isStringArrayRecord(value: unknown): value is Record<string, string[]> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value)
    && Object.values(value as Record<string, unknown>).every((items) => Array.isArray(items) && items.every((item) => typeof item === "string"));
}

export function useCoursewarePlayback(document: CoursewarePresentationDocument, now: () => number = Date.now, actorScope = "teacher:local") {
  const [stored, setStored] = useState<StoredPlayback>(() => ({
    publicationId: document.publicationId,
    actorScope,
    state: parseSession(document, actorScope, now()),
  }));
  const current = stored.publicationId === document.publicationId && stored.actorScope === actorScope
    ? stored.state
    : parseSession(document, actorScope, now());

  const update = useCallback((change: (previous: PlaybackSession) => PlaybackSession) => {
    setStored((previous) => {
      const base = previous.publicationId === document.publicationId && previous.actorScope === actorScope ? previous.state : parseSession(document, actorScope, now());
      return { publicationId: document.publicationId, actorScope, state: change(base) };
    });
  }, [actorScope, document, now]);

  useEffect(() => {
    if (stored.publicationId !== document.publicationId || stored.actorScope !== actorScope) {
      setStored({ publicationId: document.publicationId, actorScope, state: parseSession(document, actorScope, now()) });
    }
  }, [actorScope, document, now, stored.actorScope, stored.publicationId]);

  useEffect(() => {
    if (stored.publicationId !== document.publicationId || stored.actorScope !== actorScope) return;
    try {
      localStorage.setItem(sessionKey(document.publicationId, actorScope), JSON.stringify(stored));
    } catch {
      // Private browsing or a full storage quota must not stop a lesson.
    }
  }, [actorScope, document.publicationId, stored]);

  useEffect(() => {
    if (stored.publicationId !== document.publicationId || stored.actorScope !== actorScope || current.timerDeadline === null) return;
    const deadline = current.timerDeadline;
    const tick = () => update((previous) => {
      if (previous.timerDeadline !== deadline) return previous;
      const seconds = Math.max(0, Math.ceil((deadline - now()) / 1000));
      return { ...previous, timerSeconds: seconds, timerDeadline: seconds > 0 ? deadline : null };
    });
    const interval = window.setInterval(tick, 1000);
    tick();
    return () => window.clearInterval(interval);
  }, [actorScope, current.timerDeadline, document.publicationId, now, stored.actorScope, stored.publicationId, update]);

  const page = document.pages.find((item) => item.pageId === current.pageId) ?? document.pages[0];
  const pageIndex = page ? document.pages.findIndex((item) => item.pageId === page.pageId) : -1;
  const section = page ? document.sections.find((item) => item.sectionId === page.sectionId) : undefined;
  const pageIdsInCurrentSection = useMemo(
    () => new Set(section?.pageIds ?? []),
    [section?.pageIds],
  );

  const navigateTo = (pageId: string) => {
    if (!document.pages.some((item) => item.pageId === pageId)) return;
    update((previous) => ({ ...previous, pageId }));
  };
  const setPanel = (panel: PlaybackPanel) => update((previous) => ({ ...previous, panel }));
  const setMode = (mode: PresentationMode) => update((previous) => ({ ...previous, mode }));
  const toggleContrast = () => update((previous) => ({ ...previous, highContrast: !previous.highContrast }));
  const startOrPauseTimer = () => update((previous) => {
    if (previous.timerDeadline !== null) {
      const seconds = Math.max(0, Math.ceil((previous.timerDeadline - now()) / 1000));
      return { ...previous, timerSeconds: seconds, timerDeadline: null };
    }
    if (previous.timerSeconds <= 0) return { ...previous, timerSeconds: document.durationMinutes * 60, timerDeadline: now() + document.durationMinutes * 60 * 1000 };
    return { ...previous, timerDeadline: now() + previous.timerSeconds * 1000 };
  });
  const setResponse = (key: string, index: number, value: string) => update((previous) => {
    const responses = [...(previous.responses[key] ?? [])];
    responses[index] = value;
    return { ...previous, responses: { ...previous.responses, [key]: responses } };
  });
  const setChoice = (key: string, value: string, multiple = false) => update((previous) => {
    const selected = new Set(previous.responses[key] ?? []);
    if (selected.has(value)) selected.delete(value);
    else selected.add(value);
    return { ...previous, responses: { ...previous.responses, [key]: multiple ? [...selected] : [value] } };
  });
  const toggleCheck = (key: string, value: string) => update((previous) => {
    const selected = new Set(previous.checks[key] ?? []);
    if (selected.has(value)) selected.delete(value);
    else selected.add(value);
    return { ...previous, checks: { ...previous.checks, [key]: [...selected] } };
  });
  const revealStep = (revealId: string) => {
    if (!document.canRevealAnswers || !page) return;
    const steps = page.revealSteps ?? [];
    const revealed = current.revealed[page.pageId] ?? [];
    const next = steps.find((item) => !revealed.includes(item.revealId));
    if (!next || next.revealId !== revealId) return;
    update((previous) => ({
      ...previous,
      panel: "answers",
      revealed: { ...previous.revealed, [page.pageId]: [...(previous.revealed[page.pageId] ?? []), revealId] },
    }));
  };
  const revealNext = () => {
    const next = page?.revealSteps?.find((step) => !(current.revealed[page.pageId] ?? []).includes(step.revealId));
    if (next) revealStep(next.revealId);
  };
  const resetCurrentPage = () => {
    if (!page) return;
    const pagePrefix = `${page.pageId}:`;
    update((previous) => ({
      ...previous,
      responses: Object.fromEntries(Object.entries(previous.responses).filter(([key]) => !key.startsWith(pagePrefix))),
      checks: Object.fromEntries(Object.entries(previous.checks).filter(([key]) => !key.startsWith(pagePrefix))),
      revealed: { ...previous.revealed, [page.pageId]: [] },
      panel: "content",
    }));
  };
  const resetSession = () => update(() => initialSession(document));
  const writtenCount = (key: string) => (current.responses[key] ?? []).filter((value) => value.trim().length > 0).length;
  const inputResponses = (key: string) => current.responses[key] ?? [];
  const activityResponse = (key: string) => current.responses[key]?.[0] ?? "";
  const checkedValues = (key: string) => current.checks[key] ?? [];
  const selectedChoice = (key: string) => current.responses[key]?.[0] ?? "";
  const selectedChoices = (key: string) => current.responses[key] ?? [];
  const isChecked = (key: string, itemId: string) => checkedValues(key).includes(itemId);

  return {
    state: current,
    page,
    pageIndex,
    section,
    pageIdsInCurrentSection,
    navigateTo,
    setPanel,
    setMode,
    toggleContrast,
    startOrPauseTimer,
    setResponse,
    setChoice,
    toggleCheck,
    revealNext,
    revealStep,
    resetCurrentPage,
    resetSession,
    inputResponses,
    activityResponse,
    writtenCount,
    isChecked,
    selectedChoice,
    selectedChoices,
  };
}
