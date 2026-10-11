import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { loadPublishedChapter } from "../../api/chapters";
import type { PublishedChapter } from "../../types/chapter";

type SnapshotState = { key: string; snapshot: PublishedChapter | null; loading: boolean; error: string };

/** Read media source lessons from the exact immutable publication chosen by the teacher. */
export function useChapterPublishedMedia(chapterId: string, publicationId: string | null) {
  const key = JSON.stringify([chapterId, publicationId ?? ""]);
  const generation = useRef(0);
  const activeKey = useRef(key);
  const [state, setState] = useState<SnapshotState>({ key: "", snapshot: null, loading: false, error: "" });

  useLayoutEffect(() => {
    activeKey.current = key;
    generation.current += 1;
    setState({ key, snapshot: null, loading: Boolean(publicationId), error: "" });
  }, [key, publicationId]);

  useEffect(() => {
    if (!publicationId) return;
    const requestGeneration = generation.current;
    const requestKey = key;
    const controller = new AbortController();
    void loadPublishedChapter(chapterId, publicationId, controller.signal).then((snapshot) => {
      if (!controller.signal.aborted && activeKey.current === requestKey && generation.current === requestGeneration) {
        setState({ key: requestKey, snapshot, loading: false, error: "" });
      }
    }).catch((requestError: unknown) => {
      if (!controller.signal.aborted && activeKey.current === requestKey && generation.current === requestGeneration) {
        setState({ key: requestKey, snapshot: null, loading: false, error: requestError instanceof Error ? requestError.message : "已发布课程快照读取失败" });
      }
    });
    return () => controller.abort();
  }, [chapterId, key, publicationId]);

  const visible = state.key === key ? state : { key, snapshot: null, loading: Boolean(publicationId), error: "" };
  return { snapshot: visible.snapshot, loading: visible.loading, error: visible.error };
}
