// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, expect, it, vi } from "vitest";
import type { QualityReviewCase } from "../../api/qualityReviews";
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
const packageRecord = { packageId: "package-1", title: "OpenStax source review pilot", sourceKind: "real_open_material", source: { counted: false }, createdAt: 1, cases: 1, pending: 1, reviewed: 0 };
const secondCase: QualityReviewCase = {
  ...reviewCase,
  caseId: "case-2",
  externalCaseId: "math-p215-example-2-3",
  ordinal: 2,
};
const secondPackage = { ...packageRecord, packageId: "package-2", title: "Second source package" };

function fixtureResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), { status, headers: { "Content-Type": "application/json" } });
}

it("user Given an imported OpenStax case When opening its detail Then provenance, expected and actual are visible with an original-page gate", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (url) => {
    const path = String(url);
    if (path.endsWith("/api/quality-review-runs/models")) return fixtureResponse({ providers: [] });
    if (path.endsWith("/api/quality-review-runs")) return fixtureResponse({ items: [] });
    if (path.endsWith("/api/quality-reviews/packages")) return fixtureResponse({ items: [packageRecord] });
    if (path.includes("/cases") && path.includes("/packages/")) return fixtureResponse({ package: packageRecord, items: [reviewCase] });
    if (path.endsWith("/cases/case-1")) return fixtureResponse(reviewCase);
    throw new Error(`Unexpected API request: ${path}`);
  });

  render(<MemoryRouter><QualityReviewApp /></MemoryRouter>);

  expect(await screen.findByRole("heading", { name: "math-p214-example-2-2" })).toBeVisible();
  expect(screen.getByText("Elementary Algebra 2e")).toBeVisible();
  expect(screen.getByText("math · PDF 第 214 页")).toBeVisible();
  expect(screen.getByText("PDF 页码")).toBeVisible();
  expect(screen.getByText("CC BY 4.0")).toBeVisible();
  expect(screen.getByText(/原教材 PDF 尚未连接/)).toBeVisible();
  expect(screen.getByRole("heading", { name: "预期结果" })).toBeVisible();
  expect(screen.getByText("应识别题号：EXAMPLE 2.2")).toBeVisible();
  expect(screen.getByText("识别到的题号：13")).toBeVisible();
  expect(screen.getByText("逐页核对题号、题体边界、公式负号、分数和条件。")).toBeVisible();
});

it("user Given the source page is not registered When saving a positive opinion Then the UI records only a provisional decision", async () => {
  const requests: Array<{ url: string; init?: RequestInit }> = [];
  let storedCase = reviewCase;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (url, init) => {
    const path = String(url);
    requests.push({ url: path, init });
    if (path.endsWith("/api/quality-review-runs/models")) return fixtureResponse({ providers: [] });
    if (path.endsWith("/api/quality-review-runs")) return fixtureResponse({ items: [] });
    if (init?.method === "POST") {
      storedCase = { ...reviewCase, recordVersion: 1, reviewStatus: "decision_recorded_pending_source", decisions: [{ reviewId: "review-1", reviewedRecordVersion: 1, stage: "provisional", actorId: "demo:unverified", actorKind: "demo_unverified", identityVerified: false, displayName: "", decision: "accepted", rationale: "原页未显示前的临时意见", verifiedQuote: "", claimedQuote: "", createdAt: 2 }] };
      return fixtureResponse(storedCase);
    }
    if (path.endsWith("/api/quality-reviews/packages")) return fixtureResponse({ items: [{ ...packageRecord, pending: 1 }] });
    if (path.includes("/cases") && path.includes("/packages/")) return fixtureResponse({ package: packageRecord, items: [storedCase] });
    if (path.endsWith("/cases/case-1")) return fixtureResponse(storedCase);
    throw new Error(`Unexpected API request: ${path}`);
  });

  render(<MemoryRouter><QualityReviewApp /></MemoryRouter>);
  await screen.findByRole("heading", { name: "math-p214-example-2-2" });
  fireEvent.change(screen.getByLabelText("复核理由"), { target: { value: "原页未显示前的临时意见" } });
  fireEvent.click(screen.getByRole("button", { name: "保存待核验意见" }));

  expect(await screen.findByText("意见已保存为待核验记录。连接并核验原教材 PDF 后，才能提交最终教材决定。" )).toBeVisible();
  await waitFor(() => expect(requests.some((item) => item.init?.method === "POST")).toBe(true));
  const post = requests.find((item) => item.init?.method === "POST");
  expect(JSON.parse(String(post?.init?.body))).toMatchObject({ expectedRecordVersion: 1, decision: "accepted", rationale: "原页未显示前的临时意见", displayName: "" });
  expect(screen.getByText("有临时意见 · 待原页核验")).toBeVisible();
});

