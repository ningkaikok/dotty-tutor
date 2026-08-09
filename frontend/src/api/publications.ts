import type { PublicationDetail, PublicationStatus, PublicationSummary } from "../types/publication";
import type { LessonDocument } from "../types/lesson";
import { parse } from "./client";

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

export async function loadPublishedPublications(): Promise<PublicationSummary[]> {
  const result = await parse<{ items: PublicationSummary[] }>(
    await fetch("/api/publications?status=published", { cache: "no-store" }),
  );
  return result.items;
}

export async function loadPublishedPublication(publicationId: string): Promise<PublicationDetail> {
  return parse<PublicationDetail>(await fetch(`/api/publications/${publicationId}`, { cache: "no-store" }));
}
