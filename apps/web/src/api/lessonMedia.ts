import { apiResourceUrl, parse } from "./client";

export type LessonMediaScene = {
  sceneId: string;
  order: number;
  kind: string;
  title: string;
  narration: string;
  sourceRefs: Array<{ sourceRevisionId: string; page: number; sentenceId?: string; quote: string }>;
};

export type LessonMediaReview = {
  reviewId: string;
  mediaId: string;
  mediaVersion: number;
  artifactSha256: string;
  actor: { actorId: string; actorKind: string; identityVerified: boolean; displayName?: string };
  decision: "approve" | "request_changes" | "publish";
  note: string;
  createdAt: number;
};

export type LessonMedia = {
  mediaId: string;
  seriesId: string;
  version: number;
  chapterId: string;
  publicationId: string;
  lessonId: string;
  sourceRevisionId: string;
  title: string;
  subject: "math" | "english";
  license: string;
  sourceTitle: string | null;
  sourceUrl: string | null;
  attributionText: string | null;
  distributionScope: "student" | "local_only";
  sourceSnapshot: Record<string, unknown>;
  rendererSnapshot: Record<string, unknown>;
  scenes: LessonMediaScene[];
  status: string;
  durationSeconds: number | null;
  artifactSha256: string | null;
  consistencyReview: { passed: boolean; issues: Array<{ code: string; message: string }>; checks: Record<string, boolean> } | null;
  reviews: LessonMediaReview[];
  renderJob: { jobId: string; status: string; progress: number; message: string; attemptCount: number; maxAttempts: number; lastError?: string | null } | null;
  createdAt: number;
  publishedAt: number | null;
};

export type PublishedLessonMedia = {
  mediaId: string;
  version: number;
  title: string;
  subject: "math" | "english";
  sourceRevisionId: string;
  sourceTitle: string | null;
  sourceUrl: string | null;
  attributionText: string | null;
  durationSeconds: number;
  videoUrl: string;
  captionsUrl: string;
  captionsDownloadUrl: string;
};

export async function listPublishedLessonMedia(
  chapterId: string,
  publicationId: string,
  lessonId: string,
  signal?: AbortSignal,
): Promise<{ chapterId: string; publicationId: string; lessonId: string; sourceRevisionId: string; items: PublishedLessonMedia[] }> {
  const params = new URLSearchParams({ publicationId, lessonId });
  return parse(await fetch(`/api/chapters/${encodeURIComponent(chapterId)}/media/published?${params}`, { cache: "no-store", signal }));
}

export async function listLessonMedia(chapterId: string, publicationId: string, lessonId: string, signal?: AbortSignal): Promise<LessonMedia[]> {
  const path = [chapterId, publicationId, lessonId].map(encodeURIComponent).join("/");
  const response = await parse<{ items?: LessonMedia[] }>(await fetch(`/api/lesson-media/lessons/${path}`, { cache: "no-store", signal }));
  return Array.isArray(response.items) ? response.items : [];
}

export async function createLessonMediaDraft(input: {
  chapterId: string; publicationId: string; lessonId: string; sourceRevisionId: string;
}): Promise<LessonMedia> {
  return parse(await fetch("/api/lesson-media/drafts", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(input),
  }));
}

export async function createLessonMediaRevision(mediaId: string, expectedVersion: number, sceneEdits: Array<{ sceneId: string; narration: string }>): Promise<LessonMedia> {
  return parse(await fetch(`/api/lesson-media/${encodeURIComponent(mediaId)}/revisions`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ expectedVersion, sceneEdits }),
  }));
}

export async function enqueueLessonMediaRender(mediaId: string, expectedVersion: number): Promise<LessonMedia["renderJob"]> {
  return parse(await fetch(`/api/lesson-media/${encodeURIComponent(mediaId)}/render`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ expectedVersion }),
  }));
}

export async function cancelLessonMediaRender(mediaId: string): Promise<void> {
  await parse(await fetch(`/api/lesson-media/${encodeURIComponent(mediaId)}/render`, { method: "DELETE" }));
}

export async function reviewLessonMedia(mediaId: string, decision: "approve" | "request_changes", note: string): Promise<LessonMedia> {
  return parse(await fetch(`/api/lesson-media/${encodeURIComponent(mediaId)}/review`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ decision, note }),
  }));
}

export async function publishLessonMedia(mediaId: string): Promise<LessonMedia> {
  return parse(await fetch(`/api/lesson-media/${encodeURIComponent(mediaId)}/publish`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({}),
  }));
}

export function lessonMediaTeacherAssetUrl(mediaId: string, kind: "asset" | "captions" | "captions/download"): string {
  return apiResourceUrl(`/api/lesson-media/${encodeURIComponent(mediaId)}/${kind}`);
}

export function lessonMediaPublicAssetUrl(path: string): string {
  return apiResourceUrl(path);
}
