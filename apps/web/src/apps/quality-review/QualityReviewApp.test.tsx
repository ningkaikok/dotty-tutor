// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, expect, it, vi } from "vitest";
import type { QualityReviewCase, QualityReviewInboxItem } from "../../api/qualityReviews";
import { QualityReviewApp } from "./QualityReviewApp";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

const reviewCase: QualityReviewCase = {
  caseId: "case-1", externalCaseId: "math-p214-example-2-2", packageId: "package-1", ordinal: 1,
  subject: "math", sourcePage: 214, sourceKind: "real_open_material", sourceFingerprint: "s".repeat(64),
  inputFingerprint: "i".repeat(64), outputFingerprint: "o".repeat(64), recordVersion: 1,
  reviewStatus: "pending_source_verification", sourceVerification: "pending_managed_asset",
  assetVerification: "pending_managed_asset", sourceVerifiedAt: null, sourceVerifiedHash: null,
  createdBy: "demo:unverified", createdByIdentityVerified: false, counted: false,
  source: { sourceTitle: "Elementary Algebra 2e", url: "https://openstax.org/details/books/elementary-algebra-2e", version: "2e", license: "CC BY 4.0", contentSha256: "a".repeat(64), assetVerification: "pending_managed_asset" },
  sourceLocator: { quote: "Solve the equation and check the result." }, input: { prompt: "EXAMPLE 2.2", question: "逐页核对题号、题体边界、公式负号、分数和条件。" },
  expected: { questionNumbers: ["EXAMPLE 2.2"] }, actual: { questionNumbers: ["13"], textSource: "pypdf_text_layer", limitations: "Pending human review." },
  revisions: [{ recordVersion: 1, sourceFingerprint: "s".repeat(64), inputFingerprint: "i".repeat(64), outputFingerprint: "o".repeat(64), input: { prompt: "EXAMPLE 2.2" }, expected: { questionNumbers: ["EXAMPLE 2.2"] }, actual: { questionNumbers: ["13"] }, createdBy: "demo:unverified", createdByIdentityVerified: false, createdAt: 1 }],
  decisions: [],
};
const secondCase: QualityReviewCase = { ...reviewCase, caseId: "case-2", externalCaseId: "english-p123-evidence-check", packageId: "package-2", subject: "english", sourcePage: 123,
  source: { sourceTitle: "Second source", assetVerification: "pending_managed_asset" }, input: { question: "What does the sentence imply?", passage: "A source sentence." },
  expected: { acceptedAnswers: ["It suggests a change."], evidenceQuote: "A source sentence." }, actual: { method: "domain.chapters.english.evaluate_english_chapter_attempt", productionEvaluation: { assessment: "needs_review", evidenceVerdict: "unsupported" } } };
const verifiedCase: QualityReviewCase = { ...reviewCase, reviewStatus: "pending_human_review", sourceVerification: "managed_asset_verified", assetVerification: "server_hash_verified",
  source: { ...reviewCase.source, assetVerification: "server_hash_verified" } };

function summary(detail: QualityReviewCase, sourceKey: string, chapterIds: string[] = []): QualityReviewInboxItem {
  return {
    caseId: detail.caseId, externalCaseId: detail.externalCaseId, packageId: detail.packageId, ordinal: detail.ordinal,
    subject: detail.subject, sourcePage: detail.sourcePage, sourceKind: detail.sourceKind, sourceFingerprint: detail.sourceFingerprint,
    inputFingerprint: detail.inputFingerprint, outputFingerprint: detail.outputFingerprint, recordVersion: detail.recordVersion,
    reviewStatus: detail.reviewStatus, sourceVerification: detail.sourceVerification, assetVerification: detail.assetVerification,
    counted: detail.counted, sourceKey, sourceTitle: String(detail.source.sourceTitle ?? "教材来源"), uploadId: null, chapterIds,
  };
}

