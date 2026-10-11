// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, expect, it, vi } from "vitest";
import type { QualityReviewCase, QualityReviewInbox } from "../../api/qualityReviews";
import type { PublishedChapter } from "../../types/chapter";
import { ChapterStudioApp } from "./ChapterStudioApp";

const mockHooks = vi.hoisted(() => ({ useChapterStudio: vi.fn(), useChapterPublishedMedia: vi.fn() }));
vi.mock("./useChapterStudio", () => ({ useChapterStudio: (...args: unknown[]) => mockHooks.useChapterStudio(...args) }));
vi.mock("./useChapterPublishedMedia", () => ({ useChapterPublishedMedia: (...args: unknown[]) => mockHooks.useChapterPublishedMedia(...args) }));

afterEach(() => { cleanup(); vi.clearAllMocks(); });

const sourceCase: QualityReviewCase = {
  caseId: "case-1", externalCaseId: "algebra-page-214", packageId: "pkg-1", ordinal: 1, subject: "math", sourcePage: 214,
  sourceKind: "verified", sourceFingerprint: "source-fingerprint", inputFingerprint: "input-fingerprint", outputFingerprint: "output-fingerprint", recordVersion: 1,
  reviewStatus: "pending_human_review", sourceVerification: "human_quote_verified", assetVerification: "server_hash_verified", sourceVerifiedAt: 1, sourceVerifiedHash: "sha256:source",
  counted: false, createdBy: "teacher", createdByIdentityVerified: false,
  source: { sourceTitle: "Algebra", assetVerification: "server_hash_verified", contentSha256: "source", license: "CC BY 4.0", version: "2e" },
  sourceLocator: { quote: "An equation remains balanced." }, input: { question: "What is an equation?" }, expected: { acceptedAnswers: ["A statement of equality"] }, actual: { teacherObservation: "Check the wording against the page." },
  revisions: [], decisions: [],
};
const chapter = {
  teachingMode: "tutorial", chapterId: "chapter-1", subject: "math", title: "Linear equations", status: "in_review", version: 2, recordVersion: 4,
  sourceRevisions: [{ sourceRevisionId: "draft-source", sourceVersion: 2, pageStart: 1, pageEnd: 10, fingerprint: "draft-fingerprint", license: "CC BY 4.0", issues: [], pages: [] }],
  lessons: [{ lessonId: "draft-lesson", title: "Current draft lesson", version: 2, status: "in_review", sourceUploadId: "new-upload", knowledgePoints: [], blocks: [], sourceRevisionId: "draft-source", sourceLocator: { sourceRevisionId: "draft-source", page: 2, regions: [] }, reviewIssues: [] }],
  currentLessonIds: ["draft-lesson"], reviewIssues: [], publicationId: "pub-new",
  publications: [{ publicationId: "pub-old", version: 1, sourceRevisionId: "old-source", publishedAt: 10 }, { publicationId: "pub-new", version: 2, sourceRevisionId: "draft-source", publishedAt: 20 }],
} as never;
const publishedSnapshot: PublishedChapter = {
  chapterId: "chapter-1", publicationId: "pub-old", title: "Linear equations", version: 1, status: "published", teachingMode: "tutorial", subject: "math",
  lessons: [{ lessonId: "published-lesson", title: "Published equation explanation", version: 1, status: "published", sourceUploadId: "old-upload", knowledgePoints: [], blocks: [], sourceRevisionId: "old-source", sourceLocator: { sourceRevisionId: "old-source", page: 2, regions: [] }, reviewIssues: [] }],
};
const sourceItem = {
  ...sourceCase, sourceKey: "sha256:old-source", sourceTitle: "Algebra", uploadId: "old-upload", chapterIds: ["chapter-1"],
};
const sourceInbox: QualityReviewInbox = { items: [sourceItem], total: 1, pendingCount: 1, reviewedCount: 0, unlinkedCount: 0, mediaTasks: [], mediaPendingCount: 0 };
const approvedSourceCase: QualityReviewCase = {
  ...sourceCase, reviewStatus: "human_accepted", sourceVerification: "human_quote_verified",
  decisions: [{ reviewId: "decision-1", reviewedRecordVersion: 1, stage: "final", actorId: "protected:teacher", actorKind: "shared_teacher_account", identityVerified: false, displayName: "Teacher", decision: "accepted", rationale: "原页与引用一致。", verifiedQuote: "An equation remains balanced.", claimedQuote: "", createdAt: 30 }],
};
const video = {
  mediaId: "media-1", seriesId: "series-1", version: 1, chapterId: "chapter-1", publicationId: "pub-old", lessonId: "published-lesson", sourceRevisionId: "old-source",
  title: "Published equation explanation", subject: "math", license: "CC BY 4.0", sourceTitle: "Algebra", sourceUrl: "https://example.org/book", attributionText: "Access for free at openstax.org.",
  distributionScope: "local_only", sourceSnapshot: { storyboardTemplateVersion: "template-v1" }, rendererSnapshot: {},
  scenes: [{ sceneId: "scene-1", order: 1, kind: "concept", title: "Equation", narration: "An equation balances both sides.", sourceRefs: [{ sourceRevisionId: "old-source", page: 2, quote: "An equation remains balanced." }] }],
  status: "consistency_review_required", durationSeconds: 75, artifactSha256: "artifact", consistencyReview: { passed: true, issues: [], checks: { audio: true } },
  reviews: [], renderJob: null, createdAt: 1, publishedAt: null,
};

