import type { MistakeConfirmation, MistakeItem } from "../types/mistake";
import { parse } from "./client";
import { currentLearnerId } from "./identity";

export async function importMistake(
  file: File,
  input: { sourceText?: string; originalAnswer?: string; learnerId?: string } = {},
): Promise<MistakeItem> {
  const body = new FormData();
  body.append("file", file);
  body.append("sourceText", input.sourceText ?? "");
  body.append("originalAnswer", input.originalAnswer ?? "");
  body.append("learnerId", input.learnerId ?? currentLearnerId());
  return parse<MistakeItem>(await fetch("/api/mistakes/import", { method: "POST", body }));
}

export async function loadMistakes(learnerId: string = currentLearnerId()): Promise<MistakeItem[]> {
  const result = await parse<{ items: MistakeItem[] }>(
    await fetch(`/api/mistakes?learnerId=${encodeURIComponent(learnerId)}`, { cache: "no-store" }),
  );
  return result.items;
}

export async function loadMistake(mistakeId: string): Promise<MistakeItem> {
  return parse<MistakeItem>(await fetch(`/api/mistakes/${mistakeId}`, { cache: "no-store" }));
}

export async function confirmMistake(mistakeId: string, input: MistakeConfirmation): Promise<MistakeItem> {
  return parse<MistakeItem>(await fetch(`/api/mistakes/${mistakeId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  }));
}

export async function archiveMistake(mistakeId: string): Promise<void> {
  await parse<MistakeItem>(await fetch(`/api/mistakes/${mistakeId}/archive`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ archived: true }),
  }));
}
