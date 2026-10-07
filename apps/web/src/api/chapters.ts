import { parse } from "./client";
import { currentLearnerId } from "./identity";
import type { components, operations } from "../types/generated/api";
import type { CanvasAction } from "../types/question";
import type { LessonBlock } from "../types/lesson";
import type { LessonSourceRef } from "../types/lesson";
import type { BackgroundJob } from "../types/textbook";
import type {
  ChapterAttemptInput,
  ChapterAttemptResult,
  ChapterAttemptReviewInput,
  ChapterAuthorQuestion,
  ChapterEvidenceOption,
  ChapterEvidenceRef,
  ChapterLessonEditInput,
  ChapterManagement,
  ChapterSource,
  ChapterSubject,
  ChapterSummary,
  PublishedChapter,
} from "../types/chapter";

type JsonRequest<Operation extends keyof operations> = operations[Operation] extends { requestBody: { content: { "application/json": infer Body } } } ? Body : never;
type JsonResponse<Operation extends keyof operations, Status extends keyof (operations[Operation] extends { responses: infer Responses } ? Responses : never)> =
  operations[Operation] extends { responses: infer Responses }
    ? Status extends keyof Responses
      ? Responses[Status] extends { content: { "application/json": infer Body } } ? Body : never
      : never
    : never;

type CreateRequest = JsonRequest<"create_chapter_api_chapters_post">;
type RevisionRequest = JsonRequest<"revise_chapter_api_chapters__chapter_id__revisions_post">;
type EditRequest = JsonRequest<"edit_lesson_api_chapters__chapter_id__lessons__lesson_id__put">;
type LessonReviewRequest = JsonRequest<"review_lesson_api_chapters__chapter_id__lessons__lesson_id__review_patch">;
type AttemptRequest = JsonRequest<"record_chapter_attempt_api_chapters__chapter_id__attempts_post">;
type ApiManagement = components["schemas"]["ChapterResponse"];
type ApiPublicChapter = components["schemas"]["ChapterPublishedResponse"];
type ApiAttempt = components["schemas"]["ChapterAttemptResponse"];

type ApiChapterPageWithPreview = components["schemas"]["ChapterPage"] & { previewUrl?: unknown };

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function requiredString(value: unknown, description: string): string {
  if (typeof value !== "string" || !value.trim()) throw new Error(`章节服务返回了无效的${description}`);
  return value;
}

function isCanvasAction(value: unknown): value is CanvasAction {
  return value === "show-base" || value === "show-point-p" || value === "show-triangles" || value === "show-bisector";
}

function sourceRefsFromPayload(payload: Record<string, unknown>): { sourceRefs?: LessonSourceRef[] } {
  if (!Array.isArray(payload.sourceRefs)) return {};
  const sourceRefs: LessonSourceRef[] = payload.sourceRefs.flatMap((item) => {
    try {
      const reference = evidenceRefFromJson(item);
      return [{ sourceRevisionId: reference.sourceRevisionId, page: reference.page,
        ...(reference.regionId ? { regionId: reference.regionId } : {}),
        ...(reference.sentenceId ? { sentenceId: reference.sentenceId } : {}),
        ...(reference.quote ? { quote: reference.quote } : {}) }];
    } catch { return []; }
  });
  return sourceRefs.length ? { sourceRefs } : {};
}

function normalizeLessonBlocks(blocks: components["schemas"]["LessonBlock"][]): LessonBlock[] {
  return blocks.map((block) => {
    const id = requiredString(block.id, "课程步骤编号");
    const title = requiredString(block.title, "课程步骤标题");
    const payload = isRecord(block.payload) ? block.payload : {};
    const stringField = (name: string) => requiredString(payload[name], `课程步骤${name}`);
    switch (block.type) {
      case "markdown": return { id, title, type: "markdown", payload: { markdown: stringField("markdown"), ...sourceRefsFromPayload(payload) } };
      case "formula": return { id, title, type: "formula", payload: { latex: stringField("latex"), ...sourceRefsFromPayload(payload) } };
      case "annotation": return { id, title, type: "annotation", payload: { text: stringField("text"), ...sourceRefsFromPayload(payload) } };
      case "quiz": return { id, title, type: "quiz", payload: { questionId: stringField("questionId"), ...sourceRefsFromPayload(payload) } };
      case "hint": {
        if (typeof payload.level !== "number") throw new Error("章节服务返回了无效的提示级别");
        return { id, title, type: "hint", payload: { level: payload.level, hint: stringField("hint"), ...(typeof payload.question === "string" ? { question: payload.question } : {}), ...sourceRefsFromPayload(payload) } };
      }
      case "animation": return {
        id, title, type: "animation",
        payload: {
          src: stringField("src"),
          ...(typeof payload.poster === "string" ? { poster: payload.poster } : {}),
          ...(typeof payload.caption === "string" ? { caption: payload.caption } : {}),
          ...sourceRefsFromPayload(payload),
        },
      };
      case "diagram": {
        if (!isCanvasAction(payload.action)) throw new Error("章节服务返回了不支持的图形操作");
        return { id, title, type: "diagram", payload: { renderer: "geometry", action: payload.action, text: stringField("text"), speechText: typeof payload.speechText === "string" ? payload.speechText : stringField("text"), ...sourceRefsFromPayload(payload) } };
      }
    }
  });
}

