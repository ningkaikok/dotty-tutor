import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import {
  listQualityReviewInbox,
  type QualityReviewInbox,
  type QualityReviewInboxFilters,
} from "../../api/qualityReviews";

type InboxState = {
  key: string;
  result: QualityReviewInbox | null;
  loading: boolean;
  error: string;
};

type InboxOptions = QualityReviewInboxFilters & { enabled?: boolean };

const EMPTY_RESULT: QualityReviewInbox = {
  items: [], total: 0, pendingCount: 0, reviewedCount: 0, unlinkedCount: 0,
  mediaTasks: [], mediaPendingCount: 0,
};

/** Load a scoped inbox without letting late responses replace another source context. */
export function useQualityReviewInbox(filters: InboxOptions = {}) {
  const chapterId = filters.chapterId ?? "";
  const uploadId = filters.uploadId ?? "";
  const includeReviewed = filters.includeReviewed ?? false;
  const enabled = filters.enabled ?? true;
  const key = JSON.stringify([chapterId, uploadId, includeReviewed, enabled]);
  const sequence = useRef(0);
  const activeKey = useRef(key);
  const [state, setState] = useState<InboxState>({ key: "", result: null, loading: true, error: "" });

  useLayoutEffect(() => {
    activeKey.current = key;
  }, [key]);

  const refresh = useCallback(async () => {
    const request = ++sequence.current;
    const requestKey = key;
    const controller = new AbortController();
    const requestFilters = {
      ...(chapterId ? { chapterId } : {}),
      ...(uploadId ? { uploadId } : {}),
      includeReviewed,
    };
    setState((current) => current.key === requestKey
      ? { ...current, loading: true, error: "" }
      : { key: requestKey, result: null, loading: true, error: "" });
    try {
      const result = await listQualityReviewInbox(requestFilters, controller.signal);
      if (sequence.current === request && activeKey.current === requestKey) {
        setState({ key: requestKey, result, loading: false, error: "" });
      }
    } catch (requestError) {
      if (sequence.current === request && activeKey.current === requestKey) {
        setState((current) => ({
          key: requestKey,
          result: current.key === requestKey ? current.result : null,
          loading: false,
          error: requestError instanceof Error ? requestError.message : "来源复核列表读取失败，请重试。",
        }));
      }
    }
  }, [chapterId, includeReviewed, key, uploadId]);

  useEffect(() => {
    if (!enabled) {
      sequence.current += 1;
      setState({ key, result: null, loading: false, error: "" });
      return;
    }
    void refresh();
    return () => { sequence.current += 1; };
  }, [enabled, key, refresh]);

  const visible = state.key === key ? state : { key, result: null, loading: enabled, error: "" };
  return {
    items: visible.result?.items ?? [],
    counts: visible.result ?? EMPTY_RESULT,
    hasCounts: visible.result !== null,
    loading: visible.loading,
    error: visible.error,
    refresh,
  };
}
