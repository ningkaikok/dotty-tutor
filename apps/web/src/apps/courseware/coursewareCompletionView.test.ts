import { expect, it } from "vitest";
import { completionState, sourcePageLabel } from "./coursewareCompletionView";
import type { CoursewareCompletion } from "../../api/courseware";
const report: CoursewareCompletion = {
  chapterId: "chapter", sourceRevisionId: "source", expectedRecordVersion: 2, maxPagesPerBatch: 5,
  batches: [{ pageStart: 3, pageEnd: 4, sourcePages: [3, 4] }], blockedPages: [], alreadyGeneratedPages: [1, 2], alreadyPlannedPages: [],
  pendingGenerationPages: [], pendingReviewPages: [2], pendingPublicationPages: [2], existingPlan: null, canCreatePlan: true, supplementsExistingPlan: false, action: "create_plan", requiresHumanReview: true,
};
it("formats ordered distinct source pages as consecutive ranges without changing the input", () => {
  const input = [8, 4, 3, 4, 1, 9];
  expect(sourcePageLabel(input)).toBe("1、3–4、8–9");
  expect(input).toEqual([8, 4, 3, 4, 1, 9]);
  expect(sourcePageLabel([])).toBe("");
});
it("requires the same source and chapter version before a suggestion can fill a plan", () => {
  const context = { chapterId: "chapter", sourceRevisionId: "source", expectedRecordVersion: 2 };
  expect(completionState(report, context).canSelect).toBe(true);
  expect(completionState(report, { ...context, expectedRecordVersion: 3 }).canSelect).toBe(false);
  expect(completionState(report, { ...context, sourceRevisionId: "old" }).canSelect).toBe(false);
  expect(completionState(report, { ...context, chapterId: "other" }).canSelect).toBe(false);
  expect(completionState({ ...report, canCreatePlan: false }, context).canSelect).toBe(false);
});
it("never replaces a locally loaded plan or a server-side stale plan", () => {
  const context = { chapterId: "chapter", sourceRevisionId: "source", expectedRecordVersion: 2 };
  expect(completionState(report, { ...context, planStatus: "approved" }).canSelect).toBe(false);
  const existingPlan = { sourceRevisionId: "old", planVersion: 1, recordVersion: 1, status: "approved" as const, generationStatus: "completed" as const, pageStart: 1, pageEnd: 2, stale: true };
  const state = completionState({ ...report, existingPlan }, context);
  expect(state.canSelect).toBe(false);
  expect(state.planStale).toBe(true);
});
it("offers only a supplement when the server retains a finished, approved plan", () => {
  const context = { chapterId: "chapter", sourceRevisionId: "source", expectedRecordVersion: 2 };
  const existingPlan = { sourceRevisionId: "source", planVersion: 1, recordVersion: 3, status: "approved" as const, generationStatus: "completed" as const, pageStart: 1, pageEnd: 2, stale: false };
  const supplementable = { ...report, existingPlan, supplementsExistingPlan: true };
  const approved = completionState(supplementable, { ...context, planStatus: "approved" });
  expect(approved.canSelect).toBe(true);
  expect(approved.supplement).toBe(true);
  // A locally edited plan awaiting confirmation must not be discarded by a supplement
  expect(completionState(supplementable, { ...context, planStatus: "awaiting_confirmation" }).canSelect).toBe(false);
});
