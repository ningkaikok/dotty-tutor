// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { LectureChecklistPanel } from "./LectureChecklistPanel";

describe("LectureChecklistPanel", () => {
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

  it("shows common mistakes, students, reasons and teacher overturns", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({
      ok: true,
      json: async () => ({
        classId: "class-1",
        assignmentId: "assignment-1",
        limit: 5,
        commonMistakes: [{
          questionId: "q-1",
          questionOrder: 0,
          title: "解方程",
          prompt: "…",
          involvedStudentCount: 2,
          attemptedStudentCount: 3,
          errorRate: 0.6667,
          errorReasons: [
            { reason: "unknown", count: 1, rate: 0.5 },
            { reason: "calculation", count: 1, rate: 0.5 },
          ],
          students: [
            { learnerId: "a", displayName: "小安", assessment: "incorrect", originalAssessment: "incorrect", reviewStatus: "unreviewed", correctedAssessment: null, attributionSource: "unknown", mistakeEvidenceRef: null, errorReason: "unknown", evidenceRefs: ["attempt:a"] },
            { learnerId: "b", displayName: "小北", assessment: "correct", originalAssessment: "incorrect", reviewStatus: "overturned", correctedAssessment: "correct", attributionSource: "ai", mistakeEvidenceRef: "mistake:m-1:b", errorReason: "calculation", evidenceRefs: ["attempt:b", "review:r", "mistake:m-1:b"] },
          ],
          evidenceRefs: ["attempt:a", "attempt:b", "review:r"],
        }],
        items: [],
        errorReasonDistribution: [{ reason: "unknown", count: 1 }],
        evidenceRefs: [],
      }),
    })));
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });

    render(<LectureChecklistPanel classId="class-1" assignmentId="assignment-1" />);

    expect(await screen.findByRole("heading", { name: "老师讲评清单" })).toBeVisible();
    expect(screen.getByText("解方程")).toBeVisible();
    expect(screen.getByText("小安 · 错")).toBeVisible();
    expect(screen.getByText("教师改判为对")).toBeVisible();
    expect(screen.getByText(/未知来源\s*·\s*完全不会/)).toBeVisible();
    expect(screen.getByText(/AI 归因\s*·\s*计算失误/)).toBeVisible();
    expect(screen.getByText("mistake:m-1:b")).toBeVisible();
    const copyButton = screen.getByRole("button", { name: "复制证据引用 mistake:m-1:b" });
    expect(copyButton).toBeVisible();
    fireEvent.click(copyButton);
    await waitFor(() => expect(writeText).toHaveBeenCalledWith("mistake:m-1:b"));
  });
});