const inbox = (items: QualityReviewInboxItem[]) => ({ items, total: items.length, pendingCount: items.filter((item) => !["human_accepted", "human_rejected"].includes(item.reviewStatus)).length,
  reviewedCount: items.filter((item) => ["human_accepted", "human_rejected"].includes(item.reviewStatus)).length, unlinkedCount: items.filter((item) => !item.chapterIds.length).length, mediaTasks: [], mediaPendingCount: 0 });
function response(payload: unknown, status = 200) { return new Response(JSON.stringify(payload), { status, headers: { "Content-Type": "application/json" } }); }
function mount(path: string) { return render(<MemoryRouter initialEntries={[path]}><QualityReviewApp /></MemoryRouter>); }

it("user Given an old quality-review deep link to a linked case When the page is refreshed Then its source, page, evidence, history and safe return route are restored", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (url) => {
    const path = String(url);
    if (path.startsWith("/api/quality-reviews/inbox")) return response(inbox([summary(verifiedCase, "sha256:abc", ["chapter-1"])]));
    if (path.endsWith("/cases/case-1")) return response(verifiedCase);
    throw new Error(`Unexpected API request: ${path}`);
  });
  mount("/studio/quality-review?packageId=package-1&caseId=case-1&chapterId=chapter-1&uploadId=upload-1&returnTo=%2Fstudio");
  expect(await screen.findByRole("heading", { name: reviewCase.externalCaseId })).toBeVisible();
  expect(screen.getByRole("img", { name: "教材 PDF 第 214 页" })).toHaveAttribute("src", "/api/quality-reviews/cases/case-1/source-page");
  expect(screen.getByText("Elementary Algebra 2e")).toBeVisible();
  expect(screen.getByRole("heading", { name: "预期结果" })).toBeVisible();
  expect(screen.getByText("应识别题号：EXAMPLE 2.2")).toBeVisible();
  expect(screen.getByText("识别到的题号：13")).toBeVisible();
  expect(screen.getAllByText("逐页核对题号、题体边界、公式负号、分数和条件。").length).toBeGreaterThan(0);
  expect(screen.getByText(/查看 1 个不可变版本/)).toBeVisible();
  expect(screen.getByRole("link", { name: /我的教材/ })).toHaveAttribute("href", "/studio");
});

it("user Given a legacy unlinked package and a return path When opening the old review route Then the case remains actionable without importing JSON", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (url) => {
    const path = String(url);
    if (path.startsWith("/api/quality-reviews/inbox")) return response(inbox([summary(reviewCase, "package:package-1")]));
    if (path.endsWith("/cases/case-1")) return response(reviewCase);
    throw new Error(`Unexpected API request: ${path}`);
  });
  mount("/studio/quality-review?packageId=package-1&caseId=case-1&returnTo=%2Fstudio");
  expect(await screen.findByRole("heading", { name: reviewCase.externalCaseId })).toBeVisible();
  expect(screen.getByRole("heading", { name: "教材来源复核" })).toBeVisible();
  expect(screen.getByRole("button", { name: "我的教材" })).toBeVisible();
  expect(screen.queryByText(/导入.*JSON/)).not.toBeInTheDocument();
  expect(screen.queryByLabelText(/模型/)).not.toBeInTheDocument();
});

