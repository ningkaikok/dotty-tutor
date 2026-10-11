import { parse } from "./client";

export interface QualityReviewPackage {
  packageId: string;
  title: string;
  sourceKind: string;
  source: Record<string, unknown>;
  createdAt: number;
  cases?: number;
  pending?: number;
  reviewed?: number;
}

export interface QualityReviewCaseSummary {
  caseId: string;
  externalCaseId: string;
  packageId: string;
  ordinal: number;
  subject: string;
  sourcePage: number;
  sourceKind: string;
  sourceFingerprint: string;
  inputFingerprint: string;
  outputFingerprint: string;
  recordVersion: number;
  reviewStatus: string;
  sourceVerification: string;
  assetVerification: string;
  counted: boolean;
}

export interface QualityReviewInboxItem extends QualityReviewCaseSummary {
  sourceKey: string;
  sourceTitle: string;
  uploadId: string | null;
  chapterIds: string[];
}

export interface QualityReviewInboxMediaTask {
  mediaId: string;
  chapterId: string;
  publicationId: string;
  lessonId: string;
  version: number;
  title: string;
  status: string;
  sourceKey: string;
  uploadId: string | null;
  sourceRevisionId: string;
}

export interface QualityReviewInbox {
  items: QualityReviewInboxItem[];
  total: number;
  pendingCount: number;
  reviewedCount: number;
  unlinkedCount: number;
  mediaTasks: QualityReviewInboxMediaTask[];
  mediaPendingCount: number;
}

export interface QualityReviewInboxFilters {
  chapterId?: string;
  uploadId?: string;
  includeReviewed?: boolean;
}

export interface QualityReviewDecision {
  reviewId: string;
  reviewedRecordVersion: number;
  stage: "provisional" | "final";
  actorId: string;
  actorKind: string;
  identityVerified: boolean;
  displayName: string;
  decision: "accepted" | "rejected" | "needs_changes";
  rationale: string;
  verifiedQuote: string;
  claimedQuote: string;
  createdAt: number;
}

export interface QualityReviewRevision {
  recordVersion: number;
  sourceFingerprint: string;
  inputFingerprint: string;
  outputFingerprint: string;
  input: Record<string, unknown>;
  expected: Record<string, unknown>;
  actual: Record<string, unknown>;
  createdBy: string;
  createdByIdentityVerified: boolean;
  createdAt: number;
}

export interface QualityReviewCase extends QualityReviewCaseSummary {
  source: Record<string, unknown>;
  sourceLocator: Record<string, unknown>;
  input: Record<string, unknown>;
  expected: Record<string, unknown>;
  actual: Record<string, unknown>;
  createdBy: string;
  createdByIdentityVerified: boolean;
  sourceVerifiedAt: number | null;
  sourceVerifiedHash: string | null;
  revisions: QualityReviewRevision[];
  decisions: QualityReviewDecision[];
}

export async function listQualityReviewPackages(): Promise<QualityReviewPackage[]> {
  const payload = await parse<{ items?: QualityReviewPackage[] }>(await fetch("/api/quality-reviews/packages", { cache: "no-store" }));
  return Array.isArray(payload.items) ? payload.items : [];
}

/** Read server-linked review cases; chapter and upload filters are combined by the server. */
export async function listQualityReviewInbox(
  filters: QualityReviewInboxFilters = {},
  signal?: AbortSignal,
): Promise<QualityReviewInbox> {
  const query = new URLSearchParams();
  if (filters.chapterId) query.set("chapterId", filters.chapterId);
  if (filters.uploadId) query.set("uploadId", filters.uploadId);
  if (filters.includeReviewed) query.set("includeReviewed", "true");
  const suffix = query.size ? `?${query.toString()}` : "";
  const result = await parse<QualityReviewInbox>(await fetch(`/api/quality-reviews/inbox${suffix}`, {
    cache: "no-store",
    signal,
  }));
  if (!Array.isArray(result.items) || !Array.isArray(result.mediaTasks)
    || ![result.total, result.pendingCount, result.reviewedCount, result.unlinkedCount, result.mediaPendingCount].every(Number.isFinite)) {
    throw new Error("来源复核列表格式无效，请重试。");
  }
  return result;
}

export async function listQualityReviewCases(packageId: string): Promise<QualityReviewCaseSummary[]> {
  const payload = await parse<{ items?: QualityReviewCaseSummary[] }>(await fetch(`/api/quality-reviews/packages/${encodeURIComponent(packageId)}/cases`, { cache: "no-store" }));
  return Array.isArray(payload.items) ? payload.items : [];
}

export async function getQualityReviewCase(caseId: string): Promise<QualityReviewCase> {
  return parse<QualityReviewCase>(await fetch(`/api/quality-reviews/cases/${encodeURIComponent(caseId)}`, { cache: "no-store" }));
}

/** Import a prepared real_sources manifest without ever sending its server-side file paths. */
export async function importQualityReviewManifest(input: unknown): Promise<QualityReviewPackage> {
  if (!isRecord(input) || !Array.isArray(input.cases) || input.cases.length < 1 || input.cases.length > 250) {
    throw new Error("请选择包含来源、原文定位和预期结果的有效 JSON 文件");
  }
  const cases = input.cases.map((candidate, index) => {
    if (!isRecord(candidate) || !isRecord(candidate.source) || !isRecord(candidate.expected) || !isRecord(candidate.sourceLocator)) {
      throw new Error(`第 ${index + 1} 个案例缺少来源、定位或预期内容`);
    }
    const source = { ...candidate.source };
    delete source.file;
    delete source.path;
    delete source.sourceFilePath;
    delete source.sourceKind;
    delete source.counted;
    delete source.assetVerification;
    return {
      id: candidate.id,
      subject: candidate.subject,
      sourcePage: candidate.sourcePage,
      source,
      sourceLocator: candidate.sourceLocator,
      input: isRecord(candidate.input) ? candidate.input : {},
      expected: candidate.expected,
      actual: isRecord(candidate.actual) ? candidate.actual : {},
    };
  });
  const payload = {
    title: typeof input.title === "string" ? input.title : "开放教材质量复核",
    source: {
      manifestSha256: typeof input.manifestSha256 === "string" ? input.manifestSha256 : "",
      preparationReport: isRecord(input.report) ? input.report : {},
    },
    cases,
  };
  return parse<QualityReviewPackage>(await fetch("/api/quality-reviews/packages", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  }));
}

export async function recordQualityReviewDecision(
  caseId: string,
  input: { expectedRecordVersion: number; decision: QualityReviewDecision["decision"]; rationale: string; verifiedQuote: string; displayName: string },
): Promise<QualityReviewCase> {
  return parse<QualityReviewCase>(await fetch(`/api/quality-reviews/cases/${encodeURIComponent(caseId)}/decisions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  }));
}

export async function reviseQualityReviewCase(
  caseId: string,
  input: { expectedRecordVersion: number; input: Record<string, unknown>; expected: Record<string, unknown>; actual: Record<string, unknown> },
): Promise<QualityReviewCase> {
  return parse<QualityReviewCase>(await fetch(`/api/quality-reviews/cases/${encodeURIComponent(caseId)}/revisions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  }));
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}