function normalizeQuestionType(value: string): import("../types/question").QuestionType {
  const types = ["choice", "multi-select", "true-false", "short-answer", "fill-blank", "numeric", "draw-line"] as const;
  const questionType = types.find((type) => type === value);
  if (!questionType) throw new Error("章节服务返回了不支持的题型");
  return questionType;
}

function evidenceRef(reference: components["schemas"]["ChapterEvidenceRef"]): ChapterEvidenceRef {
  return {
    sourceRevisionId: requiredString(reference.sourceRevisionId, "依据来源修订编号"),
    page: reference.page,
    ...(reference.regionId ? { regionId: reference.regionId } : {}),
    ...(reference.sentenceId ? { sentenceId: reference.sentenceId } : {}),
    ...(reference.quote ? { quote: reference.quote } : {}),
  };
}

function evidenceRefFromJson(value: unknown): ChapterEvidenceRef {
  if (!isRecord(value) || typeof value.sourceRevisionId !== "string" || typeof value.page !== "number") {
    throw new Error("章节服务返回了无效的原文依据");
  }
  return {
    sourceRevisionId: value.sourceRevisionId,
    page: value.page,
    ...(typeof value.regionId === "string" ? { regionId: value.regionId } : {}),
    ...(typeof value.sentenceId === "string" ? { sentenceId: value.sentenceId } : {}),
    ...(typeof value.quote === "string" ? { quote: value.quote } : {}),
  };
}

function questionKind(value: unknown): ChapterAuthorQuestion["questionKind"] {
  const values = ["word_meaning", "reference", "explicit", "inference", "short_answer"] as const;
  return values.find((item) => item === value);
}

function questionTypeFields(question: components["schemas"]["ChapterAuthorQuestion"] | components["schemas"]["ChapterPublicQuestion"]) {
  return {
    id: requiredString(question.id, "题目编号"),
    prompt: requiredString(question.prompt, "题目内容"),
    questionType: normalizeQuestionType(question.questionType),
    ...(typeof question.knowledgePoint === "string" ? { knowledgePoint: question.knowledgePoint } : {}),
  };
}