it("user Given a source PDF is not verified When saving a positive opinion Then only a provisional source opinion is recorded", async () => {
  let stored = reviewCase;
  const requests: Array<{ url: string; init?: RequestInit }> = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (url, init) => {
    const path = String(url);
    requests.push({ url: path, init });
    if (path.startsWith("/api/quality-reviews/inbox")) return response(inbox([summary(stored, "package:package-1")]));
    if (path.endsWith("/cases/case-1/decisions")) {
      stored = { ...reviewCase, reviewStatus: "decision_recorded_pending_source", decisions: [{ reviewId: "review-1", reviewedRecordVersion: 1, stage: "provisional", actorId: "demo:unverified", actorKind: "demo_unverified", identityVerified: false, displayName: "", decision: "accepted", rationale: "原页未显示前的临时意见", verifiedQuote: "", claimedQuote: "", createdAt: 2 }] };
      return response(stored);
    }
    if (path.endsWith("/cases/case-1")) return response(stored);
    throw new Error(`Unexpected API request: ${path}`);
  });
  mount("/studio/quality-review?packageId=package-1&caseId=case-1&returnTo=%2Fstudio");
  await screen.findByRole("heading", { name: reviewCase.externalCaseId });
  fireEvent.change(screen.getByLabelText("复核理由"), { target: { value: "原页未显示前的临时意见" } });
  fireEvent.click(screen.getByRole("button", { name: "保存待核验意见" }));
  expect(await screen.findByText(/已保存为待核验记录/)).toBeVisible();
  expect(screen.getByText(/不会自动批准课程内容或教学视频/)).toBeVisible();
  const post = requests.find((item) => item.url.endsWith("/cases/case-1/decisions"));
  expect(JSON.parse(String(post?.init?.body))).toMatchObject({ expectedRecordVersion: 1, decision: "accepted", rationale: "原页未显示前的临时意见" });
  expect(screen.getByText(/未验证身份不表示独立双审/)).toBeVisible();
});

it("user Given a package deep link is waiting for its case When loading completes Then one stable review detail appears without retry churn", async () => {
  let releaseDetail: ((value: Response) => void) | undefined;
  let started = false;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (url) => {
    const path = String(url);
    if (path.startsWith("/api/quality-reviews/inbox")) return response(inbox([summary(reviewCase, "package:package-1")]));
    if (path.endsWith("/cases/case-1")) {
      if (started) return response({ detail: "重复读取不应发生" }, 409);
      started = true;
      return new Promise<Response>((resolve) => { releaseDetail = resolve; });
    }
    throw new Error(`Unexpected API request: ${path}`);
  });
  mount("/studio/quality-review?packageId=package-1&caseId=case-1");
  expect(await screen.findByText("正在读取所选案例…")).toBeVisible();
  await act(async () => {
    releaseDetail?.(response(reviewCase));
    await Promise.resolve();
  });
  expect(await screen.findByRole("heading", { name: reviewCase.externalCaseId })).toBeVisible();
  expect(screen.queryByText("重复读取不应发生")).not.toBeInTheDocument();
});

it("user Given a requested case detail fails When the teacher chooses retry Then the case can be reopened without automatic retries", async () => {
  let failed = false;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (url) => {
    const path = String(url);
    if (path.startsWith("/api/quality-reviews/inbox")) return response(inbox([summary(reviewCase, "package:package-1")]));
    if (path.endsWith("/cases/case-1")) {
      if (!failed) { failed = true; return response({ detail: "案例服务暂时不可用" }, 503); }
      return response(reviewCase);
    }
    throw new Error(`Unexpected API request: ${path}`);
  });
  mount("/studio/quality-review?packageId=package-1&caseId=case-1");
  expect(await screen.findByRole("alert")).toHaveTextContent("案例服务暂时不可用");
  expect(screen.queryByRole("heading", { name: reviewCase.externalCaseId })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "重试打开案例" }));
  expect(await screen.findByRole("heading", { name: reviewCase.externalCaseId })).toBeVisible();
});

it("user Given an English rule replay When opening the actual observation Then its method is explained in readable language", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (url) => {
    const path = String(url);
    if (path.startsWith("/api/quality-reviews/inbox")) return response(inbox([summary(secondCase, "package:package-2")]));
    if (path.endsWith("/cases/case-2")) return response(secondCase);
    throw new Error(`Unexpected API request: ${path}`);
  });
  mount("/studio/quality-review?packageId=package-2&caseId=case-2");
  expect(await screen.findByRole("heading", { name: secondCase.externalCaseId })).toBeVisible();
  expect(screen.getByText("执行方式：英语答案与证据规则回放")).toBeVisible();
});
