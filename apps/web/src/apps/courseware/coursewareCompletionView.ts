import type { CoursewareCompletion } from "../../api/courseware";

/** `supplement` keeps the approved plan's lessons as a retained batch instead of replacing them. */
export interface CompletionRange { pageStart: number; pageEnd: number; supplement: boolean }

/** Compress consecutive source pages for reading; batching remains exclusively a server responsibility. */
export function sourcePageLabel(pages: readonly number[]): string {
  const sorted = [...new Set(pages)].sort((left, right) => left - right);
  const ranges: string[] = [];
  for (let index = 0; index < sorted.length; index += 1) {
    const start = sorted[index];
    let end = start;
    while (index + 1 < sorted.length && sorted[index + 1] === end + 1) { index += 1; end = sorted[index]; }
    ranges.push(start === end ? String(start) : `${start}–${end}`);
  }
  return ranges.join("、");
}

/**
 * A read-only suggestion cannot replace an existing plan or cross a source/version boundary.
 * With an approved plan it may only start a supplement, which the server also re-validates.
 */
export function completionState(report: CoursewareCompletion, context: { chapterId: string; sourceRevisionId?: string; expectedRecordVersion?: number; planStatus?: string }) {
  const sourceChanged = report.chapterId !== context.chapterId || !context.sourceRevisionId || report.sourceRevisionId !== context.sourceRevisionId;
  const versionChanged = context.expectedRecordVersion !== undefined && report.expectedRecordVersion !== context.expectedRecordVersion;
  const hasPlan = Boolean(context.planStatus || report.existingPlan);
  const supplement = hasPlan && report.supplementsExistingPlan && (context.planStatus ?? report.existingPlan?.status) === "approved";
  return {
    sourceChanged,
    versionChanged,
    hasPlan,
    supplement,
    canSelect: !sourceChanged && !versionChanged && (!hasPlan || supplement) && report.canCreatePlan && report.action === "create_plan",
    planStatus: context.planStatus ?? report.existingPlan?.status,
    planStale: report.existingPlan?.stale ?? false,
  };
}