it("user Given a server-hash-verified source and real OCR output When opening a case Then the original page and distinct OCR and text-layer evidence are visible", async () => {
  const verifiedCase: QualityReviewCase = {
    ...reviewCase,
    reviewStatus: "pending_human_review",
    sourceVerification: "managed_asset_verified",
    assetVerification: "server_hash_verified",
    source: { ...reviewCase.source, assetVerification: "server_hash_verified" },
    input: {
      question: "逐页核对题号、题体边界、公式负号、分数和条件。",
      realOcrObservation: {
        metadata: { sourceKind: "render_derived_scan", elapsedMs: 21666, cost: null, runtime: { provider: "mineru" } },
        recognizedText: "−1 3 \\frac{7}{3} = −1 3 6",
        engineeringFinding: "OCR识别与原页数学式不一致，待用户复核。",
      },
    },
  };
  vi.spyOn(globalThis, "fetch").mockImplementation(async (url) => {
    const path = String(url);
    if (path.endsWith("/api/quality-review-runs/models")) return fixtureResponse({ providers: [] });
    if (path.endsWith("/api/quality-review-runs")) return fixtureResponse({ items: [] });
    if (path.endsWith("/api/quality-reviews/packages")) return fixtureResponse({ items: [packageRecord] });
    if (path.includes("/cases") && path.includes("/packages/")) return fixtureResponse({ package: packageRecord, items: [verifiedCase] });
    if (path.endsWith("/cases/case-1")) return fixtureResponse(verifiedCase);
    throw new Error(`Unexpected API request: ${path}`);
  });

  render(<MemoryRouter><QualityReviewApp /></MemoryRouter>);

  expect(await screen.findByRole("heading", { name: "math-p214-example-2-2" })).toBeVisible();
  expect(screen.getByRole("img", { name: "教材 PDF 第 214 页" })).toHaveAttribute(
    "src", "/api/quality-reviews/cases/case-1/source-page",
  );
  expect(screen.getAllByText("原页已校验 · 待人工复核").length).toBeGreaterThan(0);
  expect(screen.getByText(/真实 OCR 执行记录（待人工核对）/)).toBeVisible();
  expect(screen.getByText("服务商：mineru", { exact: false })).toBeVisible();
  expect(screen.getAllByText(/OCR识别与原页数学式不一致/).length).toBeGreaterThan(0);
  expect(screen.getByText("本栏是 PDF 文字层题号回放；独立 OCR 执行记录列在案例输入中。")).toBeVisible();
  expect(screen.getByRole("heading", { name: "提交你的最终单人教材决定" })).toBeVisible();
  expect(screen.getByText("PDF 页码")).toBeVisible();
});

it("user Given a replay uses the English evaluator When viewing its actual observation Then the interface shows a readable rule-replay label", async () => {
  const englishCase: QualityReviewCase = {
    ...reviewCase,
    subject: "english",
    externalCaseId: "english-p123-evidence-check",
    input: { question: "What does the sentence imply?", passage: "A source sentence." },
    expected: { acceptedAnswers: ["It suggests a change."], evidenceQuote: "A source sentence." },
    actual: {
      method: "domain.chapters.english.evaluate_english_chapter_attempt",
      productionEvaluation: { assessment: "needs_review", evidenceVerdict: "unsupported" },
    },
  };
  vi.spyOn(globalThis, "fetch").mockImplementation(async (url) => {
    const path = String(url);
    if (path.endsWith("/api/quality-review-runs/models")) return fixtureResponse({ providers: [] });
    if (path.endsWith("/api/quality-review-runs")) return fixtureResponse({ items: [] });
    if (path.endsWith("/api/quality-reviews/packages")) return fixtureResponse({ items: [packageRecord] });
    if (path.includes("/cases") && path.includes("/packages/")) return fixtureResponse({ package: packageRecord, items: [englishCase] });
    if (path.endsWith("/cases/case-1")) return fixtureResponse(englishCase);
    throw new Error(`Unexpected API request: ${path}`);
  });

  render(<MemoryRouter><QualityReviewApp /></MemoryRouter>);

  expect(await screen.findByRole("heading", { name: "english-p123-evidence-check" })).toBeVisible();
  expect(screen.getByText("执行方式：英语答案与证据规则回放")).toBeVisible();
});

