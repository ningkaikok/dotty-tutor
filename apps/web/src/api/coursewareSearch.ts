import { parse } from "./client";
import type { components } from "../types/generated/api";

export type CoursewareSourceEvidence = components["schemas"]["CoursewareSourceEvidence"];
export type CoursewareSourceExplanation = components["schemas"]["CoursewareSourceExplanation"];

const endpoint = (coursewareId: string) => `/api/courseware/${encodeURIComponent(coursewareId)}/source-search`;

/** Every query is scoped to the immutable publication currently displayed. */
export async function searchCoursewareSource(coursewareId: string, publicationId: string, query: string, signal?: AbortSignal): Promise<{ items: CoursewareSourceEvidence[] }> {
  const params = new URLSearchParams({ publicationId, q: query });
  return parse(await fetch(`${endpoint(coursewareId)}?${params}`, { cache: "no-store", signal }));
}

export async function explainCoursewareSource(coursewareId: string, publicationId: string, question: string, signal?: AbortSignal): Promise<CoursewareSourceExplanation> {
  return parse(await fetch(`${endpoint(coursewareId)}/explain`, { method: "POST", signal, headers: { "Content-Type": "application/json" }, body: JSON.stringify({ publicationId, question }) }));
}