function normalizeManagement(response: ApiManagement): ChapterManagement {
  const statuses = ["draft", "needs_review", "in_review", "published"] as const;
  const chapterStatus = statuses.find((status) => status === response.status);
  if (!chapterStatus) throw new Error("章节服务返回了未知章节状态");
  const lessons = response.lessons.map((lesson) => {
    if (!lesson.sourceRevisionId || !lesson.sourceLocator) throw new Error("章节课程缺少来源页定位，无法进入复核");
    const statusesForLesson = ["draft", "in_review", "approved", "needs_review", "published"] as const;
    if (!statusesForLesson.some((status) => status === lesson.status)) throw new Error("章节服务返回了未知课程状态");
    const question = lesson.questionPayload.question;
    if (!question) {
      if (response.teachingMode !== "tutorial") throw new Error("练习课程缺少检查题");
      const lessonStatus = statusesForLesson.find((status) => status === lesson.status)!;
      return { lessonId: lesson.lessonId, title: lesson.title, version: lesson.version, status: lessonStatus,
        knowledgePoints: lesson.knowledgePoints ?? [], blocks: normalizeLessonBlocks(lesson.blocks),
        sourceRevisionId: lesson.sourceRevisionId, sourceLocator: { ...lesson.sourceLocator, regions: lesson.sourceLocator.regions ?? [] },
        evidenceOptions: lesson.evidenceOptions ?? [], reviewIssues: lesson.reviewIssues ?? [] };
    }
    const questionReview = question as typeof question & { teacherVariants?: string[]; variantReviewStatus?: string };
    const normalizedQuestionKind = questionKind(question.questionKind);
    const answerMode: ChapterAuthorQuestion["answerMode"] = question.answerMode === "objective" || question.answerMode === "short_answer" ? question.answerMode : undefined;
    const authorQuestion = {
      ...questionTypeFields(question),
      subject: response.subject,
      ...(Array.isArray(question.correctAnswers) ? { correctAnswers: question.correctAnswers } : {}),
      ...(typeof question.answerSpec?.expected === "string" ? { answerSpec: { expected: question.answerSpec.expected } } : {}),
      ...(normalizedQuestionKind ? { questionKind: normalizedQuestionKind } : {}),
      ...(answerMode ? { answerMode } : {}),
      ...(Array.isArray(question.acceptedAnswers) ? { acceptedAnswers: question.acceptedAnswers } : {}),
      ...(Array.isArray(questionReview.teacherVariants) ? { teacherVariants: questionReview.teacherVariants } : {}),
      ...(typeof questionReview.variantReviewStatus === "string" ? { variantReviewStatus: questionReview.variantReviewStatus } : {}),
      ...(Array.isArray(question.requiredEvidenceRefs) ? { requiredEvidenceRefs: question.requiredEvidenceRefs } : {}),
      ...(isRecord(question.rubric) ? { rubric: question.rubric } : {}),
    };
    const options: ChapterEvidenceOption[] = lesson.evidenceOptions?.map((option) => ({
      sourceRevisionId: requiredString(option.sourceRevisionId, "候选依据来源修订编号"),
      page: option.page,
      ...(typeof option.regionId === "string" ? { regionId: option.regionId } : {}),
      ...(typeof option.sentenceId === "string" ? { sentenceId: option.sentenceId } : {}),
      ...(typeof option.quote === "string" ? { quote: option.quote } : {}),
      label: requiredString(option.label, "候选依据说明"),
    })) ?? question.requiredEvidenceRefs?.map((reference) => ({ ...evidenceRef(reference), label: reference.quote || `第 ${reference.page} 页原文区域` })) ?? [];
    const lessonStatus = statusesForLesson.find((status) => status === lesson.status);
    if (!lessonStatus) throw new Error("章节服务返回了未知课程状态");
    return {
      lessonId: lesson.lessonId,
      title: lesson.title,
      version: lesson.version,
      status: lessonStatus,
      knowledgePoints: lesson.knowledgePoints ?? (question.knowledgePoint ? [question.knowledgePoint] : []),
      blocks: normalizeLessonBlocks(lesson.blocks),
      questionPayload: { question: authorQuestion },
      sourceRevisionId: lesson.sourceRevisionId,
      sourceLocator: { ...lesson.sourceLocator, regions: lesson.sourceLocator.regions ?? [] },
      evidenceOptions: options,
      reviewIssues: lesson.reviewIssues ?? [],
    };
  });
  return {
    chapterId: response.chapterId,
    subject: response.subject,
    teachingMode: response.teachingMode ?? "practice",
    title: response.title,
    status: chapterStatus,
    version: response.version,
    recordVersion: response.recordVersion,
    sourceRevisions: response.sourceRevisions.map((revision) => ({
      ...revision,
      pages: revision.pages.map((page) => {
        const previewUrl = (page as ApiChapterPageWithPreview).previewUrl;
        return { ...page, previewUrl: typeof previewUrl === "string" && previewUrl.startsWith("/api/chapters/") ? previewUrl : null };
      }),
    })),
    lessons,
    currentLessonIds: response.currentLessonIds,
    reviewIssues: response.reviewIssues,
    publicationId: response.publicationId ?? null,
    publications: response.publications,
    generationJobId: typeof (response as ApiManagement & { generationJobId?: unknown }).generationJobId === "string"
      ? (response as ApiManagement & { generationJobId: string }).generationJobId : null,
  };
}

function normalizePublishedChapter(response: ApiPublicChapter): PublishedChapter {
  if (response.status !== "published") throw new Error("学生端只能打开已发布课程");
  const lessons = response.lessons.map((lesson) => {
    if (!lesson.sourceRevisionId || !lesson.sourceLocator) throw new Error("已发布课程缺少来源页定位");
    const question = lesson.questionPayload.question;
    return {
      lessonId: lesson.lessonId,
      title: lesson.title,
      version: lesson.version,
      status: "published" as const,
      knowledgePoints: lesson.knowledgePoints,
      blocks: normalizeLessonBlocks(lesson.blocks),
      sourceRevisionId: lesson.sourceRevisionId,
      sourceLocator: { ...lesson.sourceLocator, regions: lesson.sourceLocator.regions ?? [] },
      evidenceOptions: lesson.evidenceOptions ?? [],
      reviewIssues: [],
      ...(question ? { questionPayload: { question: questionTypeFields(question) } } : {}),
    };
  });
  return {
    chapterId: response.chapterId,
    subject: response.subject,
    teachingMode: response.teachingMode ?? "practice",
    title: response.title,
    publicationId: response.publicationId,
    version: response.version,
    status: "published",
    lessons,
  };
}

