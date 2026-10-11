import { useEffect, useLayoutEffect, useRef, useState } from "react";
import {
  cancelLessonMediaRender,
  createLessonMediaDraft,
  createLessonMediaRevision,
  enqueueLessonMediaRender,
  listLessonMedia,
  publishLessonMedia,
  reviewLessonMedia,
  type LessonMedia,
} from "../../api/lessonMedia";

export type MediaStudioLesson = { lessonId: string; title: string; sourceRevisionId: string };

type ScopeToken = { scopeKey: string; generation: number };

type StudioState = {
  scopeKey: string;
  generation: number;
  items: LessonMedia[];
  activeId: string;
  editsMediaId: string;
  edits: Record<string, string>;
  reviewMediaId: string;
  reviewNote: string;
  busy: boolean;
  error: string;
};

function emptyState(scopeKey: string, generation: number): StudioState {
  return {
    scopeKey,
    generation,
    items: [],
    activeId: "",
    editsMediaId: "",
    edits: {},
    reviewMediaId: "",
    reviewNote: "",
    busy: false,
    error: "",
  };
}

/** Own the teacher workflow so stale API responses cannot restore another lesson or version. */
export function useLessonMediaStudio({
  chapterId,
  publicationId,
  lessons,
  selectedLessonId,
}: {
  chapterId: string;
  publicationId: string;
  lessons: MediaStudioLesson[];
  selectedLessonId?: string;
}) {
  const defaultLessonId = selectedLessonId ?? lessons[0]?.lessonId ?? "";
  const [lessonSelection, setLessonSelection] = useState({ defaultLessonId, selectedLessonId: defaultLessonId });
  const requestedLessonId = lessonSelection.defaultLessonId === defaultLessonId
    ? lessonSelection.selectedLessonId
    : defaultLessonId;
  const lessonId = lessons.some((item) => item.lessonId === requestedLessonId)
    ? requestedLessonId
    : lessons.some((item) => item.lessonId === defaultLessonId)
      ? defaultLessonId
      : lessons[0]?.lessonId ?? "";
  const lesson = lessons.find((item) => item.lessonId === lessonId);
  const scopeKey = JSON.stringify([chapterId, publicationId, lessonId]);

  const generationRef = useRef(0);
  const listRequestRef = useRef(0);
  const currentScopeRef = useRef(scopeKey);
  const selectionRef = useRef({ scopeKey, mediaId: "" });
  const [state, setState] = useState<StudioState>(() => emptyState("", 0));

  useLayoutEffect(() => {
    // Invalidate pending work before the new lesson/publication scope is painted.
    const generation = generationRef.current + 1;
    generationRef.current = generation;
    listRequestRef.current += 1;
    currentScopeRef.current = scopeKey;
    selectionRef.current = { scopeKey, mediaId: "" };
    setState(emptyState(scopeKey, generation));
  }, [scopeKey]);

  const token = (): ScopeToken => ({ scopeKey, generation: generationRef.current });
  const isCurrent = (candidate: ScopeToken) =>
    currentScopeRef.current === candidate.scopeKey && generationRef.current === candidate.generation;
  const update = (candidate: ScopeToken, transform: (current: StudioState) => StudioState) => {
    if (!isCurrent(candidate)) return;
    setState((current) => current.scopeKey === candidate.scopeKey && current.generation === candidate.generation
      ? transform(current)
      : current);
  };
  const visibleState = state.scopeKey === scopeKey && state.generation === generationRef.current
    ? state
    : emptyState(scopeKey, generationRef.current);
  const items = visibleState.items;
  const active = items.find((item) => item.mediaId === visibleState.activeId) ?? items[0] ?? null;

  useEffect(() => {
    if (lessonSelection.defaultLessonId !== defaultLessonId) {
      setLessonSelection({ defaultLessonId, selectedLessonId: defaultLessonId });
    }
  }, [defaultLessonId, lessonSelection.defaultLessonId]);

  useEffect(() => {
    if (!lessonId) return;
    const requestToken = { scopeKey, generation: generationRef.current };
    const requestId = ++listRequestRef.current;
    const controller = new AbortController();
    void listLessonMedia(chapterId, publicationId, lessonId, controller.signal).then((nextItems) => {
      if (controller.signal.aborted || !isCurrent(requestToken) || requestId !== listRequestRef.current) return;
      const selectedId = selectionRef.current.scopeKey === scopeKey ? selectionRef.current.mediaId : "";
      const nextActiveId = nextItems.some((item) => item.mediaId === selectedId) ? selectedId : nextItems[0]?.mediaId ?? "";
      selectionRef.current = { scopeKey, mediaId: nextActiveId };
      setState((current) => current.scopeKey === requestToken.scopeKey
        && current.generation === requestToken.generation
        && requestId === listRequestRef.current
        ? { ...current, items: nextItems, activeId: nextActiveId, error: "" }
        : current);
    }).catch((loadError: unknown) => {
      if (!controller.signal.aborted && isCurrent(requestToken) && requestId === listRequestRef.current) {
        setState((current) => current.scopeKey === requestToken.scopeKey
          && current.generation === requestToken.generation
          && requestId === listRequestRef.current
          ? { ...current, error: messageFor(loadError, "媒体列表加载失败") }
          : current);
      }
    });
    return () => controller.abort();
  }, [chapterId, lessonId, publicationId, scopeKey]);

  const refresh = async () => {
    if (!lessonId) return;
    const requestToken = token();
    const requestId = ++listRequestRef.current;
    try {
      const nextItems = await listLessonMedia(chapterId, publicationId, lessonId);
      if (!isCurrent(requestToken) || requestId !== listRequestRef.current) return;
      const selectedId = selectionRef.current.scopeKey === scopeKey ? selectionRef.current.mediaId : "";
      const nextActiveId = nextItems.some((item) => item.mediaId === selectedId)
        ? selectedId
        : nextItems[0]?.mediaId ?? "";
      selectionRef.current = { scopeKey, mediaId: nextActiveId };
      update(requestToken, (current) => ({
        ...current,
        items: nextItems,
        activeId: nextActiveId,
        edits: nextActiveId === current.activeId ? current.edits : {},
        editsMediaId: nextActiveId === current.activeId ? current.editsMediaId : "",
        reviewNote: nextActiveId === current.activeId ? current.reviewNote : "",
        reviewMediaId: nextActiveId === current.activeId ? current.reviewMediaId : "",
        error: "",
      }));
    } catch (loadError) {
      if (isCurrent(requestToken) && requestId === listRequestRef.current) {
        update(requestToken, (current) => ({ ...current, error: messageFor(loadError, "媒体列表加载失败") }));
      }
    }
  };

  const mutate = async (operation: () => Promise<string | void>) => {
    if (!lessonId || visibleState.scopeKey !== scopeKey || visibleState.generation !== generationRef.current) return;
    const requestToken = token();
    const activeAtStart = active?.mediaId ?? "";
    update(requestToken, (current) => ({ ...current, busy: true, error: "" }));
    try {
      const preferredMediaId = await operation();
      if (!isCurrent(requestToken)) return;
      const requestId = ++listRequestRef.current;
      const nextItems = await listLessonMedia(chapterId, publicationId, lessonId);
      if (!isCurrent(requestToken) || requestId !== listRequestRef.current) return;
      const selectedId = selectionRef.current.scopeKey === scopeKey ? selectionRef.current.mediaId : "";
      const selectionUnchanged = selectedId === activeAtStart;
      const nextActiveId = preferredMediaId && selectionUnchanged && nextItems.some((item) => item.mediaId === preferredMediaId)
        ? preferredMediaId
        : nextItems.some((item) => item.mediaId === selectedId)
          ? selectedId
          : nextItems[0]?.mediaId ?? "";
      selectionRef.current = { scopeKey, mediaId: nextActiveId };
      update(requestToken, (current) => ({
        ...current,
        items: nextItems,
        activeId: nextActiveId,
        edits: nextActiveId === current.activeId ? current.edits : {},
        editsMediaId: nextActiveId === current.activeId ? current.editsMediaId : "",
        reviewNote: nextActiveId === current.activeId ? current.reviewNote : "",
        reviewMediaId: nextActiveId === current.activeId ? current.reviewMediaId : "",
      }));
    } catch (actionError) {
      if (isCurrent(requestToken)) update(requestToken, (current) => ({ ...current, error: messageFor(actionError, "媒体操作失败") }));
    } finally {
      update(requestToken, (current) => ({ ...current, busy: false }));
    }
  };

  const createDraft = () => {
    if (!lesson) return;
    void mutate(async () => {
      const created = await createLessonMediaDraft({ chapterId, publicationId, lessonId, sourceRevisionId: lesson.sourceRevisionId });
      return created.mediaId;
    });
  };

  const changedScenes = active?.scenes
    .filter((scene) => visibleState.editsMediaId === active.mediaId
      && (visibleState.edits[scene.sceneId] ?? scene.narration) !== scene.narration)
    .map((scene) => ({ sceneId: scene.sceneId, narration: visibleState.edits[scene.sceneId] ?? scene.narration })) ?? [];

  const saveRevision = () => {
    if (!active || !changedScenes.length) return;
    const { mediaId, version } = active;
    const edits = changedScenes;
    void mutate(async () => (await createLessonMediaRevision(mediaId, version, edits)).mediaId);
  };

  const render = () => {
    if (!active) return;
    const { mediaId, version } = active;
    void mutate(() => enqueueLessonMediaRender(mediaId, version).then(() => undefined));
  };

  const cancelRender = () => {
    if (!active) return;
    const { mediaId } = active;
    void mutate(() => cancelLessonMediaRender(mediaId));
  };

  const decide = (decision: "approve" | "request_changes") => {
    if (!active) return;
    const { mediaId } = active;
    const note = visibleState.reviewMediaId === mediaId ? visibleState.reviewNote : "";
    void mutate(() => reviewLessonMedia(mediaId, decision, note).then(() => undefined));
  };

  const publish = () => {
    if (!active) return;
    const { mediaId } = active;
    void mutate(() => publishLessonMedia(mediaId).then(() => undefined));
  };

  const selectLesson = (nextLessonId: string) => {
    if (!lessons.some((item) => item.lessonId === nextLessonId)) return;
    setLessonSelection({ defaultLessonId, selectedLessonId: nextLessonId });
  };

  const selectMedia = (mediaId: string) => {
    if (!items.some((item) => item.mediaId === mediaId)) return;
    selectionRef.current = { scopeKey, mediaId };
    const requestToken = token();
    update(requestToken, (current) => ({
      ...current,
      activeId: mediaId,
      editsMediaId: "",
      edits: {},
      reviewMediaId: "",
      reviewNote: "",
    }));
  };

  const changeNarration = (sceneId: string, narration: string) => {
    if (!active) return;
    const mediaId = active.mediaId;
    const requestToken = token();
    update(requestToken, (current) => ({
      ...current,
      editsMediaId: mediaId,
      edits: { ...(current.editsMediaId === mediaId ? current.edits : {}), [sceneId]: narration },
    }));
  };

  const setReviewNote = (reviewNote: string) => {
    if (!active) return;
    const requestToken = token();
    update(requestToken, (current) => ({ ...current, reviewMediaId: active.mediaId, reviewNote }));
  };

  const currentJob = active?.renderJob;
  const rendering = currentJob?.status === "queued" || currentJob?.status === "running";
  const canRender = Boolean(active && ["draft", "consistency_failed", "changes_requested"].includes(active.status)
    && !(active.status === "consistency_failed" && active.consistencyReview));
  const previewable = Boolean(active && active.consistencyReview
    && ["consistency_review_required", "consistency_failed", "approved", "published"].includes(active.status));

  return {
    active,
    canRender,
    cancelRender,
    changeNarration,
    changedScenes,
    createDraft,
    decide,
    edits: active && visibleState.editsMediaId === active.mediaId ? visibleState.edits : {},
    error: visibleState.error,
    items,
    lesson,
    lessonId,
    previewable,
    publish,
    render,
    refresh,
    rendering,
    reviewNote: active && visibleState.reviewMediaId === active.mediaId ? visibleState.reviewNote : "",
    saveRevision,
    selectLesson,
    selectMedia,
    setReviewNote,
    busy: visibleState.busy,
  };
}

function messageFor(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback;
}
