import { useEffect, useRef, useState } from "react";
import { explainCoursewareSource, searchCoursewareSource, type CoursewareSourceEvidence } from "../../api/coursewareSearch";

type SearchState = { key: string; busy: boolean; items: CoursewareSourceEvidence[]; explanation: string; error: string; searched: boolean };

/** Abort old publication requests and hide their evidence immediately on navigation. */
export function useCoursewareSourceSearch(coursewareId: string, publicationId: string, actorScope = "") {
  const key = JSON.stringify([coursewareId, publicationId, actorScope]);
  const active = useRef<AbortController | null>(null);
  const [state, setState] = useState<SearchState>({ key, busy: false, items: [], explanation: "", error: "", searched: false });
  useEffect(() => () => active.current?.abort(), [key]);

  async function submit(query: string, explain: boolean) {
    if (!query.trim()) return;
    active.current?.abort();
    const controller = new AbortController();
    active.current = controller;
    setState({ key, busy: true, items: [], explanation: "", error: "", searched: true });
    try {
      const response = explain
        ? await explainCoursewareSource(coursewareId, publicationId, query.trim(), controller.signal)
        : await searchCoursewareSource(coursewareId, publicationId, query.trim(), controller.signal);
      if (!controller.signal.aborted) setState({ key, busy: false, items: "items" in response ? response.items : response.evidence,
        explanation: "explanation" in response ? response.explanation : "", error: "", searched: true });
    } catch (error) {
      if (!controller.signal.aborted) setState({ key, busy: false, items: [], explanation: "", searched: true,
        error: error instanceof Error ? error.message : "教材检索失败，请重试" });
    }
  }

  return { ...(state.key === key ? state : { key, busy: false, items: [], explanation: "", error: "", searched: false }), submit };
}