function normalizeAttempt(response: ApiAttempt): ChapterAttemptResult {
  if (!isRecord(response.feedback) || typeof response.feedback.message !== "string") throw new Error("章节服务返回了无效的作答反馈");
  const reviews = (response.reviews ?? []).flatMap((review) => {
    if (!isRecord(review) || typeof review.reviewer !== "string" || (review.decision !== "correct" && review.decision !== "incorrect") || typeof review.note !== "string") return [];
    const decision: "correct" | "incorrect" = review.decision;
    return [{ reviewer: review.reviewer, decision, note: review.note, ...(typeof review.createdAt === "number" ? { createdAt: review.createdAt } : {}) }];
  });
  return {
    attemptId: response.attemptId,
    publicationId: response.publicationId,
    lessonId: response.lessonId,
    learnerId: response.learnerId,
    answer: response.answer ?? {},
    evidenceRefs: (response.evidenceRefs ?? []).map(evidenceRefFromJson),
    assessment: response.assessment,
    evidenceVerdict: response.evidenceVerdict,
    feedback: { message: response.feedback.message, ...(isRecord(response.feedback.evidence) ? { evidence: response.feedback.evidence } : {}) },
    reviews,
  };
}

export async function listChapters(): Promise<ChapterSummary[]> {
  const response: JsonResponse<"list_chapters_api_chapters_get", 200> = await parse(await fetch("/api/chapters", { cache: "no-store" }));
  return response.items;
}

export async function createChapter(request: { subject: ChapterSubject; title: string; source: ChapterSource }): Promise<ChapterManagement> {
  const body: CreateRequest = { ...request, teachingMode: "practice" };
  const response: JsonResponse<"create_chapter_api_chapters_post", 201> = await parse(
    await fetch("/api/chapters", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }),
  );
  return normalizeManagement(response);
}

export async function loadChapter(chapterId: string, signal?: AbortSignal): Promise<ChapterManagement> {
  const response: JsonResponse<"get_chapter_api_chapters__chapter_id__get", 200> = await parse(
    await fetch(`/api/chapters/${encodeURIComponent(chapterId)}`, { cache: "no-store", signal }),
  );
  return normalizeManagement(response);
}

export async function reviseChapter(chapterId: string, source: ChapterSource, expectedRecordVersion: number): Promise<ChapterManagement> {
  const body: RevisionRequest = { source, expectedRecordVersion };
  const response: JsonResponse<"revise_chapter_api_chapters__chapter_id__revisions_post", 200> = await parse(
    await fetch(`/api/chapters/${encodeURIComponent(chapterId)}/revisions`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }),
  );
  return normalizeManagement(response);
}

export async function generateChapter(chapterId: string): Promise<ChapterManagement> {
  const response: JsonResponse<"generate_chapter_api_chapters__chapter_id__generate_post", 200> = await parse(
    await fetch(`/api/chapters/${encodeURIComponent(chapterId)}/generate`, { method: "POST" }),
  );
  return normalizeManagement(response);
}

/** Starts a source-bound AI draft job using the chapter's current record version. */
export async function generateChapterAi(chapterId: string, expectedRecordVersion: number): Promise<BackgroundJob> {
  return parse<BackgroundJob>(await fetch(`/api/chapters/${encodeURIComponent(chapterId)}/generate-ai`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ expectedRecordVersion }),
  }));
}

export async function loadChapterAiJob(jobId: string): Promise<BackgroundJob> {
  return parse<BackgroundJob>(await fetch(`/api/jobs/${encodeURIComponent(jobId)}`, { cache: "no-store" }));
}

export async function retryChapterAiJob(jobId: string): Promise<BackgroundJob> {
  return parse<BackgroundJob>(await fetch(`/api/jobs/${encodeURIComponent(jobId)}/retry`, { method: "POST" }));
}

export async function cancelChapterAiJob(jobId: string): Promise<BackgroundJob> {
  return parse<BackgroundJob>(await fetch(`/api/jobs/${encodeURIComponent(jobId)}/cancel`, { method: "POST" }));
}

