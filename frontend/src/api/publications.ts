import type {
  PublicationDetail,
  PublicationRevisionResult,
  PublicationStatus,
  PublicationSummary,
  PublicationWorkspaceState,
} from "../types/publication";
import type { LessonDocument } from "../types/lesson";
import { GeneratedSuccess, parse } from "./client";

type GeneratedPublicationRevisionResponse = GeneratedSuccess<"create_publication_revision_api_publications__publication_id__revisions_post">;

/**
 * 互动试卷发布 API。
 *
 * lesson 是单题不可变快照，publication 是 lesson ID 集合。重新审核创建新集合和新 lesson，
 * 因而旧版本的学生会话始终可以重放。
 */

export async function saveLesson(document: LessonDocument) {
  return parse(await fetch("/api/lessons", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(document),
  }));
}

export async function createPublication(input: {
  title: string;
  sourceUploadId?: string;
  lessonIds: string[];
}): Promise<PublicationSummary> {
  return parse<PublicationSummary>(await fetch("/api/publications", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  }));
}

export async function updatePublicationStatus(
  publicationId: string,
  status: PublicationStatus,
): Promise<PublicationSummary> {
  return parse<PublicationSummary>(await fetch(`/api/publications/${publicationId}/status`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ status }),
  }));
}

export async function createPublicationRevision(publicationId: string): Promise<PublicationRevisionResult> {
  return parse<PublicationRevisionResult & GeneratedPublicationRevisionResponse>(await fetch(`/api/publications/${publicationId}/revisions`, {
    method: "POST",
  }));
}

export async function loadPublicationWorkspace(sourceUploadId: string): Promise<PublicationWorkspaceState> {
  // 工作台需要未脱敏审核诊断；学生端只能调用 loadPublishedPublication 获取公开投影。
  return parse<PublicationWorkspaceState>(await fetch(
    `/api/publications/source/${encodeURIComponent(sourceUploadId)}`,
    { cache: "no-store" },
  ));
}

export async function loadPublishedPublications(): Promise<PublicationSummary[]> {
  const result = await parse<{ items: PublicationSummary[] }>(
    await fetch("/api/publications?status=published", { cache: "no-store" }),
  );
  return result.items;
}

export async function loadPublishedPublication(publicationId: string): Promise<PublicationDetail> {
  return parse<PublicationDetail>(await fetch(`/api/publications/${publicationId}`, { cache: "no-store" }));
}
