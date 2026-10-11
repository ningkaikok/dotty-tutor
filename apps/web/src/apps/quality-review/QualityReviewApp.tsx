import { useCallback } from "react";
import { useNavigate, useSearchParams } from "react-router";
import type { QualityReviewInboxItem } from "../../api/qualityReviews";
import { MaterialsHeader } from "../materials/MaterialsHeader";
import { CaseReviewPanel } from "./CaseReviewPanel";
import "./qualityReview.css";

/** Keep old source-review URLs usable while the library owns the global review entry point. */
export function QualityReviewApp() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const packageId = searchParams.get("packageId") ?? undefined;
  const caseId = searchParams.get("caseId") ?? undefined;
  const chapterId = searchParams.get("chapterId") ?? undefined;
  const uploadId = searchParams.get("uploadId") ?? undefined;
  const returnTo = validReturnPath(searchParams.get("returnTo"));
  const onCaseSelected = useCallback((item: QualityReviewInboxItem) => {
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      next.set("packageId", item.packageId);
      next.set("caseId", item.caseId);
      if (chapterId) next.set("chapterId", chapterId);
      if (uploadId) next.set("uploadId", uploadId);
      return next;
    }, { replace: true });
  }, [chapterId, setSearchParams, uploadId]);

  return <main className="quality-review-shell">
    <MaterialsHeader backTo={returnTo} backLabel={returnTo === "/studio" ? "我的教材" : "返回来源上下文"} subtitle="教材来源复核" />
    <section className="quality-review-heading"><span className="eyebrow">来源、版本与人工决定</span><h1>教材来源复核</h1>
      <p>从“我的教材”继续待审来源，或打开旧复核链接。决定会保留在来源案例中，不会自动批准课程或教学视频。</p>
    </section>
    <CaseReviewPanel packageId={packageId} caseId={caseId} chapterId={chapterId} uploadId={uploadId}
      onCaseSelected={onCaseSelected} onReturn={() => navigate(returnTo)} returnLabel="我的教材" showReturn />
  </main>;
}

function validReturnPath(value: string | null): string {
  if (!value || !value.startsWith("/studio") || value.startsWith("//") || value.includes("\\")) return "/studio";
  return value;
}