export async function editChapterLesson(chapterId: string, lessonId: string, request: ChapterLessonEditInput, expectedRecordVersion: number): Promise<ChapterManagement> {
  const body: EditRequest = { ...request, expectedRecordVersion };
  const response: JsonResponse<"edit_lesson_api_chapters__chapter_id__lessons__lesson_id__put", 200> = await parse(
    await fetch(`/api/chapters/${encodeURIComponent(chapterId)}/lessons/${encodeURIComponent(lessonId)}`, {
      method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    }),
  );
  return normalizeManagement(response);
}

export async function reviewChapterLesson(chapterId: string, lessonId: string, request: Omit<LessonReviewRequest, "expectedRecordVersion">, expectedRecordVersion: number): Promise<ChapterManagement> {
  const body: LessonReviewRequest = { ...request, expectedRecordVersion };
  const response: JsonResponse<"review_lesson_api_chapters__chapter_id__lessons__lesson_id__review_patch", 200> = await parse(
    await fetch(`/api/chapters/${encodeURIComponent(chapterId)}/lessons/${encodeURIComponent(lessonId)}/review`, {
      method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    }),
  );
  return normalizeManagement(response);
}

export async function publishChapter(chapterId: string) {
  const response: JsonResponse<"publish_chapter_api_chapters__chapter_id__publish_post", 201> = await parse(
    await fetch(`/api/chapters/${encodeURIComponent(chapterId)}/publish`, { method: "POST" }),
  );
  return response;
}

export async function loadPublishedChapter(chapterId: string, publicationId?: string, signal?: AbortSignal): Promise<PublishedChapter> {
  const query = publicationId ? `?publicationId=${encodeURIComponent(publicationId)}` : "";
  const response: JsonResponse<"get_published_chapter_api_chapters__chapter_id__published_get", 200> = await parse(
    await fetch(`/api/chapters/${encodeURIComponent(chapterId)}/published${query}`, { cache: "no-store", signal }),
  );
  return normalizePublishedChapter(response);
}

export async function submitChapterAttempt(chapterId: string, request: ChapterAttemptInput): Promise<ChapterAttemptResult> {
  const body: AttemptRequest = {
    attemptId: request.attemptId,
    lessonId: request.lessonId,
    questionId: request.questionId,
    learnerId: request.learnerId || currentLearnerId(),
    publicationId: request.publicationId,
    answer: request.answer,
    evidenceRefs: request.evidenceRefs.map((reference) => ({
      sourceRevisionId: reference.sourceRevisionId,
      page: reference.page,
      ...(reference.regionId ? { regionId: reference.regionId } : {}),
      ...(reference.sentenceId ? { sentenceId: reference.sentenceId } : {}),
      ...(reference.quote ? { quote: reference.quote } : {}),
    })),
  };
  const response: JsonResponse<"record_chapter_attempt_api_chapters__chapter_id__attempts_post", 200> = await parse(
    await fetch(`/api/chapters/${encodeURIComponent(chapterId)}/attempts`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }),
  );
  return normalizeAttempt(response);
}

export async function loadChapterAttempt(chapterId: string, attemptId: string): Promise<ChapterAttemptResult> {
  const response: JsonResponse<"get_chapter_attempt_api_chapters__chapter_id__attempts__attempt_id__get", 200> = await parse(await fetch(
    `/api/chapters/${encodeURIComponent(chapterId)}/attempts/${encodeURIComponent(attemptId)}`, { cache: "no-store" },
  ));
  return normalizeAttempt(response);
}

export async function loadChapterAttemptForReview(chapterId: string, attemptId: string): Promise<ChapterAttemptResult> {
  const response: JsonResponse<"get_chapter_attempt_for_review_api_chapters__chapter_id__review_attempts__attempt_id__get", 200> = await parse(await fetch(
    `/api/chapters/${encodeURIComponent(chapterId)}/review-attempts/${encodeURIComponent(attemptId)}`, { cache: "no-store" },
  ));
  return normalizeAttempt(response);
}

export async function reviewChapterAttempt(chapterId: string, attemptId: string, request: ChapterAttemptReviewInput): Promise<ChapterAttemptResult> {
  const response: JsonResponse<"review_chapter_attempt_api_chapters__chapter_id__attempts__attempt_id__review_patch", 200> = await parse(await fetch(
    `/api/chapters/${encodeURIComponent(chapterId)}/attempts/${encodeURIComponent(attemptId)}/review`,
    { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(request) },
  ));
  return normalizeAttempt(response);
}

/** Remove a course from the studio without deleting its source or history. */
export async function deleteChapter(chapterId: string): Promise<void> {
  await parse(await fetch(`/api/chapters/${encodeURIComponent(chapterId)}`, { method: "DELETE" }));
}