it("user Given a package case list is still loading When a newer package is selected Then the older response cannot replace the selected package", async () => {
  let releaseOldPackage: ((response: Response) => void) | undefined;
  let firstPackageCaseLoads = 0;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (url) => {
    const path = String(url);
    if (path.endsWith("/api/quality-review-runs/models")) return fixtureResponse({ providers: [] });
    if (path.endsWith("/api/quality-review-runs")) return fixtureResponse({ items: [] });
    if (path.endsWith("/api/quality-reviews/packages")) return fixtureResponse({ items: [packageRecord, secondPackage] });
    if (path.endsWith("/packages/package-1/cases")) {
      firstPackageCaseLoads += 1;
      if (firstPackageCaseLoads === 2) return new Promise<Response>((resolve) => { releaseOldPackage = resolve; });
      return fixtureResponse({ package: packageRecord, items: [reviewCase] });
    }
    if (path.endsWith("/packages/package-2/cases")) return fixtureResponse({ package: secondPackage, items: [secondCase] });
    if (path.endsWith("/cases/case-1")) return fixtureResponse(reviewCase);
    if (path.endsWith("/cases/case-2")) return fixtureResponse(secondCase);
    throw new Error(`Unexpected API request: ${path}`);
  });

  render(<MemoryRouter><QualityReviewApp /></MemoryRouter>);
  expect(await screen.findByRole("heading", { name: "math-p214-example-2-2" })).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: /OpenStax source review pilot/ }));
  fireEvent.click(screen.getByRole("button", { name: /Second source package/ }));
  expect(await screen.findByRole("heading", { name: "math-p215-example-2-3" })).toBeVisible();
  await act(async () => {
    releaseOldPackage?.(fixtureResponse({ package: packageRecord, items: [reviewCase] }));
    await Promise.resolve();
    await Promise.resolve();
  });
  expect(screen.getByRole("button", { name: /Second source package/ })).toHaveAttribute("aria-current", "true");
});

it("user Given a case detail is still loading When another case is selected Then the old detail and draft cannot return or be submitted", async () => {
  let releaseOldCase: ((response: Response) => void) | undefined;
  let caseOneLoads = 0;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (url) => {
    const path = String(url);
    if (path.endsWith("/api/quality-review-runs/models")) return fixtureResponse({ providers: [] });
    if (path.endsWith("/api/quality-review-runs")) return fixtureResponse({ items: [] });
    if (path.endsWith("/api/quality-reviews/packages")) return fixtureResponse({ items: [packageRecord] });
    if (path.endsWith("/packages/package-1/cases")) return fixtureResponse({ package: packageRecord, items: [reviewCase, secondCase] });
    if (path.endsWith("/cases/case-1")) {
      caseOneLoads += 1;
      if (caseOneLoads === 2) return new Promise<Response>((resolve) => { releaseOldCase = resolve; });
      return fixtureResponse(reviewCase);
    }
    if (path.endsWith("/cases/case-2")) return fixtureResponse(secondCase);
    throw new Error(`Unexpected API request: ${path}`);
  });

  render(<MemoryRouter><QualityReviewApp /></MemoryRouter>);
  expect(await screen.findByRole("heading", { name: "math-p214-example-2-2" })).toBeVisible();
  fireEvent.change(screen.getByLabelText("复核理由"), { target: { value: "旧案例草稿" } });
  fireEvent.click(screen.getByRole("button", { name: /math-p214-example-2-2/ }));
  fireEvent.click(screen.getByRole("button", { name: /math-p215-example-2-3/ }));
  expect(await screen.findByRole("heading", { name: "math-p215-example-2-3" })).toBeVisible();
  await act(async () => {
    releaseOldCase?.(fixtureResponse(reviewCase));
    await Promise.resolve();
    await Promise.resolve();
  });
  expect(screen.getByLabelText("复核理由")).toHaveValue("");
  expect(screen.getByRole("button", { name: "保存待核验意见" })).toBeDisabled();
});
