import { parse } from "./client";
import type { components } from "../types/generated/api";

/** Advisory AI pre-review: these calls never approve pages or decide source cases. */
export type CoursewareAiReviewStatus = components["schemas"]["CoursewareAiReviewStatus"];
export type CoursewareAiReviewStart = components["schemas"]["CoursewareAiReviewStart"];
export type AiPageReview = components["schemas"]["AiPageReview"];
export type SourceCaseAiOpinion = components["schemas"]["SourceCaseAiOpinion"];

const json = (body: unknown): RequestInit => ({ method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

export async function loadCoursewareAiReview(coursewareId: string, signal?: AbortSignal): Promise<CoursewareAiReviewStatus> {
  return parse(await fetch(`/api/courseware/${encodeURIComponent(coursewareId)}/ai-review`, { cache: "no-store", signal }));
}

export async function startCoursewareAiReview(coursewareId: string, expectedRecordVersion: number): Promise<CoursewareAiReviewStart> {
  return parse(await fetch(`/api/courseware/${encodeURIComponent(coursewareId)}/ai-review`, json({ expectedRecordVersion })));
}

export async function loadSourceCaseAiOpinion(caseId: string, signal?: AbortSignal): Promise<SourceCaseAiOpinion> {
  return parse(await fetch(`/api/quality-review/cases/${encodeURIComponent(caseId)}/ai-opinion`, { cache: "no-store", signal }));
}

export async function requestSourceCaseAiOpinion(caseId: string, expectedRecordVersion: number): Promise<SourceCaseAiOpinion> {
  return parse(await fetch(`/api/quality-review/cases/${encodeURIComponent(caseId)}/ai-opinion`, json({ expectedRecordVersion })));
}
