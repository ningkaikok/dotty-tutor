import { useCallback, useEffect, useRef, useState } from "react";
import {
  getQualityReviewCase,
  importQualityReviewManifest,
  listQualityReviewCases,
  listQualityReviewPackages,
  recordQualityReviewDecision,
  reviseQualityReviewCase,
  type QualityReviewCase,
  type QualityReviewCaseSummary,
  type QualityReviewPackage,
} from "../../api/qualityReviews";

type Decision = "accepted" | "rejected" | "needs_changes";

/** Own package/case navigation and API transitions so stale requests cannot restore old review state. */
export function useQualityReview() {
  const [packages, setPackages] = useState<QualityReviewPackage[]>([]);
  const [selectedPackageId, setSelectedPackageId] = useState("");
  const [cases, setCases] = useState<QualityReviewCaseSummary[]>([]);
  const [selectedCaseId, setSelectedCaseId] = useState("");
  const [detail, setDetail] = useState<QualityReviewCase | null>(null);
  const [displayName, setDisplayName] = useState("");
  const [decision, setDecision] = useState<Decision>("accepted");
  const [rationale, setRationale] = useState("");
  const [verifiedQuote, setVerifiedQuote] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [transitioning, setTransitioning] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const selection = useRef({ packageId: "", caseId: "" });
  const selectionRevision = useRef(0);
  const packageRequest = useRef(0);

  const clearDraft = useCallback(() => {
    setRationale("");
    setVerifiedQuote("");
    setNotice("");
  }, []);

  const loadCase = useCallback(async (caseId: string, revision: number) => {
    const current = await getQualityReviewCase(caseId);
    if (selectionRevision.current === revision && selection.current.caseId === caseId) setDetail(current);
  }, []);

  const selectPackage = useCallback(async (packageId: string) => {
    const revision = ++selectionRevision.current;
    selection.current = { packageId, caseId: "" };
    setSelectedPackageId(packageId);
    setCases([]);
    setSelectedCaseId("");
    setDetail(null);
    clearDraft();
    setError("");
    setTransitioning(true);
    if (!packageId) {
      setTransitioning(false);
      return;
    }
    try {
      const rows = await listQualityReviewCases(packageId);
      if (selectionRevision.current !== revision || selection.current.packageId !== packageId) return;
      setCases(rows);
      const nextCaseId = rows[0]?.caseId ?? "";
      selection.current = { packageId, caseId: nextCaseId };
      setSelectedCaseId(nextCaseId);
      if (nextCaseId) await loadCase(nextCaseId, revision);
    } catch (cause) {
      if (selectionRevision.current === revision) setError(cause instanceof Error ? cause.message : "案例列表读取失败");
    } finally {
      if (selectionRevision.current === revision) setTransitioning(false);
    }
  }, [clearDraft, loadCase]);

  const refreshPackages = useCallback(async (preferredId?: string) => {
    const request = ++packageRequest.current;
    const revisionAtStart = selectionRevision.current;
    setError("");
    try {
      const items = await listQualityReviewPackages();
      if (packageRequest.current !== request) return;
      setPackages(items);
      // A package refresh may finish after the reviewer has navigated. Keep the new list,
      // but never let that stale snapshot select a package or case for them.
      if (selectionRevision.current !== revisionAtStart) return;
      const currentId = preferredId ?? selection.current.packageId;
      const nextId = items.some((item) => item.packageId === currentId) ? currentId : items[0]?.packageId ?? "";
      await selectPackage(nextId);
    } catch (cause) {
      if (packageRequest.current === request) setError(cause instanceof Error ? cause.message : "复核包读取失败");
    } finally {
      if (packageRequest.current === request) setLoading(false);
    }
  }, [selectPackage]);

  const openCase = useCallback(async (caseId: string) => {
    const revision = ++selectionRevision.current;
    selection.current = { ...selection.current, caseId };
    setSelectedCaseId(caseId);
    setDetail(null);
    clearDraft();
    setError("");
    setTransitioning(true);
    try {
      await loadCase(caseId, revision);
    } catch (cause) {
      if (selectionRevision.current === revision) setError(cause instanceof Error ? cause.message : "案例读取失败");
    } finally {
      if (selectionRevision.current === revision) setTransitioning(false);
    }
  }, [clearDraft, loadCase]);

  const refreshPackageSummaries = useCallback(async (revision: number) => {
    const items = await listQualityReviewPackages();
    if (selectionRevision.current === revision) setPackages(items);
  }, []);

  const importPacket = useCallback(async (manifest: unknown) => {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const created = await importQualityReviewManifest(manifest);
      await refreshPackages(created.packageId);
      const verified = created.source.assetVerification === "server_hash_verified";
      setNotice(verified
        ? `已导入“${created.title}”。受管教材 PDF 指纹和每案原文定位已由服务端核验；所有案例仍待你逐案人工复核。`
        : `已导入“${created.title}”。部分或全部教材原页尚未完成受管核验；未核验前只能保存临时意见。`);
      return true;
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "导入失败");
      return false;
    } finally {
      setBusy(false);
    }
  }, [refreshPackages]);

  const saveDecision = useCallback(async () => {
    if (!detail || !rationale.trim() || transitioning) return false;
    const caseId = detail.caseId;
    const revision = selectionRevision.current;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const saved = await recordQualityReviewDecision(caseId, {
        expectedRecordVersion: detail.recordVersion,
        decision,
        rationale: rationale.trim(),
        verifiedQuote: verifiedQuote.trim(),
        displayName: displayName.trim(),
      });
      if (selectionRevision.current === revision && selection.current.caseId === caseId) {
        setDetail(saved);
        clearDraft();
        setNotice(saved.decisions[saved.decisions.length - 1]?.stage === "final"
          ? "教材决定已保存，并绑定到已核验的原页和当前案例版本。"
          : "意见已保存为待核验记录。连接并核验原教材 PDF 后，才能提交最终教材决定。");
      }
      await refreshPackageSummaries(revision);
      return true;
    } catch (cause) {
      if (selectionRevision.current === revision) setError(cause instanceof Error ? cause.message : "决定保存失败");
      return false;
    } finally {
      setBusy(false);
    }
  }, [clearDraft, decision, detail, displayName, rationale, refreshPackageSummaries, transitioning, verifiedQuote]);

  const reviseCase = useCallback(async (snapshot: { input: Record<string, unknown>; expected: Record<string, unknown>; actual: Record<string, unknown> }) => {
    if (!detail || transitioning || busy) return false;
    const caseId = detail.caseId;
    const revision = selectionRevision.current;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const revised = await reviseQualityReviewCase(caseId, {
        expectedRecordVersion: detail.recordVersion,
        ...snapshot,
      });
      if (selectionRevision.current === revision && selection.current.caseId === caseId) {
        setDetail(revised);
        clearDraft();
        setNotice(`已创建案例版本 ${revised.recordVersion}。旧决定仍保留在原版本，当前版本需重新复核。`);
      }
      await refreshPackageSummaries(revision);
      return true;
    } catch (cause) {
      if (selectionRevision.current === revision) setError(cause instanceof Error ? cause.message : "案例修订失败");
      return false;
    } finally {
      setBusy(false);
    }
  }, [busy, clearDraft, detail, refreshPackageSummaries, transitioning]);

  useEffect(() => {
    void refreshPackages();
  }, [refreshPackages]);

  return {
    packages, selectedPackageId, cases, selectedCaseId, detail,
    displayName, setDisplayName, decision, setDecision, rationale, setRationale,
    verifiedQuote, setVerifiedQuote, busy, loading, transitioning, error, setError, notice,
    refreshPackages, selectPackage, openCase, importPacket, saveDecision, reviseCase,
  };
}
