import { parse } from "./client";
import type { components, operations } from "../types/generated/api";

type JsonRequest<Operation extends keyof operations> = operations[Operation] extends { requestBody: { content: { "application/json": infer Body } } } ? Body : never;
type JsonResponse<Operation extends keyof operations, Status extends keyof (operations[Operation] extends { responses: infer Responses } ? Responses : never)> =
  operations[Operation] extends { responses: infer Responses }
    ? Status extends keyof Responses
      ? Responses[Status] extends { content: { "application/json": infer Body } } ? Body : never
      : never
    : never;

export type CoursewareProfile = components["schemas"]["CoursewareProfileView"];
export type CoursewarePlan = components["schemas"]["CoursewarePlan"];
export type CoursewarePlanCreate = JsonRequest<"create_plan_api_chapters__chapter_id__courseware_plan_post">;
export type CoursewarePlanEdit = JsonRequest<"edit_plan_api_chapters__chapter_id__courseware_plan_put">;
export type CoursewarePageEdit = JsonRequest<"edit_page_api_courseware__courseware_id__pages__page_id__put">;
export type CoursewareDocument = components["schemas"]["CoursewareDocument"];
export type CoursewarePublicDocument = components["schemas"]["CoursewarePublicDocument"];
export type CoursewarePresentation = components["schemas"]["CoursewarePresentation"];
export type CoursewareSourcePreparationInput = JsonRequest<"create_courses_from_upload_api_chapters_from_upload__upload_id__post">;

export async function listCoursewareProfiles(signal?: AbortSignal) {
  return parse<JsonResponse<"list_profiles_api_courseware_profiles_get", 200>>(
    await fetch("/api/courseware/profiles", { cache: "no-store", signal }),
  );
}

export async function prepareCoursewareSource(
  uploadId: string,
  input: CoursewareSourcePreparationInput,
  commandId = crypto.randomUUID(),
) {
  const response = await parse<JsonResponse<"create_courses_from_upload_api_chapters_from_upload__upload_id__post", 202>>(
    await fetch(`/api/chapters/from-upload/${encodeURIComponent(uploadId)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "Idempotency-Key": commandId },
      body: JSON.stringify(input),
    }),
  );
  return response;
}

export async function loadCoursewarePlan(chapterId: string, signal?: AbortSignal) {
  return parse<JsonResponse<"get_plan_api_chapters__chapter_id__courseware_plan_get", 200>>(
    await fetch(`/api/chapters/${encodeURIComponent(chapterId)}/courseware-plan`, { cache: "no-store", signal }),
  );
}

export async function createCoursewarePlan(chapterId: string, input: CoursewarePlanCreate, commandId = crypto.randomUUID()) {
  return parse<JsonResponse<"create_plan_api_chapters__chapter_id__courseware_plan_post", 202>>(
    await fetch(`/api/chapters/${encodeURIComponent(chapterId)}/courseware-plan`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "Idempotency-Key": commandId },
      body: JSON.stringify(input),
    }),
  );
}

export async function editCoursewarePlan(chapterId: string, input: CoursewarePlanEdit) {
  return parse<JsonResponse<"edit_plan_api_chapters__chapter_id__courseware_plan_put", 200>>(
    await fetch(`/api/chapters/${encodeURIComponent(chapterId)}/courseware-plan`, {
      method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(input),
    }),
  );
}

export async function approveCoursewarePlan(chapterId: string, input: JsonRequest<"approve_plan_api_chapters__chapter_id__courseware_plan_approve_post">) {
  return parse<JsonResponse<"approve_plan_api_chapters__chapter_id__courseware_plan_approve_post", 202>>(
    await fetch(`/api/chapters/${encodeURIComponent(chapterId)}/courseware-plan/approve`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(input),
    }),
  );
}

export async function listChapterCourseware(chapterId: string, signal?: AbortSignal) {
  return parse<JsonResponse<"list_chapter_documents_api_chapters__chapter_id__courseware_get", 200>>(
    await fetch(`/api/chapters/${encodeURIComponent(chapterId)}/courseware`, { cache: "no-store", signal }),
  );
}

export async function loadCourseware(coursewareId: string, signal?: AbortSignal) {
  return parse<JsonResponse<"get_document_api_courseware__courseware_id__get", 200>>(
    await fetch(`/api/courseware/${encodeURIComponent(coursewareId)}`, { cache: "no-store", signal }),
  );
}

export async function editCoursewarePage(coursewareId: string, pageId: string, input: CoursewarePageEdit) {
  return parse<JsonResponse<"edit_page_api_courseware__courseware_id__pages__page_id__put", 200>>(
    await fetch(`/api/courseware/${encodeURIComponent(coursewareId)}/pages/${encodeURIComponent(pageId)}`, {
      method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(input),
    }),
  );
}

export async function regenerateCoursewarePage(
  coursewareId: string,
  pageId: string,
  input: JsonRequest<"regenerate_page_api_courseware__courseware_id__pages__page_id__regenerate_post">,
) {
  return parse<JsonResponse<"regenerate_page_api_courseware__courseware_id__pages__page_id__regenerate_post", 202>>(
    await fetch(`/api/courseware/${encodeURIComponent(coursewareId)}/pages/${encodeURIComponent(pageId)}/regenerate`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(input),
    }),
  );
}

export async function reviewCourseware(coursewareId: string, input: JsonRequest<"review_document_api_courseware__courseware_id__review_post">) {
  return parse<JsonResponse<"review_document_api_courseware__courseware_id__review_post", 200>>(
    await fetch(`/api/courseware/${encodeURIComponent(coursewareId)}/review`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(input),
    }),
  );
}

export async function publishCourseware(coursewareId: string, input: JsonRequest<"publish_api_courseware__courseware_id__publish_post">) {
  return parse<JsonResponse<"publish_api_courseware__courseware_id__publish_post", 200>>(
    await fetch(`/api/courseware/${encodeURIComponent(coursewareId)}/publish`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(input),
    }),
  );
}

export async function loadPublishedCourseware(coursewareId: string, publicationId?: string, signal?: AbortSignal) {
  const query = publicationId ? `?publicationId=${encodeURIComponent(publicationId)}` : "";
  return parse<JsonResponse<"get_published_api_courseware__courseware_id__published_get", 200>>(
    await fetch(`/api/courseware/${encodeURIComponent(coursewareId)}/published${query}`, { cache: "no-store", signal }),
  );
}

export async function loadCoursewarePresentation(coursewareId: string, publicationId?: string, signal?: AbortSignal) {
  const query = publicationId ? `?publicationId=${encodeURIComponent(publicationId)}` : "";
  return parse<JsonResponse<"get_presentation_api_courseware__courseware_id__presentation_get", 200>>(
    await fetch(`/api/courseware/${encodeURIComponent(coursewareId)}/presentation${query}`, { cache: "no-store", signal }),
  );
}

export async function submitCoursewareAttempt(
  coursewareId: string,
  input: JsonRequest<"record_attempt_api_courseware__courseware_id__attempts_post">,
) {
  return parse<JsonResponse<"record_attempt_api_courseware__courseware_id__attempts_post", 200>>(
    await fetch(`/api/courseware/${encodeURIComponent(coursewareId)}/attempts`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(input),
    }),
  );
}

export async function deleteCoursewareDraft(coursewareId: string, expectedRecordVersion: number) {
  return parse<JsonResponse<"delete_draft_api_courseware__courseware_id__delete", 200>>(
    await fetch(`/api/courseware/${encodeURIComponent(coursewareId)}?expectedRecordVersion=${expectedRecordVersion}`, { method: "DELETE" }),
  );
}
