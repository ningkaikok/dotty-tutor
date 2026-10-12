import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import {
  getQualityReviewCase,
  recordQualityReviewDecision,
  reviseQualityReviewCase,
  type QualityReviewCase,
  type QualityReviewInboxItem,
} from "../../api/qualityReviews";
import { useQualityReviewInbox } from "./useQualityReviewInbox";

type Decision = "accepted" | "rejected" | "needs_changes";
type Context = {
  packageId?: string;
  caseId?: string;
  chapterId?: string;
  uploadId?: string;
  onCaseSelected?: (item: QualityReviewInboxItem) => void;
};

type ReviewState = {
  contextKey: string;
  selectedCaseId: string;
  detail: QualityReviewCase | null;
  displayName: string;
  decision: Decision;
  rationale: string;
  verifiedQuote: string;
  busy: boolean;
  transitioning: boolean;
  error: string;
  notice: string;
};

function emptyState(contextKey: string): ReviewState {
  return {
    contextKey, selectedCaseId: "", detail: null, displayName: "", decision: "accepted",
    rationale: "", verifiedQuote: "", busy: false, transitioning: false, error: "", notice: "",
  };
}

/** Share case navigation and review mutations across the library, chapter workspace, and legacy route. */
export function useQualityReview({ packageId, caseId, chapterId, uploadId, onCaseSelected }: Context = {}) {
  const contextKey = JSON.stringify([chapterId ?? "", uploadId ?? "", packageId ?? ""]);
  const inbox = useQualityReviewInbox({
    ...(chapterId ? { chapterId } : {}),
    ...(uploadId ? { uploadId } : {}),
    includeReviewed: true,
  });
  const refreshInbox = inbox.refresh;
  const cases = useMemo(() => packageId ? inbox.items.filter((item) => item.packageId === packageId) : inbox.items, [inbox.items, packageId]);
  const contextRef = useRef(contextKey);
  const requestSequence = useRef(0);
  const autoOpenedRef = useRef("");
  const [state, setState] = useState<ReviewState>(() => emptyState(""));
  const visible = state.contextKey === contextKey ? state : emptyState(contextKey);

  useLayoutEffect(() => {
    contextRef.current = contextKey;
    requestSequence.current += 1;
    setState(emptyState(contextKey));
  }, [contextKey]);

  const update = useCallback((key: string, transform: (current: ReviewState) => ReviewState) => {
    if (contextRef.current !== key) return;
    setState((current) => current.contextKey === key ? transform(current) : current);
  }, []);

  const openCase = useCallback(async (nextCaseId: string) => {
    const item = cases.find((candidate) => candidate.caseId === nextCaseId);
    if (!item) return false;
    const key = contextKey;
    const request = ++requestSequence.current;
    onCaseSelected?.(item);
    update(key, (current) => ({
      ...current, selectedCaseId: nextCaseId, detail: null, rationale: "", verifiedQuote: "",
      busy: false, transitioning: true, error: "", notice: "",
    }));
    try {
      const detail = await getQualityReviewCase(nextCaseId);
      if (requestSequence.current === request && contextRef.current === key) {
        update(key, (current) => ({ ...current, detail, transitioning: false }));
      }
      return true;
    } catch (requestError) {
      if (requestSequence.current === request && contextRef.current === key) {
        update(key, (current) => ({
          ...current, transitioning: false,
          error: requestError instanceof Error ? requestError.message : "案例读取失败",
        }));
      }
      return false;
    }
  }, [cases, contextKey, onCaseSelected, update]);

  useEffect(() => {
    if (inbox.loading || inbox.error) return;
    const preferredCaseId = caseId || visible.selectedCaseId ||
      cases.find((item) => isPendingStatus(item.reviewStatus))?.caseId || cases[0]?.caseId || "";
    if (!preferredCaseId) return;
    if (preferredCaseId === visible.selectedCaseId && visible.detail?.caseId === preferredCaseId) return;
    if (visible.transitioning && visible.selectedCaseId === preferredCaseId) return;
    const automaticSelection = `${contextKey}:${preferredCaseId}`;
    if (automaticSelection === autoOpenedRef.current) return;
    if (cases.some((item) => item.caseId === preferredCaseId)) {
      autoOpenedRef.current = automaticSelection;
      void openCase(preferredCaseId);
    }
  }, [caseId, cases, contextKey, inbox.error, inbox.loading, openCase, visible.detail?.caseId, visible.selectedCaseId, visible.transitioning]);

  const saveDecision = useCallback(async () => {
    const detail = visible.detail;
    if (!detail || !visible.rationale.trim() || visible.transitioning || visible.busy) return false;
    const key = contextKey;
    const selectedId = detail.caseId;
    const request = requestSequence.current;
    update(key, (current) => ({ ...current, busy: true, error: "", notice: "" }));
    try {
      const saved = await recordQualityReviewDecision(selectedId, {
        expectedRecordVersion: detail.recordVersion,
        decision: visible.decision,
        rationale: visible.rationale.trim(),
        verifiedQuote: visible.verifiedQuote.trim(),
        displayName: visible.displayName.trim(),
      });
      if (requestSequence.current === request && contextRef.current === key) {
        update(key, (current) => ({
          ...current, detail: saved, rationale: "", verifiedQuote: "",
          notice: saved.decisions[saved.decisions.length - 1]?.stage === "final"
            ? "教材决定已保存，并绑定到已核验的原页和当前案例版本。"
            : "意见已保存为待核验记录。连接并核验原教材 PDF 后，才能提交最终教材决定。",
        }));
        await refreshInbox();
      }
      return true;
    } catch (requestError) {
      if (requestSequence.current === request && contextRef.current === key) {
        update(key, (current) => ({ ...current, error: requestError instanceof Error ? requestError.message : "决定保存失败" }));
      }
      return false;
    } finally {
      if (requestSequence.current === request && contextRef.current === key) {
        update(key, (current) => ({ ...current, busy: false }));
      }
    }
  }, [contextKey, refreshInbox, update, visible]);

  const reviseCase = useCallback(async (snapshot: { input: Record<string, unknown>; expected: Record<string, unknown>; actual: Record<string, unknown> }) => {
    const detail = visible.detail;
    if (!detail || visible.transitioning || visible.busy) return false;
    const key = contextKey;
    const selectedId = detail.caseId;
    const request = requestSequence.current;
    update(key, (current) => ({ ...current, busy: true, error: "", notice: "" }));
    try {
      const revised = await reviseQualityReviewCase(selectedId, {
        expectedRecordVersion: detail.recordVersion,
        ...snapshot,
      });
      if (requestSequence.current === request && contextRef.current === key) {
        update(key, (current) => ({
          ...current, detail: revised,
          notice: `已创建案例版本 ${revised.recordVersion}。旧决定仍保留在原版本，当前版本需重新复核。`,
        }));
        await refreshInbox();
      }
      return true;
    } catch (requestError) {
      if (requestSequence.current === request && contextRef.current === key) {
        update(key, (current) => ({ ...current, error: requestError instanceof Error ? requestError.message : "案例修订失败" }));
      }
      return false;
    } finally {
      if (requestSequence.current === request && contextRef.current === key) {
        update(key, (current) => ({ ...current, busy: false }));
      }
    }
  }, [contextKey, refreshInbox, update, visible]);

  return {
    cases,
    requestedCaseMissing: Boolean(caseId && !inbox.loading && !inbox.error && !cases.some((item) => item.caseId === caseId)),
    counts: inbox.counts,
    hasCounts: inbox.hasCounts,
    inboxLoading: inbox.loading,
    inboxError: inbox.error,
    refresh: inbox.refresh,
    selectedCaseId: visible.selectedCaseId,
    detail: caseId && visible.detail?.caseId !== caseId ? null : visible.detail,
    displayName: visible.displayName,
    setDisplayName: (displayName: string) => update(contextKey, (current) => ({ ...current, displayName })),
    decision: visible.decision,
    setDecision: (decision: Decision) => update(contextKey, (current) => ({ ...current, decision })),
    rationale: visible.rationale,
    setRationale: (rationale: string) => update(contextKey, (current) => ({ ...current, rationale })),
    verifiedQuote: visible.verifiedQuote,
    setVerifiedQuote: (verifiedQuote: string) => update(contextKey, (current) => ({ ...current, verifiedQuote })),
    busy: visible.busy,
    transitioning: visible.transitioning,
    error: visible.error,
    setError: (error: string) => update(contextKey, (current) => ({ ...current, error })),
    notice: visible.notice,
    openCase,
    saveDecision,
    reviseCase,
  };
}

function isPendingStatus(status: string): boolean {
  return !["human_accepted", "human_rejected"].includes(status);
}
