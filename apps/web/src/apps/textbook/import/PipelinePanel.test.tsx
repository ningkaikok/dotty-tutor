// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, expect, it, vi } from "vitest";
import type { TextbookImportResult } from "../../../types/textbook";
import { PipelinePanel } from "./PipelinePanel";

afterEach(cleanup);
it("user Given a textbook source without generated exam questions When upload completes Then automatic courses are offered without exam metrics", () => {
  const result: TextbookImportResult = { uploadId: "book", importId: "book-import", filename: "English.pdf", contentType: "application/pdf", size: 100, stored: true,
    materialKind: "textbook", detectionReason: "章节标题", stages: [],
    ocrRun: { requestedProvider: "pypdf", provider: "pypdf", mode: "text", fallback: false, output: "Unit 1 Hello" },
    extraction: { chapter: "教材课程", knowledgePoint: "自动识别章节", questionCount: 0, formulaCount: 0, guideCardCount: 0, pageCount: 20, confidence: 0, mode: "source-only" } };
  render(<MemoryRouter><PipelinePanel result={result} pdfMode phase="done" processingTask={null} activeStage={0} activeFilename="English.pdf" onContinue={vi.fn()} /></MemoryRouter>);
  expect(screen.getByRole("link", { name: "自动制作课程（最多前 5 章） →" })).toHaveAttribute("href", "/studio/chapters/new?uploadId=book");
  expect(screen.queryByText("实际生成")).not.toBeInTheDocument();
  expect(screen.queryByText("道题")).not.toBeInTheDocument();
  expect(screen.getByText(/自动识别：教材/)).toBeVisible();
});