function json(value: unknown, status = 200) {
  return new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
}

it("user Given a case belongs to the current chapter but its source upload differs from the draft When the teacher accepts the case Then the old publication video stays selected and course and video approvals remain separate", async () => {
  mockHooks.useChapterStudio.mockReturnValue({
    chapter, loading: false, busy: "", error: "", notice: "", aiJob: null,
    reload: vi.fn(), generate: vi.fn(), startAiGeneration: vi.fn(), retryAiGeneration: vi.fn(), cancelAiGeneration: vi.fn(),
    revise: vi.fn(), edit: vi.fn(), review: vi.fn(), publish: vi.fn(),
  });
  mockHooks.useChapterPublishedMedia.mockReturnValue({ snapshot: publishedSnapshot, loading: false, error: "" });
  const requests: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    requests.push(`${init?.method ?? "GET"} ${url}`);
    if (url === "/api/library") return json({ items: [] });
    if (url.startsWith("/api/quality-reviews/inbox")) {
      // The frozen case upload differs from this chapter's current draft upload.
      // The server links by verified source hash and page, so chapterId alone is sufficient.
      return json(url.includes("uploadId=") ? { ...sourceInbox, items: [], total: 0 } : sourceInbox);
    }
    if (url.endsWith("/api/quality-reviews/cases/case-1/decisions")) return json(approvedSourceCase);
    if (url.endsWith("/api/quality-reviews/cases/case-1")) return json(sourceCase);
    if (url === "/api/lesson-media/lessons/chapter-1/pub-old/published-lesson") return json({ items: [video] });
    throw new Error(`Unexpected API request: ${url}`);
  }));
  render(<MemoryRouter initialEntries={["/studio/chapters/chapter-1?mediaReviewPublicationId=pub-old&mediaReviewLessonId=published-lesson&sourceReviewPackageId=pkg-1&sourceReviewCaseId=case-1"]}>
    <Routes><Route path="/studio/chapters/:chapterId" element={<ChapterStudioApp />} /></Routes>
  </MemoryRouter>);

  expect(await screen.findByRole("heading", { name: "algebra-page-214" })).toBeVisible();
  expect(screen.getByText("视频来源课程发布版本 1 · pub-old")).toBeVisible();
  expect(await screen.findByRole("heading", { name: "Published equation explanation" })).toBeVisible();
  expect(screen.getByRole("button", { name: "发布课程" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "批准此版本" })).toBeEnabled();
  expect(requests.some((request) => request.startsWith("GET /api/quality-reviews/inbox") && request.includes("uploadId="))).toBe(false);

  fireEvent.change(screen.getByLabelText("复核理由"), { target: { value: "原页与教材引用一致。" } });
  fireEvent.change(screen.getByLabelText("原文核对摘录（系统会与受管教材原页比对）"), { target: { value: "An equation remains balanced." } });
  fireEvent.click(screen.getByRole("button", { name: "提交最终单人决定" }));

  expect(await screen.findByText(/教材决定已保存/)).toBeVisible();
  expect(screen.getByRole("button", { name: "发布课程" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "批准此版本" })).toBeEnabled();
  expect(screen.getAllByText("consistency_review_required")).toHaveLength(2);
  await waitFor(() => expect(requests.some((request) => request.startsWith("POST /api/quality-reviews/cases/case-1/decisions"))).toBe(true));
});
